"""strategy/pullback_strategy.py — 눌림목(Pullback) 전략.

초기 급등 후 첫 번째 조정에서 매수, 고점 재돌파 시 익절.

진입: entry_start ~ entry_end (기본 09:30 ~ 13:00)
손절: day_high × (1 - sl_from_high_pct)
익절: day_high × (1 + tp_above_high_pct)
강제: 15:10
"""
from __future__ import annotations

import re
from datetime import datetime, time

import pandas as pd

from config.settings import TradingConfig
from strategy.base_strategy import BaseStrategy, Signal


def _parse_time(s: str, default: time) -> time:
    m = re.match(r"(\d+):(\d+)", str(s))
    return time(int(m.group(1)), int(m.group(2))) if m else default


class PullbackStrategy(BaseStrategy):
    """눌림목 전략 — 초기 급등 후 첫 번째 조정에서 진입, 고점 재돌파 시 익절."""

    def __init__(self, config: TradingConfig) -> None:
        super().__init__()
        self._config = config

        self._prev_day_volume: int = 0
        self._prev_day_close: float = 0.0

        # 당일 급등/눌림 상태
        self._surge_detected: bool = False
        self._day_high: float = 0.0

        self._surge_pct       = float(getattr(config, "pb_surge_pct", 0.05))
        self._pullback_depth  = float(getattr(config, "pb_pullback_depth", 0.02))
        self._min_above_close = float(getattr(config, "pb_min_above_close_pct", 0.01))
        self._sl_from_high    = float(getattr(config, "pb_sl_from_high_pct", 0.05))
        self._tp_above_high   = float(getattr(config, "pb_tp_above_high_pct", 0.01))
        self._min_volume      = int(getattr(config, "pb_min_volume", 50000))

        self._entry_start: time = _parse_time(
            getattr(config, "pb_entry_start", "09:30"), time(9, 30)
        )
        self._entry_end: time = _parse_time(
            getattr(config, "pb_entry_end", "13:00"), time(13, 0)
        )

        self.configure_multi_trade(max_trades=1, cooldown_minutes=999)

    # ────────────────────────── 데이터 주입 ─────────────────────────────────

    def set_prev_day_data(self, high: float, volume: int, close: float = 0.0) -> None:
        self._prev_day_volume = volume
        self._prev_day_close = close

    def set_prev_day_volume(self, volume: int) -> None:
        self._prev_day_volume = volume

    def set_prev_day_candles(self, candles: pd.DataFrame | None) -> None:
        pass

    def reset(self) -> None:
        super().reset()
        self._surge_detected = False
        self._day_high = 0.0

    # ────────────────────────── 급등 상태 갱신 ──────────────────────────────

    def update_surge(self, candles: pd.DataFrame) -> None:
        """분봉 기준 급등/고점 상태 갱신 (실시간 호출용)."""
        if candles.empty or self._prev_day_close <= 0:
            return
        threshold = self._prev_day_close * (1.0 + self._surge_pct)
        for _, row in candles.iterrows():
            h = float(row.get("high", 0))
            if h > threshold:
                self._surge_detected = True
            if self._surge_detected and h > self._day_high:
                self._day_high = h

    # ────────────────────────── 시그널 생성 ─────────────────────────────────

    def generate_signal(
        self,
        candles: pd.DataFrame,
        tick: dict,
        *,
        breakout_price: float | None = None,
    ) -> Signal | None:
        if not self.can_trade():
            return None

        now = self._backtest_time if self._backtest_time else datetime.now().time()
        if now < self._entry_start or now > self._entry_end:
            return None

        self.update_surge(candles)
        if not self._surge_detected or self._day_high <= 0:
            return None

        price = float(tick.get("price", 0.0))
        if price <= 0:
            return None

        if price > self._day_high * (1.0 - self._pullback_depth):
            return None

        if self._prev_day_close > 0 and price <= self._prev_day_close * (1.0 + self._min_above_close):
            return None

        if self._prev_day_volume > 0 and self._prev_day_volume < self._min_volume:
            return None

        return Signal(
            ticker=tick.get("ticker", ""),
            side="buy",
            price=price,
            strategy="pullback",
            reason="pullback_entry",
            context={"day_high": self._day_high},
        )

    # ────────────────────────── 손절 / 익절 ─────────────────────────────────

    def get_stop_loss(self, entry_price: float) -> float:
        if self._day_high > 0:
            return self._day_high * (1.0 - self._sl_from_high)
        return entry_price * 0.95

    def get_take_profit(self, entry_price: float) -> float:
        if self._day_high > 0:
            return self._day_high * (1.0 + self._tp_above_high)
        return entry_price * 1.05
