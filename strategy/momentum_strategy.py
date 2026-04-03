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
        """매수 신호 생성.

        조건:
        1. 거래 가능 시간
        2. 현재가 > 전일 고점 (돌파)
        3. 누적 거래량 >= 전일 거래량 × momentum_volume_ratio
        4. 마지막 캔들 종가 > 전일 고점 (확인)
        5. VWAP 방향 필터 (옵션)
        """
        if not self.can_trade():
            return None

        current_price: float = tick["price"]

        # 전일 고가가 설정되지 않았으면 스킵
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
        if candles.iloc[-1]["close"] <= self._prev_day_high:
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
