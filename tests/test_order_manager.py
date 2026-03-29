"""tests/test_order_manager.py"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from core.order_manager import OrderManager


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.execute_safe = AsyncMock(return_value=1)
    return db


@pytest.fixture
def order_mgr(mock_db):
    return OrderManager(
        rest_client=AsyncMock(),
        risk_manager=AsyncMock(),
        notifier=AsyncMock(),
        db=mock_db,
    )


@pytest.mark.asyncio
async def test_execute_buy_split(order_mgr):
    order_mgr._rest_client.send_order = AsyncMock(
        return_value={"output": {"ODNO": "12345"}, "rt_cd": "0"}
    )
    result = await order_mgr.execute_buy(ticker="005930", price=70000, total_qty=100)
    assert result["order_no"] == "12345"
    call_args = order_mgr._rest_client.send_order.call_args
    assert call_args.kwargs["qty"] == 55  # 100 * 0.55


@pytest.mark.asyncio
async def test_duplicate_order_blocked(order_mgr):
    order_mgr._active_orders["005930"] = True
    result = await order_mgr.execute_buy(ticker="005930", price=70000, total_qty=100)
    assert result is None


@pytest.mark.asyncio
async def test_sell_tp1(order_mgr):
    order_mgr._rest_client.send_order = AsyncMock(
        return_value={"output": {"ODNO": "22222"}, "rt_cd": "0"}
    )
    result = await order_mgr.execute_sell_tp1(ticker="005930", price=71400, remaining_qty=100)
    call_args = order_mgr._rest_client.send_order.call_args
    assert call_args.kwargs["qty"] == 50  # 100 * 0.5


@pytest.mark.asyncio
async def test_sell_stop_market_order(order_mgr):
    order_mgr._rest_client.send_order = AsyncMock(
        return_value={"output": {"ODNO": "33333"}, "rt_cd": "0"}
    )
    result = await order_mgr.execute_sell_stop(ticker="005930", qty=100)
    call_args = order_mgr._rest_client.send_order.call_args
    assert call_args.kwargs["order_type"] == "00"  # 시장가


@pytest.mark.asyncio
async def test_execute_buy_records_to_db(order_mgr, mock_db):
    """execute_buy 성공 시 DB에 trades INSERT."""
    order_mgr._rest_client.send_order = AsyncMock(
        return_value={"output": {"ODNO": "44444"}, "rt_cd": "0"}
    )
    result = await order_mgr.execute_buy(ticker="005930", price=70000, total_qty=100, strategy="momentum")
    assert result is not None
    mock_db.execute_safe.assert_called_once()
    sql = mock_db.execute_safe.call_args[0][0]
    assert "INSERT INTO trades" in sql
    args = mock_db.execute_safe.call_args[0][1]
    assert args[1] == "momentum"


@pytest.mark.asyncio
async def test_send_order_records_to_db(order_mgr, mock_db):
    """_send_order 성공 시 DB에 trades INSERT."""
    order_mgr._rest_client.send_order = AsyncMock(
        return_value={"output": {"ODNO": "55555"}, "rt_cd": "0"}
    )
    result = await order_mgr.execute_sell_stop(ticker="005930", qty=50, strategy="orb")
    assert result is not None
    mock_db.execute_safe.assert_called_once()
    args = mock_db.execute_safe.call_args[0][1]
    assert args[1] == "orb"
    assert args[2] == "sell"
