"""tests/test_paper_order_manager.py — PaperOrderManager 단위 테스트."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.paper_order_manager import PaperOrderManager
from config.settings import TradingConfig


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.execute_safe = AsyncMock(return_value=1)
    return db


@pytest.fixture
def mock_notifier():
    notifier = MagicMock()
    notifier.send = MagicMock(return_value=None)
    notifier.send_execution = MagicMock(return_value=None)
    return notifier


@pytest.fixture
def paper_om(mock_db, mock_notifier):
    return PaperOrderManager(
        notifier=mock_notifier,
        db=mock_db,
        trading_config=TradingConfig(),
    )


class TestPaperBuy:
    @pytest.mark.asyncio
    async def test_execute_buy_returns_order(self, paper_om):
        """매수 시뮬레이션이 주문번호와 수량을 반환한다."""
        result = await paper_om.execute_buy("005930", 70000, 100)
        assert result is not None
        assert "order_no" in result
        assert result["order_no"].startswith("PAPER-")
        assert result["qty"] == 55  # 100 * 0.55

    @pytest.mark.asyncio
    async def test_execute_buy_records_to_db(self, paper_om, mock_db):
        """매수 체결이 DB에 기록된다."""
        await paper_om.execute_buy("005930", 70000, 100, strategy="orb")
        mock_db.execute_safe.assert_called_once()
        sql = mock_db.execute_safe.call_args[0][0]
        assert "INSERT INTO trades" in sql
        # strategy가 실제 전략명으로 기록되는지 확인
        args = mock_db.execute_safe.call_args[0][1]
        assert args[1] == "orb"

    @pytest.mark.asyncio
    async def test_execute_buy_sends_telegram(self, paper_om, mock_notifier):
        """매수 체결이 send_execution(mode='paper')로 전송된다 (ADR-008)."""
        await paper_om.execute_buy("005930", 70000, 100)
        mock_notifier.send_execution.assert_called_once()
        kwargs = mock_notifier.send_execution.call_args.kwargs
        assert kwargs["mode"] == "paper"
        assert kwargs["side"] == "buy"
        assert kwargs["ticker"] == "005930"

    @pytest.mark.asyncio
    async def test_blocks_duplicate_order(self, paper_om):
        """동일 종목 중복 주문을 차단한다."""
        paper_om._active_orders["005930"] = True
        result = await paper_om.execute_buy("005930", 70000, 100)
        assert result is None


class TestPaperSell:
    @pytest.mark.asyncio
    async def test_execute_sell_stop(self, paper_om):
        """손절 시뮬레이션이 정상 동작한다."""
        result = await paper_om.execute_sell_stop("005930", 50)
        assert result is not None
        assert result["qty"] == 50

    @pytest.mark.asyncio
    async def test_execute_sell_force_close(self, paper_om, mock_notifier):
        """강제 청산 시뮬레이션이 send_execution(reason='forced_close')로 기록된다."""
        await paper_om.execute_sell_force_close("005930", 100)
        mock_notifier.send_execution.assert_called_once()
        kwargs = mock_notifier.send_execution.call_args.kwargs
        assert kwargs["mode"] == "paper"
        assert kwargs["side"] == "sell"
        assert kwargs["reason"] == "forced_close"

    @pytest.mark.asyncio
    async def test_execute_sell_tp1(self, paper_om):
        """1차 익절 시뮬레이션."""
        result = await paper_om.execute_sell_tp1("005930", 72000, 100)
        assert result is not None
        assert result["qty"] == 50  # 100 * 0.5


class TestPaperOrderNo:
    @pytest.mark.asyncio
    async def test_sequential_order_numbers(self, paper_om):
        """주문번호가 순차 증가한다."""
        r1 = await paper_om._simulate_order("A", 10, 1000, "buy")
        r2 = await paper_om._simulate_order("B", 20, 2000, "sell")
        assert r1["order_no"] == "PAPER-000001"
        assert r2["order_no"] == "PAPER-000002"


class TestPaperConfirmation:
    @pytest.mark.asyncio
    async def test_wait_for_confirmation_immediate(self, paper_om):
        """페이퍼 모드에서 체결 확인은 즉시 반환된다."""
        result = await paper_om.wait_for_confirmation("PAPER-000001")
        assert result is not None
        assert result["status"] == "filled"
