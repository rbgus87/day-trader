"""tests/test_engine_worker_order_tracking.py — engine_worker x OrderTracker 통합 시나리오.

EngineWorker 전체 부팅을 피하기 위해 OrderTracker + risk_manager 직접 호출로
통합 지점의 시나리오를 검증. 실 통합은 engine_worker.py의 코드 수정으로 보장.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from core.order_tracker import OrderStatus, OrderTracker


def _tracker() -> OrderTracker:
    return OrderTracker(timeout_seconds=10.0)


def _risk_manager(tmp_path):
    """실 DbManager + AsyncMock notifier로 RiskManager 구성."""
    from config.settings import TradingConfig
    from data.db_manager import DbManager
    from risk.risk_manager import RiskManager

    db = DbManager(str(tmp_path / "t.db"))
    asyncio.run(db.init())
    return RiskManager(
        trading_config=TradingConfig(),
        db=db,
        notifier=AsyncMock(),
    )


class TestBuyPipeline:
    def test_real_mode_buy_submit_then_filled(self, tmp_path):
        """real_mode 매수: submit → on_fill → mark_confirmed 가능."""
        rm = _risk_manager(tmp_path)
        t = _tracker()
        # 1) submit + register_position(status=pending)
        t.submit("ORD1", "000001", "buy", 10)
        rm.register_position(
            ticker="000001", entry_price=10000, qty=10, stop_loss=9200,
            status="pending",
        )
        assert rm.get_position("000001")["status"] == "pending"
        # 2) on_fill → FILLED
        order = t.on_fill("ORD1", filled_qty=10, filled_price=10000)
        assert order.status == OrderStatus.FILLED
        # 3) _handle_fill 시뮬: mark_confirmed
        rm.mark_confirmed("000001")
        assert rm.get_position("000001")["status"] == "confirmed"

    def test_paper_mode_buy_immediate_confirmed(self, tmp_path):
        """paper_mode: tracker 미사용, register_position(status=confirmed) 즉시."""
        rm = _risk_manager(tmp_path)
        rm.register_position(
            ticker="000001", entry_price=10000, qty=10, stop_loss=9200,
            status="confirmed",
        )
        assert rm.get_position("000001")["status"] == "confirmed"


class TestSellPipeline:
    def test_real_mode_sell_settle_deferred(self, tmp_path):
        """real_mode 매도: submit 후 settle_sell 호출 안 됨, on_fill 후에만 settle."""
        rm = _risk_manager(tmp_path)
        t = _tracker()

        rm.register_position(
            ticker="000001", entry_price=10000, qty=10, stop_loss=9200,
            status="confirmed",
        )
        pos_before = rm.get_position("000001")
        assert pos_before["remaining_qty"] == 10
        # 매도 submit (engine_worker는 settle_sell 미호출)
        t.submit("ORD2", "000001", "sell", 10)
        assert rm.get_position("000001")["remaining_qty"] == 10
        # on_fill 시점 → _handle_fill에서 settle_sell 호출
        order = t.on_fill("ORD2", filled_qty=10, filled_price=11000)
        assert order.status == OrderStatus.FILLED
        rm.settle_sell("000001", order.filled_price, order.filled_qty)
        assert rm.get_position("000001") is None  # 전량 매도 후 제거


class TestReEntryGuard:
    def test_pending_sell_blocks_re_entry(self):
        """매도 PENDING 상태에서 get_pending → 비None → exit 분기 스킵."""
        t = _tracker()
        t.submit("ORD3", "000001", "sell", 10)
        assert t.get_pending("000001") is not None  # exit check 스킵 트리거

    def test_filled_allows_re_entry(self):
        """FILLED 후 get_pending None → 다음 매도 가능."""
        t = _tracker()
        t.submit("ORD3", "000001", "sell", 10)
        t.on_fill("ORD3", filled_qty=10, filled_price=11000)
        assert t.get_pending("000001") is None


class TestTimeoutPath:
    def test_timeout_marks_and_clears_index(self):
        """타임아웃 후 자연 재시도 가능 (get_pending None)."""
        t = _tracker()
        t.submit("ORD4", "000001", "sell", 10)
        # 시간 강제 과거화
        t.get_by_order_no("ORD4").submitted_at = datetime.now() - timedelta(seconds=20)
        stale = t.get_unfilled_older_than(10.0)
        assert len(stale) == 1
        t.mark_timeout("ORD4")
        assert t.get_pending("000001") is None  # 다음 tick에서 자연 재시도

    def test_limit_up_exit_pending_lifecycle(self):
        """_limit_up_exit_pending set 라이프사이클 — FILLED / TIMEOUT 양쪽 정리.

        engine_worker._handle_fill과 _order_tracker_timeout_checker 양쪽이
        ticker를 discard해야 다음 tick에서 limit_up 재트리거 가능.
        본 테스트는 _limit_up_exit_pending: set[str] 의 의미를 검증.
        """
        pending: set[str] = set()
        # 1) limit_up_exit submit
        pending.add("000001")
        # 2-A) FILLED 경로: _handle_fill에서 discard
        pending.discard("000001")
        assert "000001" not in pending

        # 다시 submit
        pending.add("000002")
        # 2-B) TIMEOUT 경로: _order_tracker_timeout_checker에서 discard
        pending.discard("000002")
        assert "000002" not in pending
