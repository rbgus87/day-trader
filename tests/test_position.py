"""tests/test_position.py — Position FSM 상태 전이 검증."""
import pytest
from datetime import datetime
from core.position import Position, PositionStatus, ExitPhase


@pytest.fixture
def pos():
    return Position(
        ticker="005930",
        entry_price=70000.0,
        qty=10,
        remaining_qty=10,
        stop_loss=68000.0,
        strategy="momentum",
        entry_time=datetime.now(),
    )


# 정상 전이
def test_pending_to_confirmed(pos):
    """PENDING → CONFIRMED 정상 전이."""
    assert pos.status == PositionStatus.PENDING
    pos.confirm()
    assert pos.status == PositionStatus.CONFIRMED

def test_confirmed_to_exit_pending(pos):
    """CONFIRMED → EXIT_PENDING 정상 전이."""
    pos.confirm()
    pos.mark_exit_pending()
    assert pos.status == PositionStatus.EXIT_PENDING

def test_exit_pending_to_closed(pos):
    """EXIT_PENDING → CLOSED 정상 전이."""
    pos.confirm()
    pos.mark_exit_pending()
    pos.close()
    assert pos.status == PositionStatus.CLOSED

# 잘못된 전이 에러
def test_invalid_confirm_on_confirmed(pos):
    """이미 CONFIRMED 상태에서 confirm() → AssertionError."""
    pos.confirm()
    with pytest.raises(AssertionError):
        pos.confirm()

def test_invalid_confirm_on_closed(pos):
    """CLOSED 상태에서 confirm() → AssertionError."""
    pos.confirm()
    pos.mark_exit_pending()
    pos.close()
    with pytest.raises(AssertionError):
        pos.confirm()

def test_invalid_close_from_confirmed(pos):
    """CONFIRMED에서 close() 직접 호출 → AssertionError."""
    pos.confirm()
    with pytest.raises(AssertionError):
        pos.close()

# exit_phase 전이
def test_activate_breakeven(pos):
    """activate_breakeven: exit_phase → BREAKEVEN, stop_loss 상향."""
    new_stop = 70700.0
    pos.activate_breakeven(new_stop)
    assert pos.exit_phase == ExitPhase.BREAKEVEN
    assert pos.stop_loss == new_stop

def test_activate_breakeven_does_not_lower_stop(pos):
    """activate_breakeven: 기존 stop_loss보다 낮으면 상향하지 않음."""
    pos.stop_loss = 69000.0
    pos.activate_breakeven(68000.0)  # 현재 stop_loss(69000)보다 낮음
    assert pos.stop_loss == 69000.0

def test_mark_limit_up_failed(pos):
    """mark_limit_up_failed: exit_phase → LIMIT_UP_FAILED."""
    new_stop = 71000.0
    pos.mark_limit_up_failed(new_stop)
    assert pos.exit_phase == ExitPhase.LIMIT_UP_FAILED
    assert pos.stop_loss == new_stop

# is_active, is_confirmed
def test_is_active_pending(pos):
    assert pos.is_active is True

def test_is_active_confirmed(pos):
    pos.confirm()
    assert pos.is_active is True

def test_is_active_exit_pending(pos):
    pos.confirm()
    pos.mark_exit_pending()
    assert pos.is_active is False  # EXIT_PENDING은 active 아님

def test_is_confirmed(pos):
    assert pos.is_confirmed is False
    pos.confirm()
    assert pos.is_confirmed is True

# highest_price 초기화
def test_highest_price_initialized_to_entry(pos):
    """__post_init__에 의해 highest_price가 entry_price로 초기화됨."""
    assert pos.highest_price == 70000.0

# mark_exit_pending 중복 가드
def test_mark_exit_pending_idempotent(pos):
    """EXIT_PENDING 상태에서 mark_exit_pending() 재호출 시 에러 없음."""
    pos.confirm()
    pos.mark_exit_pending()
    pos.mark_exit_pending()  # 중복 — 예외 없어야 함
    assert pos.status == PositionStatus.EXIT_PENDING
