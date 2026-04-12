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
        # Phase 2 Day 6: ATR 손절 컨텍스트
        self._ticker: str = ""
        self._last_signal_date: str = ""  # YYYY-MM-DD
        self.configure_multi_trade(
            max_trades=config.max_trades_per_day,
            cooldown_minutes=config.cooldown_minutes,
        )

    def set_prev_day_data(self, high: float, volume: int) -> None:
        """전일 고가·거래량 기준값 저장."""
        self._prev_day_high = high
        self._prev_day_volume = volume

    def set_ticker(self, ticker: str) -> None:
        """ATR 조회용 종목 코드 주입 (backtester/engine_worker에서 호출)."""
        self._ticker = ticker

    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        """매수 신호 생성."""
        if not self.can_trade():
            return None

        current_price: float = tick["price"]

        if self._prev_day_high <= 0:
            return None

        # 1) 가격 돌파 확인
        if current_price <= self._prev_day_high:
            return None

        # 2) 거래량 필터
        if candles is None or candles.empty:
            return None

        cum_volume: float = candles["volume"].sum()
        required_volume: float = self._prev_day_volume * self._config.momentum_volume_ratio
        if cum_volume < required_volume:
            return None

        # 3) 마지막 캔들 종가 > 전일 고점
        last_close = candles.iloc[-1]["close"]
        if last_close <= self._prev_day_high:
            return None

        # 4) ADX 추세 필터
        if self._config.adx_enabled and not self._check_adx(candles):
            return None

        # 5) RVol 거래량 급증 필터
        if self._config.rvol_enabled and not self._check_rvol(candles):
            return None

        # 6) VWAP 매수 우위 필터
        if self._config.vwap_enabled and not self._check_vwap(candles, current_price):
            return None

        # ATR 손절 계산을 위한 신호 발생 날짜 캡처 (캔들의 마지막 ts 기준)
        try:
            if candles is not None and not candles.empty and "ts" in candles.columns:
                self._last_signal_date = pd.to_datetime(
                    candles["ts"].iloc[-1]
                ).strftime("%Y-%m-%d")
        except Exception:
            pass  # 폴백은 get_stop_loss에서 처리

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

    def _check_adx(self, candles: pd.DataFrame) -> bool:
        """ADX 추세 강도 필터. 캔들 부족 또는 계산 실패 시 False."""
        min_candles = self._config.adx_length + 20
        if len(candles) < min_candles:
            return False
        try:
            import pandas_ta as ta
            df = candles.tail(min_candles)
            adx_result = ta.adx(df["high"], df["low"], df["close"], length=self._config.adx_length)
            if adx_result is None or adx_result.empty:
                return False
            adx_col = f"ADX_{self._config.adx_length}"
            if adx_col not in adx_result.columns:
                return False
            current_adx = adx_result[adx_col].iloc[-1]
            if pd.isna(current_adx):
                return False
            return current_adx >= self._config.adx_min
        except Exception as e:
            logger.warning(f"ADX 계산 실패: {e}")
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

    def get_take_profit(self, entry_price: float) -> tuple[float, float]:
        """TP1 계산.

        Phase 2 Day 7: atr_tp_enabled면 ticker_atr 기반 동적 TP1,
        실패/비활성 시 고정 tp1_pct 폴백. tp2는 트레일링으로 관리하므로 0.
        """
        fallback = entry_price * (1 + self._config.tp1_pct)
        if not getattr(self._config, "atr_tp_enabled", False) or not self._ticker:
            return fallback, 0
        try:
            from core.indicators import calculate_atr_tp1, get_latest_atr

            atr_pct = get_latest_atr(
                "daytrader.db", self._ticker, self._last_signal_date or None
            )
            if atr_pct is None:
                return fallback, 0
            tp1 = calculate_atr_tp1(
                entry_price=entry_price,
                atr_pct=atr_pct,
                multiplier=self._config.atr_tp_multiplier,
                min_pct=self._config.atr_tp_min_pct,
                max_pct=self._config.atr_tp_max_pct,
            )
            return tp1, 0
        except Exception as e:
            logger.warning(f"ATR TP1 계산 실패 ({self._ticker}): {e}")
            return fallback, 0

    def reset(self) -> None:
        """일별 리셋 (기준값은 유지)."""
        super().reset()
