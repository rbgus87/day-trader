"""strategy/momentum_strategy.py — 모멘텀 브레이크아웃 (F-STR-03).

전일 고점 돌파 + 거래량 필터 + VWAP 방향 필터.
v1 진입 로직 + v2 필터(거래량 1.5배 완화, VWAP 필터).
"""

import pandas as pd
from loguru import logger

from config.settings import TradingConfig
from strategy.base_strategy import BaseStrategy, Signal


class MomentumStrategy(BaseStrategy):
    """전일 고점 돌파 + 거래량 확인 + VWAP 필터 후 매수."""

    def __init__(self, config: TradingConfig) -> None:
        super().__init__()
        self._config = config
        self._prev_day_high: float = 0.0
        self._prev_day_volume: int = 0
        self._prev_day_candles: "pd.DataFrame | None" = None  # 시간대별 거래량 비율용
        # Phase 2 Day 6: ATR 손절 컨텍스트
        self._ticker: str = ""
        self._last_signal_date: str = ""  # YYYY-MM-DD
        # 5분 요약 로그용 단계별 평가 카운터. engine_worker가 주기적으로
        # 합산해 [SIGNAL-SUMMARY]를 출력하고 reset_diag_counters()로 리셋.
        self.diag_counters: dict[str, int] = self._make_diag_counters()
        self.configure_multi_trade(
            max_trades=config.max_trades_per_day,
            cooldown_minutes=config.cooldown_minutes,
        )

    @staticmethod
    def _make_diag_counters() -> dict[str, int]:
        return {
            "prev_day_missing": 0,
            "breakout_fail": 0,
            "breakout_pass": 0,
            "no_candle": 0,
            "breakout_surge_fail": 0,
            "volume_fail": 0,
            "breakout_last_fail": 0,
            "adx_no_bars": 0,
            "adx_fail": 0,
            "adx_pass": 0,
            "rvol_fail": 0,
            "vwap_fail": 0,
            "signal_emit": 0,
        }

    def reset_diag_counters(self) -> None:
        """5분 요약 로그 출력 직후 호출되어 카운터를 0으로 초기화."""
        for k in self.diag_counters:
            self.diag_counters[k] = 0

    def set_prev_day_data(self, high: float, volume: int) -> None:
        """전일 고가·거래량 기준값 저장."""
        self._prev_day_high = high
        self._prev_day_volume = volume

    def set_prev_day_candles(self, candles: "pd.DataFrame | None") -> None:
        """전일 분봉 데이터 주입 — 시간대별 거래량 비율 계산용.
        backtester._setup_strategy_day에서 호출. ts 컬럼은 datetime64 보장.
        """
        if candles is not None and not candles.empty:
            self._prev_day_candles = candles.copy()
            if "ts" in self._prev_day_candles.columns:
                self._prev_day_candles["ts"] = pd.to_datetime(self._prev_day_candles["ts"])
        else:
            self._prev_day_candles = None

    def set_ticker(self, ticker: str) -> None:
        """ATR 조회용 종목 코드 주입 (backtester/engine_worker에서 호출)."""
        self._ticker = ticker

    def _check_buy_time_limit(self) -> bool:
        """Phase 3 Day 11.5: 매수 허용 시간 초과 여부. True면 차단.

        `buy_time_end` (예: "11:30") 이후 신호를 차단. 백테스트는 주입된
        `_backtest_time`, 실시간은 `datetime.now().time()` 기준.
        """
        if not getattr(self._config, "buy_time_limit_enabled", False):
            return False
        import re
        from datetime import datetime, time as _time
        m = re.match(r"(\d+):(\d+)", str(self._config.buy_time_end))
        if not m:
            return False
        limit = _time(int(m.group(1)), int(m.group(2)))
        now = self._backtest_time if self._backtest_time else datetime.now().time()
        return now >= limit

    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        """매수 신호 생성."""
        if not self.can_trade():
            return None

        # Phase 3 Day 11.5: 오전 매수 제한
        if self._check_buy_time_limit():
            return None

        current_price: float = tick["price"]

        if self._prev_day_high <= 0:
            self.diag_counters["prev_day_missing"] += 1
            return None

        # 1) 가격 돌파 확인 (ADR-016: 최소 돌파폭 적용)
        min_bp = getattr(self._config, "min_breakout_pct", 0.0)
        breakout_pct = (current_price - self._prev_day_high) / self._prev_day_high
        if breakout_pct < min_bp:
            self.diag_counters["breakout_fail"] += 1
            logger.debug(
                f"[BREAKOUT] 미달: {tick['ticker']} {breakout_pct:.2%} < {min_bp:.2%}"
            )
            return None
        self.diag_counters["breakout_pass"] += 1

        # 2) 거래량 필터
        if candles is None or candles.empty:
            self.diag_counters["no_candle"] += 1
            logger.debug(f"[VOLUME] 캔들 없음: {tick['ticker']}")
            return None

        cum_volume: float = candles["volume"].sum()

        # 2a) 돌파 캔들 거래량 서지 — 직전 5분 평균의 N배 이상이어야 유효
        if getattr(self._config, "breakout_volume_surge_enabled", False) and len(candles) >= 6:
            last_vol = float(candles.iloc[-1]["volume"])
            prev5_avg = float(candles.iloc[-6:-1]["volume"].mean())
            surge_req = getattr(self._config, "breakout_volume_surge_ratio", 2.0)
            if prev5_avg > 0 and last_vol < prev5_avg * surge_req:
                self.diag_counters["breakout_surge_fail"] += 1
                logger.debug(
                    f"[SURGE] 미달: {tick['ticker']} "
                    f"last={last_vol:.0f} < prev5×{surge_req}={prev5_avg * surge_req:.0f}"
                )
                return None

        # 2b) 거래량 기준: 시간대별(time-based) 또는 전일 전체 대비 fallback
        if (
            getattr(self._config, "volume_by_time_enabled", False)
            and self._prev_day_candles is not None
            and not self._prev_day_candles.empty
        ):
            from datetime import datetime as _dt
            current_time = (
                self._backtest_time if self._backtest_time else _dt.now().time()
            )
            prev_up_to_now = self._prev_day_candles[
                self._prev_day_candles["ts"].dt.time <= current_time
            ]
            if not prev_up_to_now.empty:
                prev_vol_up_to_now = int(prev_up_to_now["volume"].sum())
                required_volume: float = prev_vol_up_to_now * getattr(
                    self._config, "volume_by_time_ratio", 1.5
                )
            else:
                required_volume = self._prev_day_volume * self._config.momentum_volume_ratio
        else:
            required_volume = self._prev_day_volume * self._config.momentum_volume_ratio

        if cum_volume < required_volume:
            self.diag_counters["volume_fail"] += 1
            logger.debug(
                f"[VOLUME] 미달: {tick['ticker']} "
                f"cum={cum_volume:,.0f} < req={required_volume:,.0f}"
            )
            return None

        # 3) 마지막 캔들 종가 돌파 재확정 (ADR-016: 최소 돌파폭 적용)
        last_close = candles.iloc[-1]["close"]
        last_breakout_pct = (last_close - self._prev_day_high) / self._prev_day_high
        if last_breakout_pct < min_bp:
            self.diag_counters["breakout_last_fail"] += 1
            logger.debug(
                f"[BREAKOUT_LAST] 미달: {tick['ticker']} "
                f"{last_breakout_pct:.2%} < {min_bp:.2%}"
            )
            return None

        # 4) ADX 추세 필터 (카운팅은 _check_adx 내부)
        if self._config.adx_enabled and not self._check_adx(candles, tick["ticker"]):
            return None

        # 5) RVol 거래량 급증 필터
        if self._config.rvol_enabled and not self._check_rvol(candles):
            self.diag_counters["rvol_fail"] += 1
            logger.debug(f"[RVOL] 탈락: {tick['ticker']}")
            return None

        # 6) VWAP 매수 우위 필터
        if self._config.vwap_enabled and not self._check_vwap(candles, current_price):
            self.diag_counters["vwap_fail"] += 1
            logger.debug(f"[VWAP] 탈락: {tick['ticker']} price={current_price}")
            return None

        # ATR 손절 계산을 위한 신호 발생 날짜 캡처 (캔들의 마지막 ts 기준)
        try:
            if candles is not None and not candles.empty and "ts" in candles.columns:
                self._last_signal_date = pd.to_datetime(
                    candles["ts"].iloc[-1]
                ).strftime("%Y-%m-%d")
        except Exception:
            pass  # 폴백은 get_stop_loss에서 처리

        self.diag_counters["signal_emit"] += 1
        logger.info(
            f"모멘텀 매수 신호: {tick['ticker']} price={current_price} "
            f"prev_high={self._prev_day_high} cum_vol={cum_volume:,.0f}"
        )

        return Signal(
            ticker=tick["ticker"],
            side="buy",
            price=current_price,
            strategy="momentum",
            reason=f"전일 고점({self._prev_day_high:,.0f}) 돌파 + 거래량 {self._config.momentum_volume_ratio:.1f}배 확인",
        )

    def _check_adx(self, candles: pd.DataFrame, ticker: str = "") -> bool:
        """ADX 추세 강도 필터. 캔들 부족 또는 계산 실패 시 False.

        진입 후보(BREAKOUT + 거래량 통과) 단계에서만 호출되므로 진단 로그를
        debug 레벨로 남겨도 스팸이 되지 않는다.
        """
        min_candles = self._config.adx_length + 20
        if len(candles) < min_candles:
            self.diag_counters["adx_no_bars"] += 1
            logger.debug(
                f"[ADX] 봉 부족: {ticker} {len(candles)} < {min_candles}"
            )
            return False
        try:
            from core.indicators import wilder_adx
            df = candles.tail(min_candles)
            adx_result = wilder_adx(df["high"], df["low"], df["close"], length=self._config.adx_length)
            if adx_result is None or adx_result.empty:
                self.diag_counters["adx_fail"] += 1
                logger.debug(f"[ADX] 결과 비어있음: {ticker}")
                return False
            adx_col = f"ADX_{self._config.adx_length}"
            if adx_col not in adx_result.columns:
                self.diag_counters["adx_fail"] += 1
                logger.debug(f"[ADX] 컬럼 없음: {ticker} cols={list(adx_result.columns)}")
                return False
            current_adx = adx_result[adx_col].iloc[-1]
            if pd.isna(current_adx):
                self.diag_counters["adx_fail"] += 1
                logger.debug(f"[ADX] NaN: {ticker}")
                return False
            if current_adx < self._config.adx_min:
                self.diag_counters["adx_fail"] += 1
                logger.debug(
                    f"[ADX] 미달: {ticker} {current_adx:.1f} < {self._config.adx_min}"
                )
                return False
            self.diag_counters["adx_pass"] += 1
            logger.debug(
                f"[ADX] 통과: {ticker} {current_adx:.1f} >= {self._config.adx_min}"
            )
            return True
        except Exception as e:
            self.diag_counters["adx_fail"] += 1
            logger.warning(f"ADX 계산 실패 ({ticker}): {e}")
            return False

    def _check_rvol(self, candles: pd.DataFrame) -> bool:
        """RVol 필터 — 직전 N분봉 거래량이 ��일 평균의 rvol_min배 이상."""
        window = self._config.rvol_window
        if len(candles) < window + 10:
            return False
        try:
            recent_vol = candles["volume"].iloc[-window:].sum()
            avg_vol = candles["volume"].iloc[:-window].mean()
            if avg_vol <= 0:
                return False
            rvol = recent_vol / (avg_vol * window)
            return rvol >= self._config.rvol_min
        except Exception as e:
            logger.warning(f"RVol 계산 실패: {e}")
            return False

    def _check_vwap(self, candles: pd.DataFrame, current_price: float) -> bool:
        """VWAP 필터 — 현��가가 당일 VWAP 이상이어야 진입."""
        if len(candles) < 10:
            return False
        try:
            tp = (candles["high"] + candles["low"] + candles["close"]) / 3
            vol = candles["volume"]
            vwap_den = vol.sum()
            if vwap_den <= 0:
                return False
            vwap = (tp * vol).sum() / vwap_den
            threshold = vwap * (1 + self._config.vwap_min_above)
            return current_price >= threshold
        except Exception as e:
            logger.warning(f"VWAP 계산 실패: {e}")
            return False

    def get_stop_loss(self, entry_price: float) -> float:
        """손절가 계산.

        Phase 2 Day 6: atr_stop_enabled면 ticker_atr 캐시에서 조회한 ATR%로
        종목별 동적 손절을 계산. 실패 시 고정 -3% 폴백.
        """
        fallback = entry_price * (1 + self._config.momentum_stop_loss_pct)
        if not getattr(self._config, "atr_stop_enabled", False):
            return fallback
        if not self._ticker:
            return fallback
        try:
            from core.indicators import calculate_atr_stop_loss, get_latest_atr

            atr_pct = get_latest_atr(
                "daytrader.db", self._ticker, self._last_signal_date or None
            )
            if atr_pct is None:
                return fallback
            return calculate_atr_stop_loss(
                entry_price=entry_price,
                atr_pct=atr_pct,
                multiplier=self._config.atr_stop_multiplier,
                min_pct=self._config.atr_stop_min_pct,
                max_pct=self._config.atr_stop_max_pct,
            )
        except Exception as e:
            logger.warning(f"ATR 손절 계산 실패 ({self._ticker}): {e}")
            return fallback

    def get_take_profit(self, entry_price: float) -> float:
        """TP1 계산.

        Phase 2 Day 7: atr_tp_enabled면 ticker_atr 기반 동적 TP1,
        실패/비활성 시 고정 tp1_pct 폴백. 2차 목표는 트레일링 스톱으로 관리.
        """
        fallback = entry_price * (1 + self._config.tp1_pct)
        if not getattr(self._config, "atr_tp_enabled", False) or not self._ticker:
            return fallback
        try:
            from core.indicators import calculate_atr_tp1, get_latest_atr

            atr_pct = get_latest_atr(
                "daytrader.db", self._ticker, self._last_signal_date or None
            )
            if atr_pct is None:
                return fallback
            return calculate_atr_tp1(
                entry_price=entry_price,
                atr_pct=atr_pct,
                multiplier=self._config.atr_tp_multiplier,
                min_pct=self._config.atr_tp_min_pct,
                max_pct=self._config.atr_tp_max_pct,
            )
        except Exception as e:
            logger.warning(f"ATR TP1 계산 실패 ({self._ticker}): {e}")
            return fallback

    def reset(self) -> None:
        """일별 리셋 (기준값은 유지)."""
        super().reset()
