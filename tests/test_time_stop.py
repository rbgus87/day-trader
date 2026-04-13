"""tests/test_time_stop.py — 시간 손절 테스트."""

from datetime import datetime, timedelta

import pytest

from config.settings import TradingConfig
from risk.risk_manager import RiskManager


@pytest.fixture
def rm(tmp_path):
    from unittest.mock import MagicMock
    db = MagicMock()
    notifier = MagicMock()
    config = TradingConfig()
    rm = RiskManager(config, db, notifier)
    rm.set_daily_capital(1_000_000)
    return rm


def test_time_stop_triggers_after_60min(rm):
    """60분 후 +0.3% → 청산 (min_profit=0.5% 미달)."""
    rm.register_position("005930", entry_price=10000, qty=10, stop_loss=9850)
    # 진입 시각을 61분 전으로 조작
    rm._positions["005930"]["entry_time"] = datetime.now() - timedelta(minutes=61)
    current_price = 10030  # +0.3%
    assert rm.check_time_stop("005930", current_price, time_stop_minutes=60, min_profit=0.005)


def test_time_stop_no_trigger_with_profit(rm):
    """60분 후 +0.8% → 청산 안 함 (min_profit 충족)."""
    rm.register_position("005930", entry_price=10000, qty=10, stop_loss=9850)
    rm._positions["005930"]["entry_time"] = datetime.now() - timedelta(minutes=61)
    current_price = 10080  # +0.8%
    assert not rm.check_time_stop("005930", current_price, time_stop_minutes=60, min_profit=0.005)


def test_time_stop_no_trigger_before_timeout(rm):
    """30분 후 +0.3% → 청산 안 함 (시간 미달)."""
    rm.register_position("005930", entry_price=10000, qty=10, stop_loss=9850)
    rm._positions["005930"]["entry_time"] = datetime.now() - timedelta(minutes=30)
    current_price = 10030  # +0.3%
    assert not rm.check_time_stop("005930", current_price, time_stop_minutes=60, min_profit=0.005)


def test_entry_time_recorded(rm):
    """register_position에 entry_time이 기록되는지 확인."""
    rm.register_position("005930", entry_price=10000, qty=10, stop_loss=9850)
    pos = rm.get_position("005930")
    assert "entry_time" in pos
    assert isinstance(pos["entry_time"], datetime)
