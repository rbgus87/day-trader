from enum import Enum, auto
from dataclasses import dataclass
from datetime import datetime


class PositionStatus(Enum):
    PENDING = auto()      # 주문 접수, 체결 대기
    CONFIRMED = auto()    # 체결 확인, 보유 중
    EXIT_PENDING = auto() # 청산 주문 발송, 체결 대기
    CLOSED = auto()       # 청산 완료


class ExitPhase(Enum):
    NONE = auto()           # 아직 청산 조건 미도달
    TRAILING = auto()       # 트레일링 스톱 활성
    BREAKEVEN = auto()      # BE 발동 (stop >= entry)
    LIMIT_UP_PENDING = auto() # 상한가 청산 시도 중
    LIMIT_UP_FAILED = auto()  # 상한가 청산 실패, stop 상향됨


@dataclass
class Position:
    ticker: str
    entry_price: float
    qty: int
    remaining_qty: int
    stop_loss: float
    strategy: str
    entry_time: datetime

    # 상태
    status: PositionStatus = PositionStatus.PENDING
    exit_phase: ExitPhase = ExitPhase.NONE

    # 추적
    highest_price: float = 0.0
    tp1_price: float | None = None
    trailing_pct: float = 0.0
    limit_up_price: float | None = None
    tp1_hit: bool = False

    def confirm(self) -> None:
        """PENDING → CONFIRMED 전이."""
        assert self.status == PositionStatus.PENDING, (
            f"confirm() requires PENDING status, got {self.status}"
        )
        self.status = PositionStatus.CONFIRMED

    def mark_exit_pending(self) -> None:
        """CONFIRMED → EXIT_PENDING 전이."""
        assert self.status == PositionStatus.CONFIRMED, (
            f"mark_exit_pending() requires CONFIRMED, got {self.status}"
        )
        self.status = PositionStatus.EXIT_PENDING

    def close(self) -> None:
        """청산 완료 — EXIT_PENDING → CLOSED 전이."""
        assert self.status == PositionStatus.EXIT_PENDING, (
            f"close() requires EXIT_PENDING, got {self.status}"
        )
        self.status = PositionStatus.CLOSED

    def activate_breakeven(self, new_stop: float) -> None:
        """BE 발동: exit_phase → BREAKEVEN, stop_loss 상향."""
        self.exit_phase = ExitPhase.BREAKEVEN
        self.stop_loss = max(self.stop_loss, new_stop)

    def mark_limit_up_failed(self, new_stop: float) -> None:
        """상한가 청산 실패: exit_phase → LIMIT_UP_FAILED, stop_loss 상향."""
        self.exit_phase = ExitPhase.LIMIT_UP_FAILED
        self.stop_loss = max(self.stop_loss, new_stop)

    @property
    def is_active(self) -> bool:
        """포지션 활성 여부 (PENDING 또는 CONFIRMED)."""
        return self.status in (PositionStatus.PENDING, PositionStatus.CONFIRMED)

    @property
    def is_confirmed(self) -> bool:
        return self.status == PositionStatus.CONFIRMED

    @property
    def pnl_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        return (self.highest_price - self.entry_price) / self.entry_price
