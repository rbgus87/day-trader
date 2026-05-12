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
        notifier=MagicMock(),
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
    # 손절은 즉시 체결을 위해 시장가(PRICE_MARKET='03').
    # 이전엔 '00'(지정가)을 잘못 넘기던 버그 — order_type 도메인 통일로 수정.
    assert call_args.kwargs["order_type"] == "03"


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


@pytest.mark.asyncio
async def test_prefer_best_limit_converts_market_to_06(order_mgr):
    """prefer_best_limit=True + order_type='market' → 키움 코드 '06' 전송."""
    order_mgr._rest_client.send_order = AsyncMock(
        return_value={"output": {"ODNO": "77777"}, "rt_cd": "0"}
    )
    result = await order_mgr.execute_sell_stop(
        ticker="005930", qty=10, price=70000,
        prefer_best_limit=True,
    )
    assert result is not None
    call_args = order_mgr._rest_client.send_order.call_args
    assert call_args.kwargs["order_type"] == "06"  # PRICE_BEST_LIMIT


@pytest.mark.asyncio
async def test_market_without_prefer_best_limit_stays_03(order_mgr):
    """prefer_best_limit=False(기본) → 시장가 '03' 유지."""
    order_mgr._rest_client.send_order = AsyncMock(
        return_value={"output": {"ODNO": "77778"}, "rt_cd": "0"}
    )
    await order_mgr.execute_sell_stop(ticker="005930", qty=10, price=70000)
    call_args = order_mgr._rest_client.send_order.call_args
    assert call_args.kwargs["order_type"] == "03"  # PRICE_MARKET


@pytest.mark.asyncio
async def test_rejection_callback_invoked_on_rt_cd_nonzero(order_mgr):
    """rt_cd ≠ '0' 응답 시 on_rejection(ticker, rt_cd) 콜백 호출."""
    order_mgr._rest_client.send_order = AsyncMock(
        return_value={"output": {}, "rt_cd": "9", "msg1": "거부"}
    )
    rejections: list[tuple[str, str]] = []
    result = await order_mgr.execute_sell_stop(
        ticker="005930", qty=10, price=70000,
        on_rejection=lambda tk, rt: rejections.append((tk, rt)),
    )
    assert result is None  # rt_cd != "0" → 실패 반환 유지
    assert rejections == [("005930", "9")]


@pytest.mark.asyncio
async def test_rejection_callback_exception_swallowed(order_mgr):
    """on_rejection 콜백 자체 예외는 로그만 + 정상 진행."""
    order_mgr._rest_client.send_order = AsyncMock(
        return_value={"output": {}, "rt_cd": "9"}
    )
    def boom(tk, rt):
        raise RuntimeError("callback fail")
    # 예외가 _send_order 밖으로 전파되면 안 됨
    result = await order_mgr.execute_sell_stop(
        ticker="005930", qty=10, price=70000,
        on_rejection=boom,
    )
    assert result is None


@pytest.mark.asyncio
async def test_prefer_best_limit_ignored_for_limit_order(order_mgr):
    """prefer_best_limit=True + order_type='limit' → 변환 없음, 키움 코드 '00' 유지.

    가드(market에서만 best_limit 전환)가 깨지지 않는지 회귀 방어.
    """
    order_mgr._rest_client.send_order = AsyncMock(
        return_value={"output": {"ODNO": "99999"}, "rt_cd": "0"}
    )
    await order_mgr._send_order(
        ticker="005930", qty=10, price=70000, side="sell",
        order_type="limit", prefer_best_limit=True,
    )
    call_args = order_mgr._rest_client.send_order.call_args
    assert call_args.kwargs["order_type"] == "00"  # PRICE_LIMIT — 전환 없음
