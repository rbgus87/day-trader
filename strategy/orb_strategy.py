"""strategy/orb_strategy.py — Opening Range Breakout."""

import pandas as pd
from loguru import logger

from strategy.base_strategy import BaseStrategy, Signal
from config.settings import TradingConfig


class OrbStrategy(BaseStrategy):
    """09:05~09:15 레인지 형성 후 상단 돌파 매수."""

    def __init__(self, config: TradingConfig):
        self._config = config
        self._range_high: float | None = None
        self._range_low: float | None = None
        self._prev_day_volume: int = 0
        self._signal_fired: bool = False

    def set_prev_day_volume(self, volume: int) -> None:
        self._prev_day_volume = volume

    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        if not self.is_tradable_time() or self._signal_fired:
            return None

        time_str = tick["time"][:4]

        # 레인지 빌딩 (09:05~09:15)
        if "0905" <= time_str <= "0915":
            range_candles = candles[
                (candles["time"] >= "09:05") & (candles["time"] <= "09:15")
            ] if candles is not None and "time" in candles.columns else pd.DataFrame()
            if not range_candles.empty:
                self._range_high = range_candles["high"].max()
                self._range_low = range_candles["low"].min()
            return None

        if self._range_high is None:
            return None

        current_price = tick["price"]
        if current_price <= self._range_high:
            return None

        # 거래량 확인
        if self._prev_day_volume > 0 and candles is not None and not candles.empty:
            cum_volume = candles["volume"].sum()
            if cum_volume < self._prev_day_volume * self._config.orb_volume_ratio:
                return None

        # 최신 캔들 종가 > 레인지 상단
        if candles is not None and not candles.empty:
            if candles.iloc[-1]["close"] <= self._range_high:
                return None

        self._signal_fired = True
        logger.info(
            f"ORB 매수 신호: {tick['ticker']} price={current_price} "
            f"range=[{self._range_low}, {self._range_high}]"
        )

        return Signal(
            ticker=tick["ticker"],
            side="buy",
            price=current_price,
            strategy="orb",
            reason=f"레인지 상단({self._range_high:,.0f}) 돌파",
        )

    def get_stop_loss(self, entry_price: float) -> float:
        return entry_price * (1 + self._config.orb_stop_loss_pct)

    def get_take_profit(self, entry_price: float) -> tuple[float, float]:
        tp1 = entry_price * (1 + self._config.tp1_pct)
        tp2 = 0  # 트레일링 스톱으로 관리
        return tp1, tp2

    def reset(self) -> None:
        self._range_high = None
        self._range_low = None
        self._signal_fired = False
