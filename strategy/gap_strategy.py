"""strategy/gap_strategy.py — 갭 앤 고 전략.

당일 시가가 전일 종가 대비 min_gap_pct 이상 갭업 → 첫 양봉에서 진입.
"""

import pandas as pd
from loguru import logger

from config.settings import TradingConfig
from strategy.base_strategy import BaseStrategy, Signal


class GapStrategy(BaseStrategy):
    """갭업 확인 후 첫 양봉 매수."""

    def __init__(self, config: TradingConfig) -> None:
        self._config = config
        self._prev_close: float = 0.0
        self._signaled_today: bool = False
        self.configure_multi_trade(
            max_trades=config.max_trades_per_day,
            cooldown_minutes=config.cooldown_minutes,
        )

    def set_prev_close(self, price: float) -> None:
        """전일 종가 설정 (외부 주입)."""
        self._prev_close = price

    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        if not self.can_trade():
            return None

        if self._signaled_today:
            return None

        if self._prev_close <= 0:
            return None

        if candles is None or candles.empty:
            return None

        # 당일 시가
        day_open = float(candles.iloc[0]["open"])

        # 조건 1: 갭 확인
        gap_pct = (day_open - self._prev_close) / self._prev_close
        if gap_pct < self._config.gap_min_gap_pct:
            return None

        # 조건 2: 최신 캔들이 양봉
        last = candles.iloc[-1]
        if last["close"] <= last["open"]:
            return None

        # 조건 3: 누적 거래량 > 0
        if candles["volume"].sum() <= 0:
            return None

        current_price = tick["price"]
        self._signaled_today = True

        logger.info(
            f"갭 매수 신호: {tick['ticker']} price={current_price} "
            f"gap={gap_pct:.2%} prev_close={self._prev_close}"
        )

        return Signal(
            ticker=tick["ticker"],
            side="buy",
            price=current_price,
            strategy="gap",
            reason=f"갭업 {gap_pct:.1%} (전일종가 {self._prev_close:,.0f}) + 양봉 확인",
        )

    def get_stop_loss(self, entry_price: float) -> float:
        """손절가: 당일 시가 × (1 + gap_stop_loss_pct)."""
        # 시가 기준 손절이지만 entry_price만 받으므로 entry_price 기준 적용
        return entry_price * (1 + self._config.gap_stop_loss_pct)

    def get_take_profit(self, entry_price: float) -> tuple[float, float]:
        tp1 = entry_price * (1 + self._config.tp1_pct)
        return tp1, 0

    def reset(self) -> None:
        super().reset()
        self._signaled_today = False
