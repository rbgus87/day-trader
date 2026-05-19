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
        self._prev_day_close: float = 0.0   # 갭업 기준가 계산용
        self._prev_day_candles: "pd.DataFrame | None" = None  # 시간대별 거래량 비율용
        # Phase 2 Day 6: ATR 손절 컨텍스트
        self._ticker: str = ""
        self._last_signal_date: str = ""  # YYYY-MM-DD
        self._last_adx: float = 0.0        # 스코어링용 마지막 ADX 값
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
            "entry_too_high": 0,
            "afternoon_bp_fail": 0,
            "afternoon_vol_fail": 0,
            "afternoon_adx_fail": 0,
        }

    def reset_diag_counters(self) -> None:
        """5분 요약 로그 출력 직후 호출되어 카운터를 0으로 초기화."""
        for k in self.diag_counters:
            self.diag_counters[k] = 0

    def set_prev_day_data(self, high: float, volume: int, close: float = 0.0) -> None:
        """전일 고가·거래량·종가 기준값 저장."""
        self._prev_day_high = high
        self._prev_day_volume = volume
        self._prev_day_close = close

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

        afternoon_entry_enabled=True이면 buy_time_end~afternoon_end 구간은 허용
        (강화 조건은 generate_signal 내에서 별도 적용).
        afternoon_entry_enabled=False이면 buy_time_end 이후 전면 차단.
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
        if now < limit:
            return False  # 오전 → 허용
        # now >= buy_time_end (12:00 이후)
        if not getattr(self._config, "afternoon_entry_enabled", False):
            return True  # 오후 비활성 → 차단
        # 오후 진입 활성: afternoon_end 확인
        m2 = re.match(r"(\d+):(\d+)", str(getattr(self._config, "afternoon_end", "14:00")))
        if not m2:
            return True
        end_time = _time(int(m2.group(1)), int(m2.group(2)))
        return now >= end_time  # afternoon_end 이후 차단

    def _is_afternoon_window(self) -> bool:
        """buy_time_end ~ afternoon_end 사이 여부 (오후 강화 조건 적용 구간)."""
        if not getattr(self._config, "afternoon_entry_enabled", False):
            return False
        import re
        from datetime import datetime, time as _time
        m1 = re.match(r"(\d+):(\d+)", str(self._config.buy_time_end))
        if not m1:
            return False
        start = _time(int(m1.group(1)), int(m1.group(2)))
        m2 = re.match(r"(\d+):(\d+)", str(getattr(self._config, "afternoon_end", "14:00")))
        if not m2:
            return False
        end = _time(int(m2.group(1)), int(m2.group(2)))
        now = self._backtest_time if self._backtest_time else datetime.now().time()
        return start <= now < end

    def generate_signal(
        self,
        candles: pd.DataFrame,
        tick: dict,
        *,
        breakout_price: float | None = None,
    ) -> Signal | None:
        """매수 신호 생성.

        breakout_price: 틱 레벨 돌파 감지 시점 가격.
            설정 시 현재가와의 괴리가 max_entry_above_breakout_pct 초과하면 차단.
        """
        if not self.can_trade():
            return None

        # Phase 3 Day 11.5: 오전 매수 제한
        if self._check_buy_time_limit():
            return None

        current_price: float = tick["price"]

        if self._prev_day_high <= 0:
            self.diag_counters["prev_day_missing"] += 1
            return None

        # 오후 강화 조건 구간 여부 (12:00~afternoon_end)
        _aft = self._is_afternoon_window()

        # 1) 가격 돌파 확인 (ADR-016: 최소 돌파폭 적용)
        min_bp = getattr(self._config, "min_breakout_pct", 0.0)
        if _aft:
            aft_bp = getattr(self._config, "afternoon_min_breakout_pct", 0.05)
            if aft_bp > min_bp:
                min_bp = aft_bp  # 오후 구간 임계값 상향

        # 갭업 기준가 조정: 당일 시가가 전일 종가 대비 N% 이상 갭업이면 시가를 기준가로
        breakout_ref = self._prev_day_high
        if (
            getattr(self._config, "gap_breakout_adjust_enabled", False)
            and self._prev_day_close > 0
            and candles is not None
            and not candles.empty
        ):
            today_open = float(candles.iloc[0].get("open", 0) if hasattr(candles.iloc[0], "get") else candles.iloc[0]["open"])
            gap_threshold = getattr(self._config, "gap_threshold_pct", 0.03)
            if today_open > 0:
                gap_pct = (today_open - self._prev_day_close) / self._prev_day_close
                if gap_pct >= gap_threshold:
                    breakout_ref = today_open

        breakout_pct = (current_price - breakout_ref) / breakout_ref
        if breakout_pct < min_bp:
            self.diag_counters["breakout_fail"] += 1
            if _aft:
                self.diag_counters["afternoon_bp_fail"] += 1
            logger.debug(
                f"[BREAKOUT] 미달: {tick['ticker']} {breakout_pct:.2%} < {min_bp:.2%}"
            )
            return None
        self.diag_counters["breakout_pass"] += 1

        # 1b) 돌파 시점 가격 대비 현재 괴리 제한 (고점 진입 방지)
        if breakout_price is not None and breakout_price > 0:
            max_gap = getattr(self._config, "max_entry_above_breakout_pct", 0.05)
            gap = (current_price - breakout_price) / breakout_price
            if gap > max_gap:
                self.diag_counters["entry_too_high"] += 1
                logger.info(
                    f"[MAX_ENTRY] {tick['ticker']} 차단: {gap * 100:.1f}% > {max_gap * 100:.0f}% "
                    f"(cur={current_price:,.0f} bp={breakout_price:,.0f})"
                )
                return None
            logger.info(f"[MAX_ENTRY] {tick['ticker']} 통과: {gap * 100:.1f}%")

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
            _eff_vol_ratio = self._config.momentum_volume_ratio
            if _aft:
                _aft_vr = getattr(self._config, "afternoon_min_volume_ratio", 3.0)
                if _aft_vr > _eff_vol_ratio:
                    _eff_vol_ratio = _aft_vr
            required_volume = self._prev_day_volume * _eff_vol_ratio

        if cum_volume < required_volume:
            self.diag_counters["volume_fail"] += 1
            if _aft:
                self.diag_counters["afternoon_vol_fail"] += 1
            logger.debug(
                f"[VOLUME] 미달: {tick['ticker']} "
                f"cum={cum_volume:,.0f} < req={required_volume:,.0f}"
            )
            return None

        # 3) 마지막 캔들 종가 돌파 재확정 (ADR-016: 최소 돌파폭 적용)
        last_close = candles.iloc[-1]["close"]
        last_breakout_pct = (last_close - breakout_ref) / breakout_ref
        if last_breakout_pct < min_bp:
            self.diag_counters["breakout_last_fail"] += 1
            logger.debug(
                f"[BREAKOUT_LAST] 미달: {tick['ticker']} "
                f"{last_breakout_pct:.2%} < {min_bp:.2%}"
            )
            return None

        # 4) ADX 추세 필터 (카운팅은 _check_adx 내부)
        if self._config.adx_enabled:
            _aft_adx = getattr(self._config, "afternoon_min_adx", 25.0) if _aft else None
            if not self._check_adx(candles, tick["ticker"], min_adx=_aft_adx):
                if _aft and _aft_adx is not None:
                    self.diag_counters["afternoon_adx_fail"] += 1
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

        # 스코어링 컨텍스트 구성
        signal_context: dict = {
            "breakout_pct": breakout_pct,
            "adx": self._last_adx,
        }
        if self._prev_day_volume > 0:
            signal_context["volume_ratio"] = cum_volume / self._prev_day_volume
        if self._prev_day_high > 0 and self._prev_day_close > 0:
            signal_context["close_to_high"] = self._prev_day_close / self._prev_day_high
        # ATR 추정: 최근 14봉 평균 TR / 평균 종가 (candle-based, DB 조회 없음)
        if len(candles) >= 5:
            _recent = candles.tail(min(14, len(candles)))
            _avg_tr = (_recent["high"] - _recent["low"]).mean()
            _mid = _recent["close"].mean()
            if _mid > 0:
                signal_context["atr_pct"] = _avg_tr / _mid

        ref_label = "시가" if breakout_ref != self._prev_day_high else "전일고가"
        logger.info(
            f"모멘텀 매수 신호: {tick['ticker']} price={current_price} "
            f"ref={breakout_ref}({ref_label}) cum_vol={cum_volume:,.0f}"
        )

        return Signal(
            ticker=tick["ticker"],
            side="buy",
            price=current_price,
            strategy="momentum",
            reason=f"전일 고점({breakout_ref:,.0f}) 돌파 + 거래량 {self._config.momentum_volume_ratio:.1f}배 확인",
            context=signal_context,
        )

    def _check_adx(self, candles: pd.DataFrame, ticker: str = "", min_adx: float | None = None) -> bool:
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
            _adx_threshold = min_adx if min_adx is not None else self._config.adx_min
            if current_adx < _adx_threshold:
                self.diag_counters["adx_fail"] += 1
                logger.debug(
                    f"[ADX] 미달: {ticker} {current_adx:.1f} < {_adx_threshold}"
                )
                return False
            self.diag_counters["adx_pass"] += 1
            self._last_adx = float(current_adx)
            logger.debug(
                f"[ADX] 통과: {ticker} {current_adx:.1f} >= {_adx_threshold}"
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
