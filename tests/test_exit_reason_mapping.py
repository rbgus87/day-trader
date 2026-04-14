"""tests/test_exit_reason_mapping.py — 각 청산 경로별 exit_reason/strategy 기록 검증.

버그 1/2 회귀 방지:
- trades.exit_reason이 실제 청산 사유로 기록되는지 (force_close 하드코딩 방지)
- trades.strategy가 매도 행에도 전략명으로 기록되는지 ('unknown' 방지)
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from config.settings import TradingConfig
from core.paper_order_manager import PaperOrderManager
from core.order_manager import OrderManager


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.execute_safe = AsyncMock(return_value=1)
    return db


@pytest.fixture
def mock_notifier():
    n = MagicMock()
    n.send = AsyncMock(return_value=True)
    n.send_urgent = AsyncMock(return_value=True)
    return n


@pytest.fixture
def paper_om(mock_db, mock_notifier):
    return PaperOrderManager(
        notifier=mock_notifier, db=mock_db, trading_config=TradingConfig(),
    )


def _last_trade_insert(mock_db):
    """execute_safe에 전달된 (sql, params)를 INSERT INTO trades 기준으로 반환."""
    for call in reversed(mock_db.execute_safe.call_args_list):
        sql = call[0][0]
        if "INSERT INTO trades" in sql:
            return sql, call[0][1]
    raise AssertionError("trades INSERT 호출 없음")


class TestExitReasonPaper:
    """각 청산 메서드가 exit_reason을 정확히 DB에 기록한다."""

    @pytest.mark.asyncio
    async def test_stop_loss_default(self, paper_om, mock_db):
        await paper_om.execute_sell_stop("005930", 10, price=70000, strategy="momentum")
        _, args = _last_trade_insert(mock_db)
        # INSERT: ticker, strategy, side, price, qty, amount, pnl, pnl_pct, exit_reason, now
        assert args[1] == "momentum"
        assert args[8] == "stop_loss"

    @pytest.mark.asyncio
    async def test_stop_loss_as_trailing_stop(self, paper_om, mock_db):
        """tp1 도달 후 손절은 trailing_stop으로 기록."""
        await paper_om.execute_sell_stop(
            "005930", 5, price=70500, strategy="orb",
            exit_reason="trailing_stop",
        )
        _, args = _last_trade_insert(mock_db)
        assert args[1] == "orb"
        assert args[8] == "trailing_stop"

    @pytest.mark.asyncio
    async def test_tp1_hit(self, paper_om, mock_db):
        await paper_om.execute_sell_tp1(
            "005930", 72000, 100, strategy="vwap",
        )
        _, args = _last_trade_insert(mock_db)
        assert args[1] == "vwap"
        assert args[8] == "tp1_hit"

    @pytest.mark.asyncio
    async def test_time_stop(self, paper_om, mock_db):
        await paper_om.execute_sell_force_close(
            "005930", 10, price=69500, strategy="pullback",
            exit_reason="time_stop",
        )
        _, args = _last_trade_insert(mock_db)
        assert args[1] == "pullback"
        assert args[8] == "time_stop"

    @pytest.mark.asyncio
    async def test_forced_close(self, paper_om, mock_db):
        await paper_om.execute_sell_force_close(
            "005930", 10, price=69500, strategy="momentum",
        )
        _, args = _last_trade_insert(mock_db)
        assert args[1] == "momentum"
        assert args[8] == "forced_close"


class TestExitReasonReal:
    """실매매 OrderManager도 동일하게 exit_reason을 기록한다."""

    @pytest.fixture
    def real_om(self, mock_db, mock_notifier):
        rest = MagicMock()
        rest.send_order = AsyncMock(return_value={
            "rt_cd": "0",
            "output": {"ODNO": "REAL-001"},
        })
        return OrderManager(
            rest_client=rest, notifier=mock_notifier, db=mock_db,
            trading_config=TradingConfig(),
        )

    @pytest.mark.asyncio
    async def test_time_stop_real(self, real_om, mock_db):
        await real_om.execute_sell_force_close(
            "005930", 10, price=69500, strategy="momentum",
            exit_reason="time_stop",
        )
        _, args = _last_trade_insert(mock_db)
        # INSERT: ticker, strategy, side, order_type, price, qty, amount, pnl, pnl_pct, exit_reason, now
        assert args[1] == "momentum"
        assert args[9] == "time_stop"

    @pytest.mark.asyncio
    async def test_forced_close_real(self, real_om, mock_db):
        await real_om.execute_sell_force_close(
            "005930", 10, price=69500, strategy="vwap",
        )
        _, args = _last_trade_insert(mock_db)
        assert args[1] == "vwap"
        assert args[9] == "forced_close"

    @pytest.mark.asyncio
    async def test_tp1_real(self, real_om, mock_db):
        await real_om.execute_sell_tp1(
            "005930", 72000, 100, strategy="orb",
        )
        _, args = _last_trade_insert(mock_db)
        assert args[1] == "orb"
        assert args[9] == "tp1_hit"

    @pytest.mark.asyncio
    async def test_stop_loss_real(self, real_om, mock_db):
        await real_om.execute_sell_stop(
            "005930", 10, price=69000, strategy="pullback",
        )
        _, args = _last_trade_insert(mock_db)
        assert args[1] == "pullback"
        assert args[9] == "stop_loss"


class TestStrategyPropagation:
    """strategy 전달 누락 방지 (기본값 'unknown' 대신 호출부가 반드시 명시적으로 넘긴 값)."""

    @pytest.mark.asyncio
    async def test_default_strategy_is_unknown(self, paper_om, mock_db):
        """호출자가 strategy를 빼먹으면 'unknown'으로 저장 (실제 운영에선 호출부가 반드시 채워야 함)."""
        await paper_om.execute_sell_stop("005930", 10, price=70000)
        _, args = _last_trade_insert(mock_db)
        assert args[1] == "unknown"
