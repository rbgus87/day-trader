"""tests/test_risk_manager.py"""

import pytest
from unittest.mock import AsyncMock

from risk.risk_manager import RiskManager
from config.settings import TradingConfig


@pytest.fixture
def risk_mgr():
    return RiskManager(
        trading_config=TradingConfig(),
        db=AsyncMock(),
        notifier=AsyncMock(),
    )


def test_check_stop_loss_triggers(risk_mgr):
    risk_mgr._positions["005930"] = {
        "entry_price": 70000, "stop_loss": 68950,
        "qty": 10, "remaining_qty": 10,
    }
    result = risk_mgr.check_stop_loss("005930", current_price=68900)
    assert result is True


def test_check_stop_loss_safe(risk_mgr):
    risk_mgr._positions["005930"] = {
        "entry_price": 70000, "stop_loss": 68950,
        "qty": 10, "remaining_qty": 10,
    }
    result = risk_mgr.check_stop_loss("005930", current_price=69000)
    assert result is False


def test_daily_loss_limit_blocks(risk_mgr):
    risk_mgr._daily_pnl = -200_000
    risk_mgr._daily_capital = 10_000_000
    assert risk_mgr.is_trading_halted() is True


def test_daily_loss_limit_allows(risk_mgr):
    risk_mgr._daily_pnl = -100_000
    risk_mgr._daily_capital = 10_000_000
    assert risk_mgr.is_trading_halted() is False


@pytest.mark.asyncio
async def test_update_trailing_stop(risk_mgr):
    # Phase 2 Day 7: 이 테스트는 고정 trailing_pct 경로를 검증.
    # 가짜 ticker로 ATR 조회가 None이 되도록 하여 폴백(고정) 경로 사용.
    risk_mgr._positions["TEST001"] = {
        "entry_price": 70000, "stop_loss": 68950,
        "qty": 10, "remaining_qty": 5,
        "highest_price": 71400, "trailing_pct": 0.01,
        "tp1_hit": True,
    }
    risk_mgr.update_trailing_stop("TEST001", current_price=72000)
    pos = risk_mgr._positions["TEST001"]
    assert pos["highest_price"] == 72000
    assert pos["stop_loss"] == 72000 * (1 - 0.01)


@pytest.mark.asyncio
async def test_check_consecutive_losses(risk_mgr):
    risk_mgr._db.fetch_all = AsyncMock(return_value=[
        {"total_pnl": -50000},
        {"total_pnl": -30000},
        {"total_pnl": -10000},
    ])
    reduced = await risk_mgr.check_consecutive_losses()
    assert reduced is True


# ---------------------------------------------------------------------------
# save_daily_summary 테스트
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_daily_summary_with_trades(risk_mgr):
    """매매 기록이 있으면 daily_pnl 저장 + 요약 반환."""
    risk_mgr._db.fetch_all = AsyncMock(return_value=[
        {"strategy": "orb", "pnl": 15000, "pnl_pct": 0.02},
        {"strategy": "orb", "pnl": -5000, "pnl_pct": -0.007},
        {"strategy": "vwap", "pnl": 8000, "pnl_pct": 0.01},
    ])
    risk_mgr._db.execute = AsyncMock(return_value=1)

    summary = await risk_mgr.save_daily_summary()

    assert summary is not None
    assert summary["total_trades"] == 3
    assert summary["wins"] == 2
    assert summary["losses"] == 1
    assert summary["total_pnl"] == 18000
    assert summary["strategy"] == "orb,vwap"
    assert 0.66 < summary["win_rate"] < 0.67
    risk_mgr._db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_daily_summary_no_trades(risk_mgr):
    """매매 기록이 없으면 None 반환."""
    risk_mgr._db.fetch_all = AsyncMock(return_value=[])

    summary = await risk_mgr.save_daily_summary()
    assert summary is None
