"""strategy/big_candle_strategy.py — 세력 캔들 추종 전략.

2단계 상태 머신:
  SCANNING → 비정상적 큰 양봉 탐색
  PULLBACK → 되돌림(mid 이하) 대기 후 양봉 반등 시 진입
"""

from datetime import time as dt_time

import pandas as pd
from loguru import logger

from config.settings import TradingConfig
from strategy.base_strategy import BaseStrategy, Signal

STATE_SCANNING = "scanning"
STATE_PULLBACK = "pullback"


class BigCandleStrategy(BaseStrategy):
    """세력 캔들 탐지 → 되돌림 → 반등 양봉 매수."""

    ATR_PERIOD = 10

    def __init__(self, config: TradingConfig) -> None:
        super().__init__()
        self._config = config
        self._state: str = STATE_SCANNING
        self._big_candle_high: float = 0.0
        self._big_candle_low: float = 0.0
        self._big_candle_mid: float = 0.0
        self._big_candle_idx: int = -1
        self._pullback_touched: bool = False
        self.configure_multi_trade(
            max_trades=config.max_trades_per_day,
            cooldown_minutes=config.cooldown_minutes,
        )

    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        if not self.can_trade():
            return None

        if candles is None or len(candles) < self.ATR_PERIOD + 2:
            return None

        idx = len(candles) - 1

        if self._state == STATE_SCANNING:
            self._scan_big_candle(candles, idx)
            return None

        if self._state == STATE_PULLBACK:
            # 타임아웃 체크
            elapsed = idx - self._big_candle_idx
            if elapsed > self._config.big_candle_timeout_min:
                self._reset_state()
                return None

            last = candles.iloc[-1]

            # 되돌림 감지: 캔들 low <= mid
            if float(last["low"]) <= self._big_candle_mid:
                self._pullback_touched = True

            # 반등 확인: 되돌림 후 양봉
            if self._pullback_touched and last["close"] > last["open"]:
                current_price = tick["price"]
                logger.info(
                    f"세력캔들 매수 신호: {tick['ticker']} price={current_price} "
                    f"big_high={self._big_candle_high:.0f} mid={self._big_candle_mid:.0f}"
                )
                self._reset_state()
                return Signal(
                    ticker=tick["ticker"],
                    side="buy",
                    price=current_price,
                    strategy="big_candle",
                    reason=f"세력캔들(고가 {self._big_candle_high:,.0f}) 되돌림 후 반등",
                )

        return None

    def _scan_big_candle(self, candles: pd.DataFrame, idx: int) -> None:
        """직전 ATR_PERIOD 캔들 ATR 대비 현재 캔들 body가 큰지 확인."""
        if idx < self.ATR_PERIOD + 1:
            return

        last = candles.iloc[-1]
        body = float(last["close"]) - float(last["open"])

        # 양봉만
        if body <= 0:
            return

        # ATR 계산: 직전 ATR_PERIOD 캔들의 (high - low) 평균
        lookback = candles.iloc[-(self.ATR_PERIOD + 1):-1]
        atr = (lookback["high"] - lookback["low"]).mean()

        if atr <= 0:
            return

        if body > atr * self._config.big_candle_atr_multiplier:
            self._state = STATE_PULLBACK
            self._big_candle_high = float(last["high"])
            self._big_candle_low = float(last["low"])
            self._big_candle_mid = (self._big_candle_high + self._big_candle_low) / 2
            self._big_candle_idx = idx
            self._pullback_touched = False

    def _reset_state(self) -> None:
        self._state = STATE_SCANNING
        self._big_candle_high = 0.0
        self._big_candle_low = 0.0
        self._big_candle_mid = 0.0
        self._big_candle_idx = -1
        self._pullback_touched = False

    def get_stop_loss(self, entry_price: float) -> float:
        """손절가: 세력 캔들 저가 기준이지만 entry_price로 근사."""
        return entry_price * (1 + self._config.big_candle_stop_loss_pct)

    def get_take_profit(self, entry_price: float) -> tuple[float, float]:
        tp1 = entry_price * (1 + self._config.tp1_pct)
        return tp1, 0

    def reset(self) -> None:
        super().reset()
        self._reset_state()
