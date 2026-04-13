"""strategy/base_strategy.py — 전략 ABC (복수 매매 지원)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, time

import pandas as pd
from loguru import logger


@dataclass
class Signal:
    ticker: str
    side: str          # "buy" / "sell"
    price: float
    strategy: str
    reason: str
    qty: int | None = None


class BaseStrategy(ABC):
    """전략 베이스 클래스 — 복수 매매 지원."""

    BLOCK_UNTIL = time(9, 5)
    MARKET_CLOSE = time(15, 20)

    def __init__(self) -> None:
        """모든 인스턴스 변수 초기화 — 클래스 변수 공유 방지."""
        self._trade_count: int = 0
        self._max_trades: int = 5
        self._cooldown_minutes: int = 10
        self._last_exit_time: datetime | None = None
        self._has_position: bool = False
        self._backtest_time: time | None = None

    def configure_multi_trade(
        self, max_trades: int = 5, cooldown_minutes: int = 10,
    ) -> None:
        """복수 매매 파라미터 설정."""
        self._max_trades = max_trades
        self._cooldown_minutes = cooldown_minutes

    @abstractmethod
    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        """매수/매도 신호 생성."""

    @abstractmethod
    def get_stop_loss(self, entry_price: float) -> float:
        """전략별 손절가."""

    @abstractmethod
    def get_take_profit(self, entry_price: float) -> tuple[float, float]:
        """(tp1, tp2) 익절가."""

    def set_backtest_time(self, t: time | None) -> None:
        """백테스트 모드에서 시뮬레이션 시각을 설정한다."""
        self._backtest_time = t

    def is_tradable_time(self) -> bool:
        """09:05 이전, 15:20 이후 신호 차단."""
        now = self._backtest_time if self._backtest_time else datetime.now().time()
        return self.BLOCK_UNTIL <= now <= self.MARKET_CLOSE

    def can_trade(self) -> bool:
        """복수 매매 조건 확인: 거래 한도 + 쿨다운 + 포지션 없음."""
        if self._has_position:
            return False
        if self._trade_count >= self._max_trades:
            if not hasattr(self, '_cantrade_diag'):
                self._cantrade_diag = True
                logger.info(f"[TRADE-LIMIT] max_trades={self._max_trades} 도달, trade_count={self._trade_count}")
            return False
        if not self._is_cooldown_elapsed():
            return False
        return self.is_tradable_time()

    def on_entry(self) -> None:
        """매수 체결 시 호출."""
        self._has_position = True
        self._trade_count += 1

    def on_exit(self) -> None:
        """청산 완료 시 호출."""
        self._has_position = False
        if self._backtest_time:
            # 백테스트: 오늘 날짜 + 시뮬레이션 시각
            self._last_exit_time = datetime.combine(
                datetime.now().date(), self._backtest_time,
            )
        else:
            self._last_exit_time = datetime.now()

    def reset(self) -> None:
        """일일 리셋 (하위 클래스에서 super().reset() 호출)."""
        self._trade_count = 0
        self._last_exit_time = None
        self._has_position = False

    def _is_cooldown_elapsed(self) -> bool:
        """마지막 청산 후 쿨다운이 경과했는지 확인."""
        if self._cooldown_minutes <= 0:
            return True
        if self._last_exit_time is None:
            return True
        if self._backtest_time:
            now = datetime.combine(
                datetime.now().date(), self._backtest_time,
            )
        else:
            now = datetime.now()
        elapsed = (now - self._last_exit_time).total_seconds() / 60
        return elapsed >= self._cooldown_minutes
