# Order Confirmation Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 주문 접수와 체결 완료를 분리하는 OrderTracker 인메모리 상태 추적기를 도입하여, real_mode에서 매도 settle을 WS "00" 체결통보 시점으로 지연시키고, pending 매도 ticker의 재진입을 차단한다. paper_mode와 backtester는 영향 없음.

**Architecture:** 신규 `core/order_tracker.py` (OrderStatus enum + PendingOrder dataclass + OrderTracker 클래스, 무상태 인메모리). risk_manager는 status 필드 + mark_confirmed 헬퍼만 추가. engine_worker에서 paper_mode 분기 + 5지점 통합 + 신규 background task(timeout checker) + `_handle_fill` 헬퍼 + `_limit_up_exit_pending: set[str]` 추적.

**Tech Stack:** Python 3.14, asyncio, dataclasses/enum, pytest, loguru. 키움 REST `kt10001`(취소) + `ka10070`(잔고 폴백).

**Spec:** `docs/superpowers/specs/2026-05-12-order-confirmation-pipeline-design.md`

**Critical implementation note from user**: `_limit_up_exit_pending` set은 **FILLED + TIMEOUT + FAILED 세 경로 모두**에서 `discard(ticker)`되어야 한다. 누락 시 상한가 매도가 영구 차단됨.

---

## File Structure

신규:
- `core/order_tracker.py` — OrderStatus enum, PendingOrder dataclass, OrderTracker class (단일 책임: 주문 상태 추적)
- `tests/test_order_tracker.py` — 10건 단위 테스트
- `tests/test_engine_worker_order_tracking.py` — 6건 통합 시나리오

수정:
- `config.yaml` — trading 섹션에 2개 키
- `config/settings.py` — TradingConfig 2개 필드
- `core/kiwoom_rest.py` — `cancel_order` 메서드 추가 (`get_account_balance`은 기존)
- `risk/risk_manager.py` — `register_position` status 파라미터 + `mark_confirmed` 헬퍼
- `backtest/backtester.py` — `register_position` 호출에 `status="confirmed"` 명시
- `gui/workers/engine_worker.py` — VIHandler 패턴 따라 통합 (7지점)
- `tests/test_risk_manager.py` — 신규 케이스
- `tests/test_settings.py` — 신규 1건

---

## Task 1: OrderTracker 코어 + 단위 테스트

**Files:**
- Create: `core/order_tracker.py`
- Create: `tests/test_order_tracker.py`

VIHandler 패턴: 무상태 인메모리, 외부 의존성 없음. TDD 순서로 단위 테스트 완성 후 통합.

### 1.1 빈 모듈 + import 가능 확인

- [ ] **Step 1: 실패 테스트 작성**

Create `tests/test_order_tracker.py`:
```python
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
```

- [ ] **Step 2: 테스트 실행하여 실패 확인**

Run: `python -m pytest tests/test_order_tracker.py::test_imports -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.order_tracker'`

- [ ] **Step 3: 최소 모듈 작성**

Create `core/order_tracker.py`:
```python
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
        order.status = OrderStatus.FAILED
        order.last_updated = datetime.now()
        if self._ticker_index.get(order.ticker) == order_no:
            del self._ticker_index[order.ticker]
        logger.warning(f"[ORDER-TRACK] {order_no} FAILED — {reason}")

    def mark_timeout(self, order_no: str) -> None:
        order = self._orders.get(order_no)
        if order is None:
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
```

- [ ] **Step 4: import 테스트 통과 확인**

Run: `python -m pytest tests/test_order_tracker.py::test_imports -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add core/order_tracker.py tests/test_order_tracker.py
git commit -m "feat: core/order_tracker.py 스켈레톤 + OrderStatus enum"
```

### 1.2 submit/on_fill/조회 단위 테스트

- [ ] **Step 1: 실패 테스트 작성**

Append to `tests/test_order_tracker.py`:
```python
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
```

- [ ] **Step 2: 테스트 실행하여 통과 확인**

Run: `python -m pytest tests/test_order_tracker.py -v`
Expected: 13 passed (1 import + 12 new)

- [ ] **Step 3: 커밋**

```bash
git add tests/test_order_tracker.py
git commit -m "test: OrderTracker submit/on_fill/조회 단위 테스트"
```

### 1.3 mark_failed / mark_timeout / remove 테스트

- [ ] **Step 1: 실패 테스트 작성**

Append to `tests/test_order_tracker.py`:
```python
class TestMarkStates:
    def test_mark_failed_clears_ticker_index(self):
        t = _tracker()
        t.submit("ORD1", "000001", "buy", 10)
        t.mark_failed("ORD1", "rt_cd=9")
        order = t.get_by_order_no("ORD1")
        assert order.status == OrderStatus.FAILED
        assert t.get_pending("000001") is None  # 재진입 가능

    def test_mark_timeout_clears_ticker_index(self):
        t = _tracker()
        t.submit("ORD1", "000001", "sell", 10)
        t.mark_timeout("ORD1")
        order = t.get_by_order_no("ORD1")
        assert order.status == OrderStatus.TIMEOUT
        assert t.get_pending("000001") is None  # 자연 재시도 가능

    def test_remove_deletes_completely(self):
        t = _tracker()
        t.submit("ORD1", "000001", "buy", 10)
        t.remove("ORD1")
        assert t.get_by_order_no("ORD1") is None
        assert t.get_pending("000001") is None
```

- [ ] **Step 2: 테스트 실행하여 통과 확인**

Run: `python -m pytest tests/test_order_tracker.py -v`
Expected: 16 passed (13 + 3 new)

- [ ] **Step 3: 커밋**

```bash
git add tests/test_order_tracker.py
git commit -m "test: OrderTracker mark_failed/mark_timeout/remove 테스트"
```

---

## Task 2: Config 외부화 (timeout 파라미터)

**Files:**
- Modify: `config.yaml` (trading 섹션)
- Modify: `config/settings.py` (TradingConfig 2개 필드)
- Modify: `tests/test_settings.py` (회귀 1건)

### 2.1 TradingConfig 필드 + 테스트

- [ ] **Step 1: 실패 테스트 작성**

In `tests/test_settings.py`, insert new test before `test_market_calendar_2027` or `test_trading_config_vi_defaults` (matching pattern):
```python
def test_trading_config_order_tracking_defaults():
    """OrderTracker 관련 기본값."""
    from config.settings import TradingConfig
    tc = TradingConfig()
    assert tc.order_confirmation_timeout_sec == 10.0
    assert tc.order_timeout_consecutive_threshold == 3
```

- [ ] **Step 2: 테스트 실행하여 실패 확인**

Run: `python -m pytest tests/test_settings.py::test_trading_config_order_tracking_defaults -v`
Expected: FAIL — AttributeError

- [ ] **Step 3: TradingConfig에 2개 필드 추가**

In `config/settings.py`, find the `TradingConfig` dataclass and add the following fields at the end of its body (after the VI block added earlier — locate via `grep -n "vi_suspected_duration_sec" config/settings.py`):

```python

    # 주문 체결 확인 파이프라인 (real_mode 전용)
    # order_confirmation_timeout_sec=10.0: WS '00' 체결통보 미수신 시 REST 폴백 트리거 시각
    # order_timeout_consecutive_threshold=3: 같은 ticker 연속 TIMEOUT 임계 (긴급 알림)
    order_confirmation_timeout_sec: float = 10.0
    order_timeout_consecutive_threshold: int = 3
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_settings.py -v`
Expected: 전체 통과 (vi_defaults 등 기존 테스트 회귀 없음)

### 2.2 config.yaml + from_yaml 와이어링

- [ ] **Step 1: config.yaml 키 추가**

In `config.yaml`, find the `vi_suspected_duration_sec: 60` line in the `trading:` section. Insert immediately after:
```yaml

  # 주문 체결 확인 파이프라인 (real_mode 전용; paper_mode는 무시)
  # order_confirmation_timeout_sec: WS '00' 체결통보 미수신 시 REST 폴백 시각
  # order_timeout_consecutive_threshold: 같은 ticker 연속 TIMEOUT 임계 (긴급 알림 발사)
  order_confirmation_timeout_sec: 10.0
  order_timeout_consecutive_threshold: 3
```

- [ ] **Step 2: AppConfig.from_yaml 와이어링**

In `config/settings.py`, find `AppConfig.from_yaml` body. Locate the section where `vi_static_pct` etc. are extracted from `t = cfg.get("trading", {})`. Add similar extraction for the 2 new keys:
```python
            order_confirmation_timeout_sec=t.get("order_confirmation_timeout_sec", 10.0),
            order_timeout_consecutive_threshold=t.get("order_timeout_consecutive_threshold", 3),
```

Position: alongside the existing `vi_*` extractions in the `TradingConfig(...)` call. Use Read to view current structure if uncertain.

- [ ] **Step 3: yaml 로딩 확인**

Run: `python -c "from config.settings import AppConfig; c = AppConfig.from_yaml(); print(c.trading.order_confirmation_timeout_sec, c.trading.order_timeout_consecutive_threshold)"`
Expected: `10.0 3`

- [ ] **Step 4: 전체 settings 테스트 회귀**

Run: `python -m pytest tests/test_settings.py -v`
Expected: 전체 통과

- [ ] **Step 5: 커밋**

```bash
git add config.yaml config/settings.py tests/test_settings.py
git commit -m "feat: config에 OrderTracker 타임아웃 + 연속 임계 파라미터 추가"
```

---

## Task 3: risk_manager status 필드 + mark_confirmed

**Files:**
- Modify: `risk/risk_manager.py`
- Modify: `tests/test_risk_manager.py`

### 3.1 register_position status 파라미터 + mark_confirmed 헬퍼

- [ ] **Step 1: 실패 테스트 작성**

In `tests/test_risk_manager.py`, append (after existing tests):
```python
def test_register_position_default_status_pending(monkeypatch, tmp_path):
    """status 기본값은 'pending' (real_mode 안전 기본)."""
    from config.settings import TradingConfig
    from data.db_manager import DbManager
    from risk.risk_manager import RiskManager

    db_path = tmp_path / "test.db"
    db = DbManager(str(db_path))
    import asyncio
    asyncio.run(db.init())

    rm = RiskManager(db=db, config=TradingConfig())
    rm.register_position(
        ticker="000001", entry_price=10000, qty=10, stop_loss=9200,
    )
    pos = rm.get_position("000001")
    assert pos is not None
    assert pos["status"] == "pending"


def test_register_position_status_confirmed(tmp_path):
    """status='confirmed' 명시 호출 (paper_mode / backtester 패턴)."""
    from config.settings import TradingConfig
    from data.db_manager import DbManager
    from risk.risk_manager import RiskManager

    db_path = tmp_path / "test.db"
    db = DbManager(str(db_path))
    import asyncio
    asyncio.run(db.init())

    rm = RiskManager(db=db, config=TradingConfig())
    rm.register_position(
        ticker="000001", entry_price=10000, qty=10, stop_loss=9200,
        status="confirmed",
    )
    pos = rm.get_position("000001")
    assert pos["status"] == "confirmed"


def test_mark_confirmed_flips_status(tmp_path):
    """mark_confirmed: pending → confirmed."""
    from config.settings import TradingConfig
    from data.db_manager import DbManager
    from risk.risk_manager import RiskManager

    db_path = tmp_path / "test.db"
    db = DbManager(str(db_path))
    import asyncio
    asyncio.run(db.init())

    rm = RiskManager(db=db, config=TradingConfig())
    rm.register_position(
        ticker="000001", entry_price=10000, qty=10, stop_loss=9200,
    )
    assert rm.get_position("000001")["status"] == "pending"
    rm.mark_confirmed("000001")
    assert rm.get_position("000001")["status"] == "confirmed"


def test_mark_confirmed_unknown_ticker_noop(tmp_path):
    """알 수 없는 ticker → 예외 없이 무시."""
    from config.settings import TradingConfig
    from data.db_manager import DbManager
    from risk.risk_manager import RiskManager

    db_path = tmp_path / "test.db"
    db = DbManager(str(db_path))
    import asyncio
    asyncio.run(db.init())

    rm = RiskManager(db=db, config=TradingConfig())
    rm.mark_confirmed("UNKNOWN")  # 예외 없어야 함
```

(Note: 기존 `test_risk_manager.py`의 fixture 패턴이 다를 수 있음. 실행하여 fixture/import 패턴 확인 후 조정 필요. Run `grep -n "def test_\|@pytest.fixture" tests/test_risk_manager.py | head -20` first to check style.)

- [ ] **Step 2: 테스트 실행하여 실패 확인**

Run: `python -m pytest tests/test_risk_manager.py::test_register_position_default_status_pending tests/test_risk_manager.py::test_mark_confirmed_flips_status -v`
Expected: FAIL — Position dict에 "status" 키가 없거나 `mark_confirmed` 메서드 없음

- [ ] **Step 3: risk_manager 수정**

In `risk/risk_manager.py`:

(a) Locate `register_position` (around line 31). Change signature:
```python
    def register_position(
        self, ticker: str, entry_price: float, qty: int, stop_loss: float,
        tp1_price: float | None = None, trailing_pct: float | None = None,
        strategy: str = "", limit_up_price: float | None = None,
        status: str = "pending",
    ) -> None:
```

Add `"status": status,` to the Position dict in the same method:
```python
        self._positions[ticker] = {
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "qty": qty,
            "remaining_qty": qty,
            "tp1_price": tp1_price,
            "trailing_pct": trailing_pct or self._config.trailing_stop_pct,
            "highest_price": entry_price,
            "tp1_hit": False,
            "entry_time": now,
            "strategy": strategy,
            "limit_up_price": limit_up_price,
            "limit_up_exit_failed": False,
            "status": status,  # 신규: "pending" | "confirmed"
        }
```

(b) Add `mark_confirmed` helper. Place immediately after `register_position` (before `remove_position`):
```python
    def mark_confirmed(self, ticker: str) -> None:
        """주문 체결 확인 후 status를 'confirmed'로 갱신. 알 수 없는 ticker는 무시."""
        pos = self._positions.get(ticker)
        if pos is not None:
            pos["status"] = "confirmed"
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_risk_manager.py -v`
Expected: 모든 기존 테스트 + 4개 신규 통과

- [ ] **Step 5: 커밋**

```bash
git add risk/risk_manager.py tests/test_risk_manager.py
git commit -m "feat: risk_manager status 필드 + mark_confirmed 헬퍼"
```

---

## Task 4: kiwoom_rest cancel_order

**Files:**
- Modify: `core/kiwoom_rest.py`

`get_account_balance` (`ka10070`)는 이미 존재. `cancel_order` (`kt10001`)만 신규 추가.

### 4.1 cancel_order 메서드 + 단위 검증

- [ ] **Step 1: 신규 메서드 추가**

In `core/kiwoom_rest.py`, find `send_order` method (around line 129). Add `cancel_order` immediately after it (before `get_account_balance`):

```python
    async def cancel_order(self, order_no: str, ticker: str, qty: int) -> dict:
        """미체결 주문 취소 (kt10001). 매수 TIMEOUT 시 사용.

        키움 cancel API는 원주문번호(orig_ord_no) + 종목코드 + 수량을 요구.
        엔드포인트는 주문과 동일한 EP_ORDER 경로.
        """
        if not re.match(r"^\d{6}$", ticker):
            raise ValueError(f"잘못된 종목코드: {ticker}")
        if qty < 1:
            raise ValueError(f"취소 수량은 1 이상: {qty}")
        body = {
            "orig_ord_no": order_no,
            "stk_cd": ticker,
            "ord_qty": qty,
            "acnt_no": self._config.account_no,
        }
        return await self.request("POST", EP_ORDER, API_STOCK_CANCEL, data=body)
```

- [ ] **Step 2: import 확인 + 정적 검증**

Run: `python -c "from core.kiwoom_rest import KiwoomRestClient; import inspect; assert 'cancel_order' in dir(KiwoomRestClient); print('ok')"`
Expected: `ok`

- [ ] **Step 3: 커밋**

```bash
git add core/kiwoom_rest.py
git commit -m "feat: kiwoom_rest.cancel_order (kt10001) 추가"
```

---

## Task 5: backtester register_position 호출 명시화

**Files:**
- Modify: `backtest/backtester.py`

backtester가 `register_position`을 호출할 때 `status="confirmed"`를 명시적으로 전달. 기본값 "pending"은 real_mode 안전 기본이며 backtester에서는 즉시 체결 가정.

### 5.1 backtester 호출 갱신

- [ ] **Step 1: register_position 호출 위치 확인**

Run: `grep -n "register_position" backtest/backtester.py`

기대: 1개 이상의 호출. 각 호출에 `status="confirmed"` 추가.

- [ ] **Step 2: 각 호출에 status="confirmed" 추가**

For every `register_position(...)` call in `backtest/backtester.py`, add `status="confirmed"` as the last keyword argument. Example transformation:

Before:
```python
            self._risk_manager.register_position(
                ticker=ticker,
                entry_price=entry_price,
                qty=qty,
                stop_loss=sl,
                tp1_price=tp1,
                trailing_pct=trailing_pct,
                strategy=strategy_name,
                limit_up_price=lu,
            )
```

After:
```python
            self._risk_manager.register_position(
                ticker=ticker,
                entry_price=entry_price,
                qty=qty,
                stop_loss=sl,
                tp1_price=tp1,
                trailing_pct=trailing_pct,
                strategy=strategy_name,
                limit_up_price=lu,
                status="confirmed",
            )
```

- [ ] **Step 3: baseline backtest 회귀 확인**

Run: `python -m pytest tests/test_backtester.py -v`
Expected: 모든 기존 테스트 통과 (status 파라미터는 동작 변경 없음)

- [ ] **Step 4: 커밋**

```bash
git add backtest/backtester.py
git commit -m "refactor: backtester register_position에 status='confirmed' 명시"
```

---

## Task 6: engine_worker 통합 (가장 큰 작업)

**Files:**
- Modify: `gui/workers/engine_worker.py` (7지점)

VIHandler 패턴 따라 sub-step별 commit. 각 step 후 `python -m py_compile gui/workers/engine_worker.py` 또는 `python -m pytest tests/` 회귀 확인.

### 6.1 OrderTracker 인스턴스화 + 모듈 상수

- [ ] **Step 1: 모듈 상단에 WS "00" 필드 상수 추가**

In `gui/workers/engine_worker.py`, add near the top of the file (after existing module-level constants — search for `_WS_FIELD` or similar; if none, place after imports):

```python
# TODO: 키움 WS '00'(주문체결) 메시지 필드 코드는 미검증.
# 실 페이로드 캡처 후 확정 필요. 운영 전 raw 로그 1회 수집 필수.
_WS_FIELD_ORDER_NO = "9001"      # 주문번호 (추정)
_WS_FIELD_FILLED_PRICE = "10"    # 체결가 (추정)
_WS_FIELD_FILLED_QTY = "900"     # 체결량 (추정)
```

- [ ] **Step 2: __init__에 플레이스홀더 추가**

In `gui/workers/engine_worker.py:__init__`, find `self._prev_close: dict[str, float] = {}` (around line 145). Add immediately after the VIHandler instantiation (or alongside it):

Actually `self._vi_handler` is initialized in `_run_engine` per the prior task. Follow the same pattern. In `__init__` add:
```python
        self._order_tracker = None  # _run_engine에서 인스턴스화
        self._timeout_counters: dict[str, int] = {}   # ticker → 연속 TIMEOUT 카운터
        self._limit_up_exit_pending: set[str] = set()  # limit_up_exit submit된 ticker
```

- [ ] **Step 3: _run_engine에서 인스턴스화**

In `gui/workers/engine_worker.py:_run_engine`, find where `self._vi_handler = VIHandler(...)` is constructed. Add immediately after:
```python
        from core.order_tracker import OrderTracker
        self._order_tracker = OrderTracker(
            timeout_seconds=self._config.trading.order_confirmation_timeout_sec,
        )
```

- [ ] **Step 4: 정적 검증**

Run: `python -m py_compile gui/workers/engine_worker.py`
Expected: no output

Run: `python -m pytest tests/ -x --tb=short 2>&1 | tail -5`
Expected: all pass (no integration yet)

- [ ] **Step 5: 커밋**

```bash
git add gui/workers/engine_worker.py
git commit -m "feat: engine_worker에 OrderTracker 인스턴스 + WS 필드 상수"
```

### 6.2 _signal_consumer 매수 통합 (paper_mode 분기)

- [ ] **Step 1: 매수 성공 직후 분기 작성**

In `_signal_consumer`, find `result = await self._order_manager.execute_buy(...)` and the subsequent `if result:` block. Around line 1016 currently (will shift). Read 30 lines around this point first.

Run: `grep -n "self._order_manager.execute_buy" gui/workers/engine_worker.py`

Modify the post-execute_buy block. Currently:
```python
                result = await self._order_manager.execute_buy(
                    ticker=signal.ticker,
                    price=int(signal.price),
                    total_qty=total_qty,
                    strategy=signal.strategy,
                )
                if result:
                    self._risk_manager.register_position(
                        ticker=signal.ticker,
                        entry_price=signal.price,
                        qty=result["qty"],
                        stop_loss=sl,
                        tp1_price=tp1,
                        strategy=signal.strategy or "",
                        limit_up_price=self._limit_up_map.get(signal.ticker),
                    )
                    strategy.on_entry()
                    # ... ATR-DBG 등
```

Change to:
```python
                result = await self._order_manager.execute_buy(
                    ticker=signal.ticker,
                    price=int(signal.price),
                    total_qty=total_qty,
                    strategy=signal.strategy,
                )
                if result:
                    is_paper = getattr(self._config, "paper_mode", True)
                    initial_status = "confirmed" if is_paper else "pending"
                    self._risk_manager.register_position(
                        ticker=signal.ticker,
                        entry_price=signal.price,
                        qty=result["qty"],
                        stop_loss=sl,
                        tp1_price=tp1,
                        strategy=signal.strategy or "",
                        limit_up_price=self._limit_up_map.get(signal.ticker),
                        status=initial_status,
                    )
                    if not is_paper and self._order_tracker is not None:
                        self._order_tracker.submit(
                            order_no=result["order_no"],
                            ticker=signal.ticker,
                            side="buy",
                            qty=result["qty"],
                        )
                        logger.info(
                            f"[ORDER-TRACK] {result['order_no']} SUBMIT "
                            f"{signal.ticker} buy {result['qty']}"
                        )
                    strategy.on_entry()
                    # ... 기존 ATR-DBG 등 변경 없음
```

- [ ] **Step 2: 정적 검증**

Run: `python -m py_compile gui/workers/engine_worker.py`
Expected: no output

- [ ] **Step 3: 커밋**

```bash
git add gui/workers/engine_worker.py
git commit -m "feat: _signal_consumer 매수에 paper_mode 분기 + tracker.submit"
```

### 6.3 _tick_consumer 재진입 가드 + 매도 settle 지연

- [ ] **Step 1: 재진입 가드 삽입**

In `_tick_consumer`, find the block after `self._latest_prices[ticker] = price` and after the `_vi_handler.update_from_tick` call (added in prior task). Around line 745.

Add the re-entry guard BEFORE the `pos = self._risk_manager.get_position(ticker)` line OR after it but before `check_limit_up` / `check_stop_loss`. Choose the latter to keep position retrieval available.

Find this section:
```python
                pos = self._risk_manager.get_position(ticker)
                if pos is None or pos["remaining_qty"] <= 0:
                    continue
                # 상한가 즉시 청산 (stop_loss 체크 전, 최우선)
                if self._risk_manager.check_limit_up(ticker, price):
```

Insert between `if pos is None or pos["remaining_qty"] <= 0: continue` and `# 상한가 즉시 청산` the following guard:
```python
                # 주문 진행 중이면 highest_price만 갱신, exit 스킵 (재진입 가드)
                if self._order_tracker is not None:
                    _pending = self._order_tracker.get_pending(ticker)
                    if _pending is not None:
                        if pos.get("highest_price", 0) < price:
                            pos["highest_price"] = price
                        logger.debug(
                            f"[ORDER-TRACK] {ticker} pending {_pending.side} — exit 스킵"
                        )
                        continue
```

- [ ] **Step 2: limit_up_exit 매도 후 settle_sell 지연**

Find the limit_up_exit branch (search for `exit_reason="limit_up_exit"`). The current structure is:
```python
                    result = await self._order_manager.execute_sell_stop(
                        ticker=ticker, qty=qty, price=int(price),
                        strategy=strategy_name, pnl=pnl, pnl_pct=pnl_pct,
                        exit_reason="limit_up_exit",
                    )
                    if result is not None:
                        self._risk_manager.settle_sell(ticker, price, qty)
                        # ... pnl 카운터, signal emit, on_exit
                        continue
                    else:
                        # 체결 실패 → stop을 상한가 × floor_pct 로 상향
                        new_stop = self._risk_manager.raise_stop_to_limit_up_floor(ticker)
                        logger.warning(...)
                        # fall-through: 이후 기존 stop_loss/trailing 로직이 처리
```

Modify the success branch to defer `settle_sell` in real_mode:
```python
                    result = await self._order_manager.execute_sell_stop(
                        ticker=ticker, qty=qty, price=int(price),
                        strategy=strategy_name, pnl=pnl, pnl_pct=pnl_pct,
                        exit_reason="limit_up_exit",
                    )
                    if result is not None:
                        is_paper = getattr(self._config, "paper_mode", True)
                        if is_paper:
                            # 페이퍼: 즉시 settle (현 동작)
                            self._risk_manager.settle_sell(ticker, price, qty)
                            if pnl >= 0:
                                self._rt_wins += 1
                            else:
                                self._rt_losses += 1
                            logger.info(
                                f"limit_up_exit 실행: {ticker} {qty}주 @ {price:,} "
                                f"PnL={pnl:+,.0f}"
                            )
                            strat_info = self._active_strategies.get(ticker)
                            if strat_info:
                                strat_info["strategy"].on_exit()
                            self.signals.trade_executed.emit({
                                "time": datetime.now().strftime("%H:%M:%S"),
                                "side": "sell", "ticker": ticker,
                                "price": int(price), "qty": qty,
                                "pnl": int(pnl), "reason": "limit_up_exit",
                            })
                        else:
                            # real_mode: tracker에 등록, 체결 확인 후 _handle_fill에서 settle
                            self._order_tracker.submit(
                                result["order_no"], ticker, "sell", qty,
                            )
                            self._limit_up_exit_pending.add(ticker)
                            logger.info(
                                f"[ORDER-TRACK] {result['order_no']} SUBMIT "
                                f"{ticker} sell {qty} (limit_up_exit)"
                            )
                        continue
                    else:
                        # 체결 실패 → stop을 상한가 × floor_pct 로 상향 (기존 동작)
                        new_stop = self._risk_manager.raise_stop_to_limit_up_floor(ticker)
                        logger.warning(
                            f"limit_up_exit 실패 → stop 상향: {ticker} "
                            f"new_stop={new_stop:,.0f}"
                        )
```

- [ ] **Step 3: stop_loss / trailing / breakeven 매도 settle 지연**

Find the stop_loss branch (`if self._risk_manager.check_stop_loss(ticker, price):`). The current structure includes after `execute_sell_stop` call:
```python
                    await self._order_manager.execute_sell_stop(
                        ticker=ticker, qty=qty, price=int(price),
                        strategy=strategy_name, pnl=pnl, pnl_pct=pnl_pct,
                        exit_reason=reason_code,
                        prefer_best_limit=prefer_best,
                        on_rejection=lambda tk, rt: self._vi_handler.flag_suspected(tk, f"주문 거부 (rt_cd={rt})"),
                    )
                    self._risk_manager.settle_sell(ticker, price, qty)
                    if pnl >= 0: ...
```

Capture the return value and branch:
```python
                    result = await self._order_manager.execute_sell_stop(
                        ticker=ticker, qty=qty, price=int(price),
                        strategy=strategy_name, pnl=pnl, pnl_pct=pnl_pct,
                        exit_reason=reason_code,
                        prefer_best_limit=prefer_best,
                        on_rejection=lambda tk, rt: self._vi_handler.flag_suspected(tk, f"주문 거부 (rt_cd={rt})"),
                    )
                    if result is None:
                        continue  # 주문 자체 실패 (VI 등)
                    is_paper = getattr(self._config, "paper_mode", True)
                    if is_paper:
                        self._risk_manager.settle_sell(ticker, price, qty)
                        if pnl >= 0:
                            self._rt_wins += 1
                        else:
                            self._rt_losses += 1
                        logger.info(
                            f"{reason_code} 실행: {ticker} {qty}주 @ {price:,} PnL={pnl:+,.0f}"
                        )
                        strat_info = self._active_strategies.get(ticker)
                        if strat_info:
                            strat_info["strategy"].on_exit()
                        self.signals.trade_executed.emit({
                            "time": datetime.now().strftime("%H:%M:%S"),
                            "side": "sell", "ticker": ticker,
                            "price": int(price), "qty": qty,
                            "pnl": int(pnl), "reason": reason_code,
                        })
                    else:
                        self._order_tracker.submit(
                            result["order_no"], ticker, "sell", qty,
                        )
                        logger.info(
                            f"[ORDER-TRACK] {result['order_no']} SUBMIT {ticker} sell {qty} "
                            f"({reason_code})"
                        )
                    continue
```

Note: The original code didn't capture `result` for non-limit_up sells. Verify by reading the current `_tick_consumer` body. If the existing code already has `continue` after settle_sell, preserve that flow.

- [ ] **Step 4: 정적 검증**

Run: `python -m py_compile gui/workers/engine_worker.py`
Expected: no output

Run: `python -m pytest tests/ -x --tb=short 2>&1 | tail -5`
Expected: all pass (real_mode path untested but paper_mode regression confirmed)

- [ ] **Step 5: 커밋**

```bash
git add gui/workers/engine_worker.py
git commit -m "feat: _tick_consumer 재진입 가드 + 매도 settle 지연 (real_mode)

- get_pending(ticker) 비None 시 highest_price만 갱신 + exit 스킵
- limit_up_exit: real_mode에서 tracker.submit + _limit_up_exit_pending.add
- stop_loss/trailing/breakeven: real_mode에서 tracker.submit만, settle은
  _handle_fill에서 지연 호출
- paper_mode 경로는 변경 없음 (즉시 settle 유지)"
```

### 6.4 _order_confirmation_consumer 본문 + _handle_fill 헬퍼

- [ ] **Step 1: _handle_fill 헬퍼 작성**

In `gui/workers/engine_worker.py`, find `_order_confirmation_consumer` (around line 1092). Add a new helper method immediately above it:

```python
    async def _handle_fill(self, order_no: str) -> None:
        """FILLED 상태 도달 시 risk_manager 상태 갱신 + 알림 emit.

        매수: mark_confirmed
        매도: settle_sell + trade_executed emit
        공통: _timeout_counters 리셋 + _limit_up_exit_pending 정리
        """
        if self._order_tracker is None:
            return
        order = self._order_tracker.get_by_order_no(order_no)
        if order is None:
            logger.warning(f"[ORDER-TRACK] _handle_fill {order_no} 알 수 없음")
            return
        ticker = order.ticker
        # limit_up_exit 추적 set 정리 (FILLED 시점)
        self._limit_up_exit_pending.discard(ticker)
        # 연속 TIMEOUT 카운터 리셋
        self._timeout_counters[ticker] = 0
        if order.side == "buy":
            self._risk_manager.mark_confirmed(ticker)
            logger.info(
                f"[ORDER-TRACK] {order_no} FILLED → mark_confirmed {ticker}"
            )
        elif order.side == "sell":
            pos = self._risk_manager.get_position(ticker)
            entry = pos.get("entry_price", 0) if pos else 0
            pnl = (order.filled_price - entry) * order.filled_qty if entry > 0 else 0
            pnl_pct = ((order.filled_price / entry) - 1) if entry > 0 else 0
            self._risk_manager.settle_sell(
                ticker, order.filled_price, order.filled_qty,
            )
            if pnl >= 0:
                self._rt_wins += 1
            else:
                self._rt_losses += 1
            logger.info(
                f"[ORDER-TRACK] {order_no} FILLED → settle_sell {ticker} "
                f"@ {order.filled_price:,.0f} PnL={pnl:+,.0f}"
            )
            strat_info = self._active_strategies.get(ticker)
            if strat_info:
                strat_info["strategy"].on_exit()
            self.signals.trade_executed.emit({
                "time": datetime.now().strftime("%H:%M:%S"),
                "side": "sell", "ticker": ticker,
                "price": int(order.filled_price), "qty": order.filled_qty,
                "pnl": int(pnl), "reason": "ws_filled",
            })
```

- [ ] **Step 2: _order_confirmation_consumer 본문 교체**

Find the existing `_order_confirmation_consumer` body (current single-line `logger.info(f"체결통보: {exec_data}")`). Replace the entire method body:

```python
    async def _order_confirmation_consumer(self):
        """WS '00' 체결통보 → OrderTracker 갱신 → FILLED 시 _handle_fill."""
        from core.order_tracker import OrderStatus
        while self._running and not self._stop_event.is_set():
            try:
                exec_data = await asyncio.wait_for(
                    self._order_queue.get(), timeout=0.5,
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            try:
                if self._order_tracker is None:
                    logger.debug(f"[ORDER-TRACK] tracker 미초기화 — skip: {exec_data}")
                    continue
                values = exec_data.get("values", {})
                order_no = str(values.get(_WS_FIELD_ORDER_NO, ""))
                filled_qty = abs(int(values.get(_WS_FIELD_FILLED_QTY, 0) or 0))
                filled_price = abs(float(values.get(_WS_FIELD_FILLED_PRICE, 0) or 0))
                if not order_no or filled_qty == 0:
                    logger.warning(
                        f"[ORDER-TRACK] 무효 체결 메시지 무시: order_no={order_no} qty={filled_qty}"
                    )
                    continue
                updated = self._order_tracker.on_fill(
                    order_no, filled_qty, filled_price,
                )
                if updated is None:
                    continue  # 알 수 없는 주문 (on_fill에서 이미 warning 로그)
                logger.info(
                    f"[ORDER-TRACK] {order_no} FILL "
                    f"{updated.filled_qty}/{updated.requested_qty} "
                    f"@ {filled_price:,.0f} (status={updated.status.value})"
                )
                if updated.status == OrderStatus.FILLED:
                    await self._handle_fill(order_no)
            except Exception as e:
                logger.error(f"[ORDER-TRACK] _order_confirmation_consumer 오류: {e}")
```

- [ ] **Step 3: 정적 검증**

Run: `python -m py_compile gui/workers/engine_worker.py`
Expected: no output

Run: `python -m pytest tests/ -x --tb=short 2>&1 | tail -5`
Expected: all pass

- [ ] **Step 4: 커밋**

```bash
git add gui/workers/engine_worker.py
git commit -m "feat: _order_confirmation_consumer 본문 + _handle_fill 헬퍼

- WS '00' 메시지에서 order_no/filled_qty/filled_price 파싱
- OrderTracker.on_fill 호출, FILLED 시 _handle_fill로 분기
- 매수: mark_confirmed / 매도: settle_sell + trade_executed emit
- _limit_up_exit_pending과 _timeout_counters는 FILLED 시점에 정리"
```

### 6.5 _order_tracker_timeout_checker background task

- [ ] **Step 1: _verify_fill_via_rest 헬퍼 작성**

Add new helper method in `engine_worker.py` (place before `_order_tracker_timeout_checker`):

```python
    async def _verify_fill_via_rest(self, order) -> dict | None:
        """REST ka10070 잔고 폴백 1회. 체결 확인 시 {qty, price} 반환.

        잔고에서 해당 ticker의 보유 수량 변화로 체결 여부 추론. 정밀한 매핑이
        불가능하므로 보수적으로 'qty가 requested_qty 이상이면 체결됐다' 판정.
        실 페이로드 캡처 후 ka10070 응답 구조 확정 시 정교화.
        """
        try:
            raw = await self._rest_client.get_account_balance()
        except Exception as e:
            logger.error(f"[ORDER-TRACK] ka10070 폴백 실패: {e}")
            return None
        # TODO: 실 응답 구조 확정 필요. 현재는 보수적으로 None 반환 (TIMEOUT 처리 유도)
        # ka10070 응답이 output 리스트에 {stk_cd, hldn_qty, avg_pric} 형태일 것으로 추정
        items = (raw or {}).get("output", []) or (raw or {}).get("output1", [])
        if not isinstance(items, list):
            return None
        for item in items:
            if str(item.get("stk_cd", "")).strip() == order.ticker:
                try:
                    qty = abs(int(item.get("hldn_qty", 0) or 0))
                    price = abs(float(item.get("avg_pric", 0) or 0))
                except (ValueError, TypeError):
                    return None
                # 보수적 판정: 매수 시 qty>=requested, 매도 시 qty<=0 (보유 소진)
                if order.side == "buy" and qty >= order.requested_qty:
                    return {"qty": order.requested_qty, "price": price}
                if order.side == "sell" and qty == 0:
                    return {"qty": order.requested_qty, "price": price}
        return None
```

- [ ] **Step 2: _order_tracker_timeout_checker 메서드 작성**

Add new method:
```python
    async def _order_tracker_timeout_checker(self):
        """1초 주기 타임아웃 감지 + REST 폴백 + cancel/알림."""
        while self._running and not self._stop_event.is_set():
            try:
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            try:
                if self._order_tracker is None:
                    continue
                timeout_sec = self._config.trading.order_confirmation_timeout_sec
                stale = self._order_tracker.get_unfilled_older_than(timeout_sec)
                for order in stale:
                    logger.warning(
                        f"[ORDER-TRACK] {order.order_no} TIMEOUT — REST 폴백"
                    )
                    confirmed = await self._verify_fill_via_rest(order)
                    if confirmed is not None:
                        self._order_tracker.on_fill(
                            order.order_no,
                            confirmed["qty"],
                            confirmed["price"],
                        )
                        # FILLED 도달 시 _handle_fill (on_fill 이후 상태 확인)
                        updated = self._order_tracker.get_by_order_no(order.order_no)
                        from core.order_tracker import OrderStatus
                        if updated and updated.status == OrderStatus.FILLED:
                            await self._handle_fill(order.order_no)
                    else:
                        # 미체결 확정
                        self._order_tracker.mark_timeout(order.order_no)
                        # limit_up_exit 정리 (자연 재시도 경로 — 필수)
                        if order.ticker in self._limit_up_exit_pending:
                            self._limit_up_exit_pending.discard(order.ticker)
                            new_stop = self._risk_manager.raise_stop_to_limit_up_floor(
                                order.ticker
                            )
                            logger.warning(
                                f"[ORDER-TRACK] limit_up_exit TIMEOUT → stop 상향: "
                                f"{order.ticker} new_stop={new_stop:,.0f}"
                            )
                        # 매수 TIMEOUT: 취소 시도
                        if order.side == "buy":
                            try:
                                await self._rest_client.cancel_order(
                                    order.order_no, order.ticker, order.requested_qty,
                                )
                            except Exception as e:
                                logger.error(
                                    f"[ORDER-TRACK] cancel_order 실패 "
                                    f"{order.order_no}: {e}"
                                )
                        # 연속 TIMEOUT 카운터
                        self._timeout_counters[order.ticker] = (
                            self._timeout_counters.get(order.ticker, 0) + 1
                        )
                        if self._notifier:
                            self._notifier.send_urgent(
                                f"[ORDER-TRACK] {order.ticker} {order.side} TIMEOUT "
                                f"({order.order_no})"
                            )
                        threshold = self._config.trading.order_timeout_consecutive_threshold
                        if self._timeout_counters[order.ticker] >= threshold and self._notifier:
                            self._notifier.send_urgent(
                                f"[ORDER-TRACK][CRITICAL] {order.ticker} 연속 TIMEOUT "
                                f"{self._timeout_counters[order.ticker]}회"
                            )
            except Exception as e:
                logger.error(f"[ORDER-TRACK] timeout_checker 오류: {e}")
```

- [ ] **Step 3: _run_engine task 리스트에 등록**

In `_run_engine`, find the existing `asyncio.create_task(...)` lines (search for `_tick_consumer`, `_signal_consumer`). Add new line alongside them:
```python
            asyncio.create_task(
                self._order_tracker_timeout_checker(),
                name="order_timeout_checker",
            ),
```

Also update the task health monitor name map (search for `"tick_consumer": "_tick_consumer"` in the file around line 2069). Add:
```python
        "order_timeout_checker": "_order_tracker_timeout_checker",
```

- [ ] **Step 4: 정적 검증 + 회귀**

Run: `python -m py_compile gui/workers/engine_worker.py`
Expected: no output

Run: `python -m pytest tests/ -x --tb=short 2>&1 | tail -5`
Expected: all pass

- [ ] **Step 5: 커밋**

```bash
git add gui/workers/engine_worker.py
git commit -m "feat: _order_tracker_timeout_checker + _verify_fill_via_rest 헬퍼

- 1초 주기 PendingOrder 타임아웃 감지
- REST ka10070 폴백 1회 → 체결 확인 시 on_fill + _handle_fill
- 미체결 시 mark_timeout → _limit_up_exit_pending 정리 (raise_stop) →
  매수면 cancel_order → 텔레그램 알림 + 연속 임계 추가 경고
- 백그라운드 task 등록 + health monitor 매핑"
```

### 6.6 _force_close 통합

- [ ] **Step 1: _force_close 매도 settle 지연**

In `_force_close` (around line 1374), find the call:
```python
                    prefer_best = self._vi_handler.should_use_best_limit(ticker)
                    await self._order_manager.execute_sell_force_close(
                        ticker=ticker, qty=qty, price=close_price,
                        strategy=strategy_name, pnl=pnl, pnl_pct=pnl_pct,
                        exit_reason="forced_close",
                        prefer_best_limit=prefer_best,
                        on_rejection=lambda tk, rt: self._vi_handler.flag_suspected(tk, f"주문 거부 (rt_cd={rt})"),
                    )
                    self._risk_manager.settle_sell(ticker, float(close_price), qty)
                    strat_info = self._active_strategies.get(ticker)
                    if strat_info:
                        strat_info["strategy"].on_exit()
```

Modify to capture result + paper_mode branch:
```python
                    prefer_best = self._vi_handler.should_use_best_limit(ticker)
                    result = await self._order_manager.execute_sell_force_close(
                        ticker=ticker, qty=qty, price=close_price,
                        strategy=strategy_name, pnl=pnl, pnl_pct=pnl_pct,
                        exit_reason="forced_close",
                        prefer_best_limit=prefer_best,
                        on_rejection=lambda tk, rt: self._vi_handler.flag_suspected(tk, f"주문 거부 (rt_cd={rt})"),
                    )
                    if result is None:
                        logger.error(f"[ORDER-TRACK] force_close 주문 실패: {ticker}")
                        continue
                    is_paper = getattr(self._config, "paper_mode", True)
                    if is_paper:
                        self._risk_manager.settle_sell(ticker, float(close_price), qty)
                        strat_info = self._active_strategies.get(ticker)
                        if strat_info:
                            strat_info["strategy"].on_exit()
                    else:
                        self._order_tracker.submit(
                            result["order_no"], ticker, "sell", qty,
                        )
                        logger.info(
                            f"[ORDER-TRACK] {result['order_no']} SUBMIT "
                            f"{ticker} sell {qty} (forced_close)"
                        )
                        # forced_close은 다음 _handle_fill에서 settle (정상 흐름)
```

Note: The original `for ticker, pos in list(...)` loop has cleanup after (save_daily_summary, reset_daily). Those happen AFTER the loop and operate on aggregate state, so are not affected by the per-position settle deferral. real_mode forced_close 미체결분은 다음 _handle_fill 또는 다음날 force_close까지 보류.

- [ ] **Step 2: 정적 검증 + 회귀**

Run: `python -m py_compile gui/workers/engine_worker.py`
Expected: no output

Run: `python -m pytest tests/ -x --tb=short 2>&1 | tail -5`
Expected: all pass

- [ ] **Step 3: 커밋**

```bash
git add gui/workers/engine_worker.py
git commit -m "feat: _force_close에 paper_mode 분기 + tracker.submit

real_mode에서 15:10 강제청산도 settle을 _handle_fill로 지연.
타임아웃 시 _order_tracker_timeout_checker가 동일 메커니즘으로 처리."
```

---

## Task 7: 통합 테스트 (test_engine_worker_order_tracking.py)

**Files:**
- Create: `tests/test_engine_worker_order_tracking.py`

VIHandler 통합 테스트 패턴 따라 EngineWorker 부팅 없이 OrderTracker + risk_manager 모의로 시나리오 검증.

### 7.1 통합 시나리오 테스트 작성

- [ ] **Step 1: 테스트 파일 작성**

Create `tests/test_engine_worker_order_tracking.py`:
```python
"""tests/test_engine_worker_order_tracking.py — engine_worker × OrderTracker 통합 시나리오.

EngineWorker 전체 부팅을 피하기 위해 OrderTracker + risk_manager 직접 호출로
통합 지점의 시나리오를 검증. 실 통합은 engine_worker.py의 코드 수정으로 보장.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from core.order_tracker import OrderStatus, OrderTracker


def _tracker() -> OrderTracker:
    return OrderTracker(timeout_seconds=10.0)


class TestBuyPipeline:
    def test_real_mode_buy_submit_then_filled(self, tmp_path):
        """real_mode 매수 시나리오: submit → on_fill → mark_confirmed 가능."""
        from config.settings import TradingConfig
        from data.db_manager import DbManager
        from risk.risk_manager import RiskManager

        db = DbManager(str(tmp_path / "t.db"))
        asyncio.run(db.init())
        rm = RiskManager(db=db, config=TradingConfig())
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
        """paper_mode 시나리오: tracker 미사용, register_position(status=confirmed) 즉시."""
        from config.settings import TradingConfig
        from data.db_manager import DbManager
        from risk.risk_manager import RiskManager

        db = DbManager(str(tmp_path / "t.db"))
        asyncio.run(db.init())
        rm = RiskManager(db=db, config=TradingConfig())
        rm.register_position(
            ticker="000001", entry_price=10000, qty=10, stop_loss=9200,
            status="confirmed",
        )
        assert rm.get_position("000001")["status"] == "confirmed"
        # tracker는 사용하지 않으므로 get_pending이 호출되지 않음 — 본 테스트는 의도 확인


class TestSellPipeline:
    def test_real_mode_sell_settle_deferred(self, tmp_path):
        """real_mode 매도: submit 후 settle_sell 호출 안 됨, on_fill 후에만 settle."""
        from config.settings import TradingConfig
        from data.db_manager import DbManager
        from risk.risk_manager import RiskManager

        db = DbManager(str(tmp_path / "t.db"))
        asyncio.run(db.init())
        rm = RiskManager(db=db, config=TradingConfig())
        t = _tracker()

        rm.register_position(
            ticker="000001", entry_price=10000, qty=10, stop_loss=9200,
            status="confirmed",
        )
        pos_before = rm.get_position("000001")
        assert pos_before["remaining_qty"] == 10
        # 매도 submit
        t.submit("ORD2", "000001", "sell", 10)
        # 시뮬: engine_worker가 settle_sell을 호출 안 함 → remaining_qty 불변
        assert rm.get_position("000001")["remaining_qty"] == 10
        # on_fill 시점에 settle_sell 호출 (실 코드는 _handle_fill)
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
```

- [ ] **Step 2: 테스트 실행**

Run: `python -m pytest tests/test_engine_worker_order_tracking.py -v`
Expected: 8 passed (또는 그 이상)

- [ ] **Step 3: 커밋**

```bash
git add tests/test_engine_worker_order_tracking.py
git commit -m "test: engine_worker x OrderTracker 통합 시나리오"
```

---

## Task 8: 최종 검증 + CLAUDE.md 갱신

**Files:** (검증만)
- Modify: `CLAUDE.md` (한 줄 추가)

### 8.1 전체 회귀 검증

- [ ] **Step 1: 전체 pytest**

Run: `python -m pytest tests/ -v --tb=short 2>&1 | tail -10`
Expected: 모두 통과. 신규 테스트 약 28건 (10 tracker + 4 risk_manager + 1 settings + 6 engine integration + 7기존 vi_handler / settings는 변경 없음).

총 약 304건 통과 예상 (이전 276 + 새 ~28).

- [ ] **Step 2: selftest**

Run: `python selftest.py`
Expected: `7 / 7`

- [ ] **Step 3: limit_up_exit 가드 회귀 (ADR-018)**

Run: `grep -B2 -A8 'exit_reason="limit_up_exit"' gui/workers/engine_worker.py | grep -c "prefer_best_limit"`
Expected: `0` — limit_up_exit는 여전히 시장가 강제 (VI 무관 + tracker 통합 후에도 보호 유지)

- [ ] **Step 4: _limit_up_exit_pending 정리 회귀 (사용자 핵심 주의사항)**

이 set은 반드시 3 경로에서 모두 discard:
- `_handle_fill` (FILLED 시): set.discard(ticker)
- `_order_tracker_timeout_checker` (TIMEOUT 시): set.discard(ticker)
- (`mark_failed` 처리는 plan에서 별도 코드 없음 — 향후 필요 시 추가)

Run: `grep -n "_limit_up_exit_pending.discard\|_limit_up_exit_pending.add" gui/workers/engine_worker.py`
Expected: 최소 3건 (add 1건 limit_up_exit 분기 + discard 2건: _handle_fill + timeout_checker)

- [ ] **Step 5: 베이스라인 backtest 회귀**

Run: `python scripts/baseline_pf_limit_up.py 2>&1 | tail -20`
Expected: `PF : 4.363` (or `4.36`), `총 거래: 248`, `limit_up_exit ... 15건`. 변동 시 backtester에 tracker 흔적이 누출됐는지 검토.

- [ ] **Step 6: paper_mode 안전 검증**

Run: `python -c "import yaml; cfg = yaml.safe_load(open('config.yaml', encoding='utf-8')); print('paper_mode =', cfg.get('paper_mode'))"`
Expected: `paper_mode = True` — 현재 운영 모드 확인. 이 모드에서는 tracker가 사용되지 않으므로 운영 영향 없음.

- [ ] **Step 7: CLAUDE.md 갱신**

In `CLAUDE.md`, find `재조립 진행 상태 — 전 Phase 완결` section. After the line:
```
- [x] VI 휴리스틱 대응 (2026-05-12) — 시장가 → 최유리지정가 자동 전환, VI 활성 종목 매수 차단. limit_up_exit / forced_close 보호. 백테스트 baseline PF 4.36 변동 없음.
```

Add:
```
- [x] Order Confirmation Pipeline (2026-05-12) — real_mode WS '00' 체결통보까지 settle_sell 보류. OrderTracker 재진입 가드. paper_mode/backtester 영향 없음. 백테스트 baseline PF 4.36 변동 없음.
```

- [ ] **Step 8: 최종 커밋**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md에 Order Confirmation Pipeline 추가

real_mode 주문 접수와 체결 분리. paper_mode 운영 중에는 동작 변경 없음.
WS '00' 필드 코드는 미검증 TODO — 실모드 전환 전 raw 페이로드 캡처 필요."
```

---

## Spec-Plan Coverage

| Spec 섹션 | 구현 Task |
|-----------|-----------|
| §5.1 OrderTracker 클래스 + enum + dataclass | Task 1.1, 1.2, 1.3 |
| §5.1.1 submit (중복 무시) | Task 1.2 (TestSubmit) |
| §5.1.2 on_fill (VWAP, 상태 전이) | Task 1.2 (TestOnFill) |
| §5.1.3 get_pending 재진입 가드 | Task 1.2 (TestQueries) |
| §5.1.4 get_unfilled_older_than | Task 1.2 (TestQueries) |
| §5.1.5 mark_failed / mark_timeout / remove | Task 1.3 (TestMarkStates) |
| §5.2 config 신규 키 | Task 2 |
| §5.3 register_position status + mark_confirmed | Task 3 |
| §5.4 cancel_order 메서드 | Task 4 |
| §5.5.1 __init__ + 모듈 상수 | Task 6.1 |
| §5.5.2 _run_engine task 등록 | Task 6.5 Step 3 |
| §5.5.3 _order_tracker_timeout_checker | Task 6.5 |
| §5.5.4 _signal_consumer 매수 | Task 6.2 |
| §5.5.5 _tick_consumer 재진입 가드 + 매도 지연 | Task 6.3 |
| §5.5.5 _limit_up_exit_pending (FILLED + TIMEOUT 정리) | Task 6.4 (_handle_fill) + Task 6.5 (timeout_checker) |
| §5.5.6 _order_confirmation_consumer 본문 | Task 6.4 |
| §5.5.7 _force_close 통합 | Task 6.6 |
| §5.6 backtester status="confirmed" | Task 5 |
| §5.7 PaperOrderManager 무변경 | (의도적 비구현 — 변경 0건) |
| §6 로깅 규약 | Task 6.* 본문 + Task 8 Step 3-4 검증 |
| §7.1 단위 테스트 10건 | Task 1.2 + 1.3 (실제 16건으로 분할) |
| §7.2 통합 테스트 6건 | Task 7 (실제 8건으로 분할) |
| §7.3 회귀 (risk_manager) | Task 3.1 |
| §8 에러 처리 | 각 Task의 try/except + Task 8 검증 |
| §9 위험 | 각 Task의 코드 주석 + Task 8 baseline 회귀 |
| §10 변경 파일 목록 | 전체 Task |
| §12 명시적 금지 | Task 8 Step 3 (limit_up_exit 가드) + Task 5 (backtester 명시) |

모든 spec 섹션이 task로 매핑됨. PaperOrderManager는 의도적 비구현으로 변경 0건 보장 (Task 8 Step 6 paper_mode 확인).

**사용자 핵심 주의사항 (`_limit_up_exit_pending` FILLED + TIMEOUT 양쪽 정리)** 명시적 검증: Task 8 Step 4 grep + Task 7 `test_limit_up_exit_pending_lifecycle` 단위 테스트.
