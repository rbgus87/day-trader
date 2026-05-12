"""core/order_tracker.py — 주문 접수와 체결을 분리하는 인메모리 상태 추적기.

paper_mode에서는 사용하지 않는다 (PaperOrderManager는 즉시 체결 가정).

스펙: docs/superpowers/specs/2026-05-12-order-confirmation-pipeline-design.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from loguru import logger


class OrderStatus(Enum):
    PENDING = "pending"
    PARTIAL = "partial"
    FILLED = "filled"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class PendingOrder:
    order_no: str
    ticker: str
    side: str                       # "buy" / "sell"
    requested_qty: int
    filled_qty: int = 0
    filled_price: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    submitted_at: datetime = field(default_factory=datetime.now)
    last_updated: datetime = field(default_factory=datetime.now)


class OrderTracker:
    """주문번호 기반 인메모리 체결 상태 추적기."""

    # 활성(active) 상태 — get_pending이 비None을 반환하는 상태
    _ACTIVE_STATES = {OrderStatus.PENDING, OrderStatus.PARTIAL}

    def __init__(self, timeout_seconds: float = 10.0):
        self._timeout_seconds = timeout_seconds
        self._orders: dict[str, PendingOrder] = {}   # order_no → PendingOrder
        self._ticker_index: dict[str, str] = {}      # ticker → active order_no

    # ── 상태 변경 ──
    def submit(self, order_no: str, ticker: str, side: str, qty: int) -> None:
        if order_no in self._orders:
            logger.warning(f"[ORDER-TRACK] {order_no} submit 중복 — 무시")
            return
        existing_no = self._ticker_index.get(ticker)
        if existing_no and existing_no in self._orders:
            existing = self._orders[existing_no]
            if existing.status in self._ACTIVE_STATES:
                logger.warning(
                    f"[ORDER-TRACK] {ticker} 이전 주문 {existing_no}"
                    f"({existing.status.value}) 활성 중 — {order_no} 덮어쓰기"
                )
        now = datetime.now()
        self._orders[order_no] = PendingOrder(
            order_no=order_no, ticker=ticker, side=side,
            requested_qty=qty, submitted_at=now, last_updated=now,
        )
        self._ticker_index[ticker] = order_no

    def on_fill(
        self, order_no: str, filled_qty: int, filled_price: float,
    ) -> PendingOrder | None:
        order = self._orders.get(order_no)
        if order is None:
            logger.warning(f"[ORDER-TRACK] {order_no} 알 수 없는 주문번호 on_fill")
            return None
        if order.status not in self._ACTIVE_STATES:
            logger.warning(
                f"[ORDER-TRACK] {order_no} 비활성({order.status.value}) on_fill 무시"
            )
            return order
        if filled_qty <= 0:
            logger.warning(f"[ORDER-TRACK] {order_no} 무효 filled_qty={filled_qty} 무시")
            return order
        # VWAP 누적 (단순 가중평균)
        new_total = order.filled_qty + filled_qty
        if new_total > 0:
            order.filled_price = (
                order.filled_price * order.filled_qty + filled_price * filled_qty
            ) / new_total
        order.filled_qty = new_total
        order.last_updated = datetime.now()
        # 상태 전이
        if order.filled_qty >= order.requested_qty:
            order.status = OrderStatus.FILLED
            # ticker_index 정리 (재진입 가능)
            if self._ticker_index.get(order.ticker) == order_no:
                del self._ticker_index[order.ticker]
        else:
            order.status = OrderStatus.PARTIAL
        return order

    def mark_failed(self, order_no: str, reason: str) -> None:
        order = self._orders.get(order_no)
        if order is None:
            return
        if order.status not in self._ACTIVE_STATES:
            logger.debug(
                f"[ORDER-TRACK] {order_no} already terminal "
                f"({order.status.value}) — mark_failed skipped"
            )
            return
        order.status = OrderStatus.FAILED
        order.last_updated = datetime.now()
        if self._ticker_index.get(order.ticker) == order_no:
            del self._ticker_index[order.ticker]
        logger.warning(f"[ORDER-TRACK] {order_no} FAILED — {reason}")

    def mark_timeout(self, order_no: str) -> None:
        order = self._orders.get(order_no)
        if order is None:
            return
        if order.status not in self._ACTIVE_STATES:
            logger.debug(
                f"[ORDER-TRACK] {order_no} already terminal "
                f"({order.status.value}) — mark_timeout skipped"
            )
            return
        order.status = OrderStatus.TIMEOUT
        order.last_updated = datetime.now()
        if self._ticker_index.get(order.ticker) == order_no:
            del self._ticker_index[order.ticker]
        logger.warning(f"[ORDER-TRACK] {order_no} TIMEOUT")

    def remove(self, order_no: str) -> None:
        order = self._orders.pop(order_no, None)
        if order is not None and self._ticker_index.get(order.ticker) == order_no:
            del self._ticker_index[order.ticker]

    # ── 조회 ──
    def get_pending(self, ticker: str) -> PendingOrder | None:
        """활성(PENDING/PARTIAL) 상태의 가장 최근 주문 반환. 재진입 가드용."""
        order_no = self._ticker_index.get(ticker)
        if order_no is None:
            return None
        order = self._orders.get(order_no)
        if order is None or order.status not in self._ACTIVE_STATES:
            return None
        return order

    def get_by_order_no(self, order_no: str) -> PendingOrder | None:
        return self._orders.get(order_no)

    def get_unfilled_older_than(self, seconds: float) -> list[PendingOrder]:
        """활성 상태인데 submitted_at으로부터 seconds 이상 경과한 주문 목록."""
        threshold = datetime.now() - timedelta(seconds=seconds)
        return [
            o for o in self._orders.values()
            if o.status in self._ACTIVE_STATES and o.submitted_at < threshold
        ]
