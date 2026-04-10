"""strategy/open_break_strategy.py — 시가 기준 돌파 전략.

장 초반 10분 노이즈 회피 후 시가 대비 break_pct 돌파 + 거래량 확인 시 매수.
"""

from datetime import time as dt_time

import pandas as pd
from loguru import logger

from config.settings import TradingConfig
from strategy.base_strategy import BaseStrategy, Signal


class OpenBreakStrategy(BaseStrategy):
    """시가 + break_pct 돌파 + 전일 대비 거래량 확인 후 매수."""

    def __init__(self, config: TradingConfig) -> None:
        super().__init__()
        self._config = config
        self._prev_day_volume: int = 0
        # 신호 시작 시각 파싱
        parts = config.open_break_start.split(":")
        self._signal_start = dt_time(int(parts[0]), int(parts[1]))
        self.configure_multi_trade(
            max_trades=config.max_trades_per_day,
            cooldown_minutes=config.cooldown_minutes,
        )

    def set_prev_day_volume(self, vol: int) -> None:
        """전일 총 거래량 설정 (외부 주입)."""
        self._prev_day_volume = vol

    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        if not self.can_trade():
            return None

        # 시간 필터: signal_start 이전 차단
        now = self._backtest_time if self._backtest_time else None
        if now is not None and now < self._signal_start:
            return None

        if candles is None or candles.empty:
            return None

        day_open = float(candles.iloc[0]["open"])
        break_level = day_open * (1 + self._config.open_break_pct)

        # 조건 1: 현재가 돌파
        current_price = tick["price"]
        if current_price <= break_level:
            return None

        # 조건 2: 최신 캔들 종가 확인
        last = candles.iloc[-1]
        if last["close"] <= break_level:
            return None

        # 조건 3: 누적 거래량 >= 전일 × volume_ratio
        cum_volume = candles["volume"].sum()
        required = self._prev_day_volume * self._config.open_break_volume_ratio
        if cum_volume < required:
            return None

        logger.info(
            f"시가돌파 매수 신호: {tick['ticker']} price={current_price} "
            f"break_level={break_level:.0f} cum_vol={cum_volume:,.0f}"
        )

        return Signal(
            ticker=tick["ticker"],
            side="buy",
            price=current_price,
            strategy="open_break",
            reason=f"시가({day_open:,.0f}) +{self._config.open_break_pct:.1%} 돌파 + 거래량 확인",
        )

    def get_stop_loss(self, entry_price: float) -> float:
        """손절가: 당일 시가 기준이지만 entry_price로 근사."""
        return entry_price * (1 + self._config.open_break_stop_loss_pct)

    def get_take_profit(self, entry_price: float) -> tuple[float, float]:
        tp1 = entry_price * (1 + self._config.tp1_pct)
        return tp1, 0

    def reset(self) -> None:
        super().reset()
