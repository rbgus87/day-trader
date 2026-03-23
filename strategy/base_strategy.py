"""strategy/base_strategy.py — 전략 ABC."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, time

import pandas as pd


@dataclass
class Signal:
    ticker: str
    side: str          # "buy" / "sell"
    price: float
    strategy: str
    reason: str
    qty: int | None = None


class BaseStrategy(ABC):
    """전략 베이스 클래스."""

    BLOCK_UNTIL = time(9, 5)
    MARKET_CLOSE = time(15, 20)

    @abstractmethod
    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        """매수/매도 신호 생성."""

    @abstractmethod
    def get_stop_loss(self, entry_price: float) -> float:
        """전략별 손절가."""

    @abstractmethod
    def get_take_profit(self, entry_price: float) -> tuple[float, float]:
        """(tp1, tp2) 익절가."""

    def is_tradable_time(self) -> bool:
        """09:05 이전, 15:20 이후 신호 차단."""
        now = datetime.now().time()
        return self.BLOCK_UNTIL <= now <= self.MARKET_CLOSE
