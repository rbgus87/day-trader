"""tests/test_order_tracker.py — OrderTracker 단위 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.order_tracker import OrderTracker, OrderStatus, PendingOrder


def test_imports():
    """enum / dataclass / 클래스 import."""
    assert OrderStatus.PENDING.value == "pending"
    assert OrderStatus.PARTIAL.value == "partial"
    assert OrderStatus.FILLED.value == "filled"
    assert OrderStatus.FAILED.value == "failed"
    assert OrderStatus.TIMEOUT.value == "timeout"


def _tracker(**overrides) -> OrderTracker:
    return OrderTracker(timeout_seconds=overrides.get("timeout_seconds", 10.0))


class TestSubmit:
    def test_submit_creates_pending(self):
        t = _tracker()
        t.submit("ORD1", "000001", "buy", 10)
        pending = t.get_pending("000001")
        assert pending is not None
        assert pending.order_no == "ORD1"
        assert pending.status == OrderStatus.PENDING
        assert pending.requested_qty == 10
        assert pending.filled_qty == 0

    def test_submit_duplicate_ignored(self):
        t = _tracker()
        t.submit("ORD1", "000001", "buy", 10)
        t.submit("ORD1", "000001", "buy", 999)  # 중복
        assert t.get_by_order_no("ORD1").requested_qty == 10


class TestOnFill:
    def test_full_fill_marks_filled(self):
        t = _tracker()
        t.submit("ORD1", "000001", "buy", 10)
        order = t.on_fill("ORD1", filled_qty=10, filled_price=1000.0)
        assert order is not None
        assert order.status == OrderStatus.FILLED
        assert order.filled_qty == 10
        assert order.filled_price == 1000.0
        # 재진입 가능 — get_pending None
        assert t.get_pending("000001") is None

    def test_partial_fill_marks_partial(self):
        t = _tracker()
        t.submit("ORD1", "000001", "buy", 10)
        order = t.on_fill("ORD1", filled_qty=4, filled_price=1000.0)
        assert order.status == OrderStatus.PARTIAL
        assert order.filled_qty == 4
        # 재진입 가드 유지
        assert t.get_pending("000001") is not None

    def test_partial_then_full_fill(self):
        t = _tracker()
        t.submit("ORD1", "000001", "buy", 10)
        t.on_fill("ORD1", filled_qty=4, filled_price=1000.0)
        order = t.on_fill("ORD1", filled_qty=6, filled_price=1050.0)
        assert order.status == OrderStatus.FILLED
        assert order.filled_qty == 10
        # VWAP: (4 × 1000 + 6 × 1050) / 10 = 1030
        assert order.filled_price == pytest.approx(1030.0, abs=1e-6)

    def test_fill_after_filled_ignored(self):
        """이미 FILLED 상태에서 추가 on_fill → 무시."""
        t = _tracker()
        t.submit("ORD1", "000001", "buy", 10)
        t.on_fill("ORD1", filled_qty=10, filled_price=1000.0)
        order = t.on_fill("ORD1", filled_qty=5, filled_price=2000.0)
        # 누적 변동 없음
        assert order.filled_qty == 10
        assert order.filled_price == 1000.0

    def test_fill_unknown_order_returns_none(self):
        t = _tracker()
        assert t.on_fill("UNKNOWN", 1, 1000.0) is None

    def test_fill_zero_qty_ignored(self):
        t = _tracker()
        t.submit("ORD1", "000001", "buy", 10)
        order = t.on_fill("ORD1", filled_qty=0, filled_price=1000.0)
        assert order.filled_qty == 0
        assert order.status == OrderStatus.PENDING


class TestQueries:
    def test_get_pending_only_active(self):
        t = _tracker()
        assert t.get_pending("000001") is None
        t.submit("ORD1", "000001", "sell", 5)
        assert t.get_pending("000001") is not None
        t.on_fill("ORD1", filled_qty=5, filled_price=1000.0)
        assert t.get_pending("000001") is None  # FILLED → 활성 아님

    def test_get_unfilled_older_than(self):
        t = _tracker()
        t.submit("ORD1", "000001", "buy", 10)
        # submit_at을 강제로 과거로 변경
        t.get_by_order_no("ORD1").submitted_at = datetime.now() - timedelta(seconds=20)
        stale = t.get_unfilled_older_than(10.0)
        assert len(stale) == 1
        assert stale[0].order_no == "ORD1"

    def test_get_unfilled_excludes_filled(self):
        t = _tracker()
        t.submit("ORD1", "000001", "buy", 10)
        t.get_by_order_no("ORD1").submitted_at = datetime.now() - timedelta(seconds=20)
        t.on_fill("ORD1", filled_qty=10, filled_price=1000.0)
        assert t.get_unfilled_older_than(10.0) == []
