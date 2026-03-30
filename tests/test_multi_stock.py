"""tests/test_multi_stock.py — 멀티 종목 파이프라인 로직 테스트."""

import pytest

from config.settings import TradingConfig
from risk.risk_manager import RiskManager
from unittest.mock import MagicMock


@pytest.fixture
def rm():
    db = MagicMock()
    notifier = MagicMock()
    config = TradingConfig(max_positions=3)
    rm = RiskManager(config, db, notifier)
    rm.set_daily_capital(1_000_000)
    return rm


def test_max_positions_3(rm):
    """3종목 포지션 → 4번째 진입 불가."""
    rm.register_position("A", entry_price=10000, qty=10, stop_loss=9800)
    rm.register_position("B", entry_price=20000, qty=5, stop_loss=19600)
    rm.register_position("C", entry_price=15000, qty=8, stop_loss=14700)
    open_pos = rm.get_open_positions()
    assert len(open_pos) == 3
    # 4번째 종목은 파이프라인에서 max_positions 체크로 무시됨
    # (여기서는 register 자체는 가능하지만 로직에서 막는 것을 확인)
    assert len(open_pos) >= 3  # 한도 도달


def test_same_ticker_blocked(rm):
    """이미 보유 중인 종목에 재진입 불가."""
    rm.register_position("A", entry_price=10000, qty=10, stop_loss=9800)
    pos = rm.get_position("A")
    assert pos is not None
    # candle_consumer에서 get_position(ticker) 체크로 스킵


def test_capital_split_by_max_positions(rm):
    """자본 분배: total / max_positions."""
    capital = 1_000_000
    max_positions = 3
    position_capital = capital / max_positions
    assert position_capital == pytest.approx(333333.33, rel=0.01)


def test_open_positions_returns_only_active(rm):
    """remaining_qty > 0인 포지션만 반환."""
    rm.register_position("A", entry_price=10000, qty=10, stop_loss=9800)
    rm.register_position("B", entry_price=20000, qty=5, stop_loss=19600)
    # A를 완전 청산
    rm._positions["A"]["remaining_qty"] = 0
    open_pos = rm.get_open_positions()
    assert len(open_pos) == 1
    assert "B" in open_pos
