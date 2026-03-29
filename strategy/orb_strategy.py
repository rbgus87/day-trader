"""DEPRECATED: 백테스트 결과 4종목 전부 PF<1.0, 2026-03-30 폐기.

strategy/orb_strategy.py — Opening Range Breakout (복수 매매 지원).
비교 백테스트용으로 보존. 실전 파이프라인에서는 사용하지 않음.
"""

import pandas as pd
from loguru import logger

from strategy.base_strategy import BaseStrategy, Signal
from config.settings import TradingConfig


class OrbStrategy(BaseStrategy):
    """09:05~09:15 레인지 형성 후 상단 돌파 매수."""

    def __init__(self, config: TradingConfig, min_range_pct: float = 0.0):
        self._config = config
        self._min_range_pct = min_range_pct
        self._range_high: float | None = None
        self._range_low: float | None = None
        self._prev_day_volume: int = 0
        self.configure_multi_trade(
            max_trades=config.max_trades_per_day,
            cooldown_minutes=config.cooldown_minutes,
        )

    def set_prev_day_volume(self, volume: int) -> None:
        self._prev_day_volume = volume

    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        if not self.can_trade():
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

        if self._range_high is None or self._range_low is None:
            return None

        # 최소 레인지 폭 필터
        if self._min_range_pct > 0:
            range_pct = (self._range_high - self._range_low) / self._range_low
            if range_pct < self._min_range_pct:
                return None

        current_price = tick["price"]
        if current_price <= self._range_high:
            return None

        # 거래량 확인 (volume_ratio=0이면 비활성화)
        if (self._config.orb_volume_ratio > 0
                and self._prev_day_volume > 0
                and candles is not None and not candles.empty):
            cum_volume = candles["volume"].sum()
            if cum_volume < self._prev_day_volume * self._config.orb_volume_ratio:
                return None

        # 최신 캔들 종가 > 레인지 상단
        if candles is not None and not candles.empty:
            if candles.iloc[-1]["close"] <= self._range_high:
                return None

        logger.info(
            f"ORB 매수 신호 (#{self._trade_count + 1}): {tick['ticker']} "
            f"price={current_price} range=[{self._range_low}, {self._range_high}]"
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
        tp2 = 0
        return tp1, tp2

    def reset(self) -> None:
        super().reset()
        self._range_high = None
        self._range_low = None
