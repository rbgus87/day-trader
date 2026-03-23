"""strategy/momentum_strategy.py — 모멘텀 브레이크아웃 전략 (F-STR-03).

전일 고점 돌파 후 리테스트 지지 확인 → 재돌파 시 진입.
전일 거래량 200% 이상 필터 필수.
"""

import pandas as pd
from loguru import logger

from config.settings import TradingConfig
from strategy.base_strategy import BaseStrategy, Signal


class MomentumStrategy(BaseStrategy):
    """전일 고점 돌파 + 거래량 200% 확인 후 매수."""

    def __init__(self, config: TradingConfig) -> None:
        self._config = config
        self._prev_day_high: float = 0.0
        self._prev_day_volume: int = 0
        self._signal_fired: bool = False

    # ------------------------------------------------------------------
    # 전일 기준값 설정
    # ------------------------------------------------------------------

    def set_prev_day_data(self, high: float, volume: int) -> None:
        """전일 고가·거래량 기준값 저장."""
        self._prev_day_high = high
        self._prev_day_volume = volume

    # ------------------------------------------------------------------
    # 추상 메서드 구현
    # ------------------------------------------------------------------

    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        """매수 신호 생성.

        조건:
        1. 거래 가능 시간 (09:05 ~ 15:20)
        2. 현재가 > 전일 고점 (돌파)
        3. 누적 거래량 >= 전일 거래량 × momentum_volume_ratio (200%)
        4. 마지막 캔들 종가 > 전일 고점 (확인)
        5. 신호 1회만 발생
        """
        if not self.is_tradable_time():
            return None

        if self._signal_fired:
            return None

        current_price: float = tick["price"]

        # 1) 가격 돌파 확인
        if current_price <= self._prev_day_high:
            return None

        # 2) 거래량 필터 (캔들 누적 거래량 >= 전일 × 2.0)
        if candles is None or candles.empty:
            return None

        cum_volume: float = candles["volume"].sum()
        required_volume: float = self._prev_day_volume * self._config.momentum_volume_ratio
        if cum_volume < required_volume:
            return None

        # 3) 마지막 캔들 종가 > 전일 고점 (확인 캔들)
        if candles.iloc[-1]["close"] <= self._prev_day_high:
            return None

        self._signal_fired = True
        logger.info(
            f"모멘텀 매수 신호: {tick['ticker']} price={current_price} "
            f"prev_high={self._prev_day_high} cum_vol={cum_volume:,.0f}"
        )

        return Signal(
            ticker=tick["ticker"],
            side="buy",
            price=current_price,
            strategy="momentum",
            reason=f"전일 고점({self._prev_day_high:,.0f}) 돌파 + 거래량 {self._config.momentum_volume_ratio:.0f}배 확인",
        )

    def get_stop_loss(self, entry_price: float) -> float:
        """손절가: 진입가 × (1 - 1.5%) = 진입가 × 0.985."""
        return entry_price * 0.985

    def get_take_profit(self, entry_price: float) -> tuple[float, float]:
        """(tp1, tp2): tp1 = 진입가 × 1.02, tp2 = 0 (트레일링 스톱)."""
        tp1 = entry_price * (1 + self._config.tp1_pct)
        return tp1, 0

    def reset(self) -> None:
        """일별 리셋 — 신호 플래그만 초기화 (기준값은 유지)."""
        self._signal_fired = False
