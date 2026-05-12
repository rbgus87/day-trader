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
