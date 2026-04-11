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
        self.configure_multi_trade(
            max_trades=config.max_trades_per_day,
            cooldown_minutes=config.cooldown_minutes,
        )

    def set_prev_day_data(self, high: float, volume: int) -> None:
        """전일 고가·거래량 기준값 저장."""
        self._prev_day_high = high
        self._prev_day_volume = volume

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
        """손절가: 진입가 × (1 + momentum_stop_loss_pct)."""
        return entry_price * (1 + self._config.momentum_stop_loss_pct)

    def get_take_profit(self, entry_price: float) -> tuple[float, float]:
        """(tp1, tp2): tp1 = 진입가 × (1 + tp1_pct), tp2 = 0 (트레일링 스톱)."""
        tp1 = entry_price * (1 + self._config.tp1_pct)
        return tp1, 0

    def reset(self) -> None:
        """일별 리셋 (기준값은 유지)."""
        super().reset()
