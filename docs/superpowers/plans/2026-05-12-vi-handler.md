# VI Handler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 가격 휴리스틱 기반 VI(변동성완화장치) 추정 + REST 주문 거부 기반 SUSPECTED 활성화 → 시장가 매도 주문을 키움 "06"(최유리지정가)로 자동 전환하고, VI 활성 종목 신규 매수를 차단한다. `limit_up_exit`(ADR-018) / `forced_close`는 보호 대상.

**Architecture:** 신규 무상태 인메모리 모듈 `core/vi_handler.py`(`VIHandler` 클래스, lazy 만료). OrderManager에 `prefer_best_limit`/`on_rejection` 파라미터를 패스스루로 추가. engine_worker가 단일 통합 지점에서 (a) `_tick_consumer`에서 휴리스틱 업데이트 + stop_loss 시 prefer_best_limit 전달, (b) `_signal_consumer`에서 매수 차단, (c) `_force_close`에서 prefer_best_limit 전달. risk_manager는 변경 없음.

**Tech Stack:** Python 3.14, asyncio, dataclasses/enum, pytest + pytest-asyncio, loguru, 기존 키움 REST 코드("00"/"03"/"06").

**Spec:** `docs/superpowers/specs/2026-05-12-vi-handler-design.md`

---

## File Structure

신규:
- `core/vi_handler.py` — VIState enum + VIHandler 클래스 (단일 책임: VI 상태 관리)
- `tests/test_vi_handler.py` — 단위 테스트
- `tests/test_engine_worker_vi.py` — engine_worker 통합 테스트

수정:
- `config.yaml` — trading 섹션에 3개 키 추가
- `config/settings.py` — `TradingConfig` 3개 필드 추가
- `core/kiwoom_rest.py` — `PRICE_BEST_LIMIT = "06"` 상수
- `core/order_manager.py` — `_send_order`/`execute_sell_stop`/`execute_sell_force_close` 시그니처 확장 (`prefer_best_limit`, `on_rejection`)
- `gui/workers/engine_worker.py` — VIHandler 인스턴스화 + 4개 지점 통합 (line 145의 `self._prev_close` 재사용)
- `tests/test_order_manager.py` — best_limit 전환 + rejection 콜백 케이스 추가
- `tests/test_settings.py` — VI 설정 필드 단위 테스트

기존 `self._prev_close`(engine_worker.py:145)는 이미 line 1561에서 채워지므로 별도 캐시 신설하지 않음.

---

## Task 1: VIHandler 코어 모듈 + 단위 테스트

**Files:**
- Create: `core/vi_handler.py`
- Create: `tests/test_vi_handler.py`

VIHandler는 무상태(인메모리)이며 외부 의존성이 없으므로 먼저 단위 테스트로 완성한 뒤 통합한다. TDD 순서: 실패 테스트 → 최소 구현 → 통과 확인.

### 1.1 빈 모듈 + import 가능 확인

- [ ] **Step 1: 실패 테스트 작성 — import**

Create `tests/test_vi_handler.py`:
```python
"""tests/test_vi_handler.py — VIHandler 단위 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest


def test_import_vi_handler():
    """모듈/클래스/enum이 import 가능."""
    from core.vi_handler import VIHandler, VIState
    assert VIState.NORMAL.value == "normal"
    assert VIState.STATIC_VI.value == "static_vi"
    assert VIState.SUSPECTED.value == "suspected"
```

- [ ] **Step 2: 테스트 실행하여 실패 확인**

Run: `pytest tests/test_vi_handler.py::test_import_vi_handler -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.vi_handler'`

- [ ] **Step 3: 최소 모듈 작성**

Create `core/vi_handler.py`:
```python
"""core/vi_handler.py — VI(변동성완화장치) 휴리스틱 감지 및 주문 전환 의사결정.

가격 휴리스틱(전일종가 대비 ±static_pct 이상)으로 정적VI 발동을 추정하고,
REST 주문 거부(rt_cd ≠ "0")로 SUSPECTED 상태를 활성화한다. 무상태 인메모리.

스펙: docs/superpowers/specs/2026-05-12-vi-handler-design.md
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from loguru import logger


class VIState(Enum):
    NORMAL = "normal"
    STATIC_VI = "static_vi"
    SUSPECTED = "suspected"


@dataclass
class _Entry:
    state: VIState
    expires_at: datetime


class VIHandler:
    """VI 발동 추정 + 주문 전환 의사결정 (인메모리)."""

    def __init__(
        self,
        static_pct: float = 0.095,
        assumed_duration_sec: int = 150,
        suspected_duration_sec: int = 60,
    ):
        self._static_pct = static_pct
        self._assumed_duration = timedelta(seconds=assumed_duration_sec)
        self._suspected_duration = timedelta(seconds=suspected_duration_sec)
        self._entries: dict[str, _Entry] = {}

    def get_vi_state(self, ticker: str) -> VIState:
        entry = self._entries.get(ticker)
        if entry is None:
            return VIState.NORMAL
        if datetime.now() >= entry.expires_at:
            logger.debug(f"[VI] {ticker} 만료 → NORMAL")
            del self._entries[ticker]
            return VIState.NORMAL
        return entry.state

    def is_vi_active(self, ticker: str) -> bool:
        return self.get_vi_state(ticker) != VIState.NORMAL

    def should_use_best_limit(self, ticker: str) -> bool:
        return self.get_vi_state(ticker) != VIState.NORMAL

    def update_from_tick(self, ticker: str, price: float, prev_close: float) -> None:
        if prev_close <= 0 or price <= 0:
            return
        limit_up_price = prev_close * 1.30
        if price >= limit_up_price * 0.99:
            return
        change_pct = (price - prev_close) / prev_close
        if abs(change_pct) >= self._static_pct:
            expires = datetime.now() + self._assumed_duration
            self._entries[ticker] = _Entry(VIState.STATIC_VI, expires)
            logger.info(
                f"[VI] {ticker} STATIC 추정 — change={change_pct * 100:+.2f}%, "
                f"expires_at={expires:%H:%M:%S}"
            )

    def update_from_ws_0a(self, ticker: str, payload: dict) -> None:
        """TODO: 키움 WS '0A'(기세) 메시지의 VI 발동 필드 확정 후 구현.
        실제 페이로드 샘플 수집 → 단위 테스트 추가 → 본문 작성."""
        pass

    def flag_suspected(self, ticker: str, reason: str) -> None:
        expires = datetime.now() + self._suspected_duration
        self._entries[ticker] = _Entry(VIState.SUSPECTED, expires)
        logger.warning(f"[VI] {ticker} SUSPECTED — {reason}")
```

- [ ] **Step 4: 테스트 실행하여 통과 확인**

Run: `pytest tests/test_vi_handler.py::test_import_vi_handler -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add core/vi_handler.py tests/test_vi_handler.py
git commit -m "feat: core/vi_handler.py 스켈레톤 + VIState enum"
```

### 1.2 update_from_tick 휴리스틱 단위 테스트

- [ ] **Step 1: 실패 테스트 작성 — 임계값 +/-9.5% 경계 + 상한가 보호**

Append to `tests/test_vi_handler.py`:
```python
def _fresh_handler(**overrides) -> "VIHandler":
    from core.vi_handler import VIHandler
    defaults = {
        "static_pct": 0.095,
        "assumed_duration_sec": 150,
        "suspected_duration_sec": 60,
    }
    defaults.update(overrides)
    return VIHandler(**defaults)


class TestUpdateFromTick:
    def test_above_threshold_activates_static_vi(self):
        """+9.5% 도달 → STATIC_VI 추정."""
        from core.vi_handler import VIState
        h = _fresh_handler()
        h.update_from_tick("000001", price=10950, prev_close=10000)  # +9.50%
        assert h.get_vi_state("000001") == VIState.STATIC_VI

    def test_below_threshold_stays_normal(self):
        """+9.4% 미만 → NORMAL 유지."""
        from core.vi_handler import VIState
        h = _fresh_handler()
        h.update_from_tick("000001", price=10940, prev_close=10000)  # +9.40%
        assert h.get_vi_state("000001") == VIState.NORMAL

    def test_limit_up_excluded(self):
        """상한가(+30%) 도달 종목 → STATIC_VI 미발동 (limit_up_exit 보호)."""
        from core.vi_handler import VIState
        h = _fresh_handler()
        h.update_from_tick("000001", price=13000, prev_close=10000)  # +30.0%
        assert h.get_vi_state("000001") == VIState.NORMAL

    def test_near_limit_up_excluded(self):
        """상한가의 99% 이상 종목 → STATIC_VI 미발동 (limit_up_exit 우선)."""
        from core.vi_handler import VIState
        h = _fresh_handler()
        # 13000 × 0.99 = 12870 → 12870 이상이면 limit_up 영역 간주
        h.update_from_tick("000001", price=12870, prev_close=10000)
        assert h.get_vi_state("000001") == VIState.NORMAL

    def test_negative_threshold_activates(self):
        """−9.5% 하락 → STATIC_VI (절댓값 기준)."""
        from core.vi_handler import VIState
        h = _fresh_handler()
        h.update_from_tick("000001", price=9050, prev_close=10000)  # −9.50%
        assert h.get_vi_state("000001") == VIState.STATIC_VI

    def test_zero_prev_close_no_crash(self):
        """prev_close=0이면 조용히 무시."""
        from core.vi_handler import VIState
        h = _fresh_handler()
        h.update_from_tick("000001", price=10000, prev_close=0)
        assert h.get_vi_state("000001") == VIState.NORMAL

    def test_custom_threshold(self):
        """static_pct 외부 주입 시 임계 적용."""
        from core.vi_handler import VIState
        h = _fresh_handler(static_pct=0.05)
        h.update_from_tick("000001", price=10510, prev_close=10000)  # +5.10%
        assert h.get_vi_state("000001") == VIState.STATIC_VI
```

- [ ] **Step 2: 테스트 실행하여 통과 확인 (이미 Step 1.1.3에서 구현됨)**

Run: `pytest tests/test_vi_handler.py::TestUpdateFromTick -v`
Expected: 7 passed

- [ ] **Step 3: 커밋**

```bash
git add tests/test_vi_handler.py
git commit -m "test: VIHandler.update_from_tick 단위 테스트 7건"
```

### 1.3 만료 / SUSPECTED / 조회 매트릭스 테스트

- [ ] **Step 1: 실패 테스트 작성**

Append to `tests/test_vi_handler.py`:
```python
class TestExpiry:
    def test_static_vi_expires(self):
        """assumed_duration 경과 후 조회 → NORMAL 자동 복귀."""
        from core.vi_handler import VIState
        h = _fresh_handler(assumed_duration_sec=0)  # 즉시 만료
        h.update_from_tick("000001", price=10950, prev_close=10000)
        import time as _t
        _t.sleep(0.01)
        assert h.get_vi_state("000001") == VIState.NORMAL

    def test_static_vi_not_expired_yet(self):
        """assumed_duration 내 → STATIC_VI 유지."""
        from core.vi_handler import VIState
        h = _fresh_handler(assumed_duration_sec=60)
        h.update_from_tick("000001", price=10950, prev_close=10000)
        assert h.get_vi_state("000001") == VIState.STATIC_VI


class TestSuspected:
    def test_flag_suspected_activates(self):
        """flag_suspected → SUSPECTED 상태."""
        from core.vi_handler import VIState
        h = _fresh_handler()
        h.flag_suspected("000001", "rt_cd=9")
        assert h.get_vi_state("000001") == VIState.SUSPECTED

    def test_suspected_expires(self):
        """suspected_duration 경과 → NORMAL."""
        from core.vi_handler import VIState
        h = _fresh_handler(suspected_duration_sec=0)
        h.flag_suspected("000001", "rt_cd=9")
        import time as _t
        _t.sleep(0.01)
        assert h.get_vi_state("000001") == VIState.NORMAL


class TestQueries:
    def test_is_vi_active_matrix(self):
        from core.vi_handler import VIState
        h = _fresh_handler()
        # NORMAL: False
        assert h.is_vi_active("a") is False
        # STATIC_VI: True
        h.update_from_tick("b", price=10950, prev_close=10000)
        assert h.is_vi_active("b") is True
        # SUSPECTED: True
        h.flag_suspected("c", "test")
        assert h.is_vi_active("c") is True

    def test_should_use_best_limit_matrix(self):
        h = _fresh_handler()
        assert h.should_use_best_limit("a") is False
        h.update_from_tick("b", price=10950, prev_close=10000)
        assert h.should_use_best_limit("b") is True
        h.flag_suspected("c", "test")
        assert h.should_use_best_limit("c") is True


class TestStubs:
    def test_update_from_ws_0a_no_exception(self):
        """현재는 스텁 — 호출만으로 예외 없음."""
        h = _fresh_handler()
        h.update_from_ws_0a("000001", {"any": "payload"})
        # 상태 변동 없음 확인
        from core.vi_handler import VIState
        assert h.get_vi_state("000001") == VIState.NORMAL
```

- [ ] **Step 2: 테스트 실행하여 통과 확인**

Run: `pytest tests/test_vi_handler.py -v`
Expected: 14 passed total

- [ ] **Step 3: 커밋**

```bash
git add tests/test_vi_handler.py
git commit -m "test: VIHandler 만료/SUSPECTED/조회 매트릭스 테스트"
```

---

## Task 2: Config 외부화 (vi_static_pct 등)

**Files:**
- Modify: `config.yaml` (trading 섹션)
- Modify: `config/settings.py` (TradingConfig)
- Modify: `tests/test_settings.py`

### 2.1 TradingConfig 필드 + 테스트

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_settings.py`에 다음 함수 추가 (`test_market_calendar_2027` 위에 삽입):
```python
def test_trading_config_vi_defaults():
    """VI 관련 기본값이 spec 값과 일치."""
    from config.settings import TradingConfig
    tc = TradingConfig()
    assert tc.vi_static_pct == 0.095
    assert tc.vi_assumed_duration_sec == 150
    assert tc.vi_suspected_duration_sec == 60
```

- [ ] **Step 2: 테스트 실행하여 실패 확인**

Run: `pytest tests/test_settings.py::test_trading_config_vi_defaults -v`
Expected: FAIL — `AttributeError: ... has no attribute 'vi_static_pct'`

- [ ] **Step 3: `TradingConfig`에 3개 필드 추가**

`config/settings.py`의 `TradingConfig` dataclass에 (다른 trading 필드와 같은 들여쓰기로) 추가:
```python
    # VI(변동성완화장치) 휴리스틱
    # static_pct=0.095: 전일종가 대비 ±9.5% 이상이면 정적VI 추정
    # assumed_duration_sec=150: 단일가 매매 2분 + 랜덤종료 30초
    # suspected_duration_sec=60: REST 주문 거부 기반 SUSPECTED 만료 (키움 일시 장애 대비)
    vi_static_pct: float = 0.095
    vi_assumed_duration_sec: int = 150
    vi_suspected_duration_sec: int = 60
```

위치는 `TradingConfig` dataclass 본문 마지막 줄 (`@dataclass(frozen=True)` 다음 dataclass 직전). 정확한 위치 확인을 위해:

Run: `grep -n "class TradingConfig" config/settings.py`
이어서 해당 클래스의 마지막 필드 다음 줄에 삽입.

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/test_settings.py::test_trading_config_vi_defaults -v`
Expected: PASS

- [ ] **Step 5: 기존 test_trading_config_defaults_match_yaml 회귀 확인**

Run: `pytest tests/test_settings.py -v`
Expected: 전체 통과 (yaml과 일치 검증 테스트가 깨지면 다음 Step에서 yaml 보강)

### 2.2 config.yaml 키 추가

- [ ] **Step 1: yaml 수정**

`config.yaml` `trading:` 섹션의 `market_ma_length: 5` 다음 줄에 삽입:
```yaml

  # VI(변동성완화장치) 휴리스틱 — 시장가 → 최유리지정가 자동 전환용
  # vi_static_pct: 전일종가 대비 |change_pct| ≥ 9.5% → STATIC_VI 추정 (보수적)
  # vi_assumed_duration_sec: 단일가 매매 2분 + 랜덤종료 30초
  # vi_suspected_duration_sec: REST 주문 거부(rt_cd≠"0") 기반 SUSPECTED 만료
  vi_static_pct: 0.095
  vi_assumed_duration_sec: 150
  vi_suspected_duration_sec: 60
```

- [ ] **Step 2: yaml 로딩이 깨지지 않는지 확인**

Run: `python -c "from config.settings import AppConfig; c = AppConfig.from_yaml(); print(c.trading.vi_static_pct, c.trading.vi_assumed_duration_sec, c.trading.vi_suspected_duration_sec)"`
Expected: `0.095 150 60`

- [ ] **Step 3: 전체 settings 테스트 회귀 확인**

Run: `pytest tests/test_settings.py -v`
Expected: 전체 통과

- [ ] **Step 4: 커밋**

```bash
git add config.yaml config/settings.py tests/test_settings.py
git commit -m "feat: config에 VI 휴리스틱 파라미터 3개 추가"
```

---

## Task 3: 키움 코드 + OrderManager 확장

**Files:**
- Modify: `core/kiwoom_rest.py` (PRICE_BEST_LIMIT 상수)
- Modify: `core/order_manager.py` (시그니처 확장)
- Modify: `tests/test_order_manager.py` (2 케이스 추가)

### 3.1 PRICE_BEST_LIMIT 상수

- [ ] **Step 1: 상수 추가**

`core/kiwoom_rest.py`의 `PRICE_MARKET = "03"` 다음 줄에 추가:
```python
PRICE_BEST_LIMIT = "06"  # 최유리지정가 (VI 단일가 매매 대응)
```

- [ ] **Step 2: import 가능 확인**

Run: `python -c "from core.kiwoom_rest import PRICE_BEST_LIMIT; print(PRICE_BEST_LIMIT)"`
Expected: `06`

### 3.2 OrderManager: best_limit 매핑 + prefer_best_limit 파라미터

먼저 현재 `_send_order` 시그니처와 호출 경로를 확인한다.

Run: `grep -n "_send_order\|_ORDER_TYPE_TO_KIWOOM" core/order_manager.py`

- [ ] **Step 1: 실패 테스트 작성 — best_limit 변환**

기존 `tests/test_order_manager.py`에 새 테스트 케이스 추가 (파일 끝에):
```python
class TestPreferBestLimit:
    """VI 대응: prefer_best_limit=True + order_type='market' → 키움 코드 '06' 전송."""

    @pytest.mark.asyncio
    async def test_prefer_best_limit_converts_market_to_06(self, monkeypatch):
        """market 주문이 prefer_best_limit=True 시 키움 '06' 코드로 변환."""
        from core.order_manager import OrderManager
        from config.settings import TradingConfig

        captured: dict = {}

        class FakeRest:
            async def place_order(self, **kwargs):
                captured.update(kwargs)
                return {"rt_cd": "0", "ord_no": "1234"}

        class FakeDb:
            async def execute(self, *args, **kwargs):
                return None

        om = OrderManager(
            rest_client=FakeRest(),
            risk_manager=None,
            notifier=None,
            db=FakeDb(),
            trading_config=TradingConfig(),
        )
        result = await om._send_order(
            ticker="000001", qty=1, price=10000, side="sell",
            order_type="market",
            prefer_best_limit=True,
            reason="stop_loss",
        )
        assert result is not None
        assert captured.get("order_type") == "06"

    @pytest.mark.asyncio
    async def test_rejection_callback_invoked_on_rt_cd_nonzero(self):
        """rt_cd ≠ '0' 응답 시 on_rejection 콜백 호출."""
        from core.order_manager import OrderManager
        from config.settings import TradingConfig

        class FakeRest:
            async def place_order(self, **kwargs):
                return {"rt_cd": "9", "msg1": "거부"}

        class FakeDb:
            async def execute(self, *args, **kwargs):
                return None

        rejections: list[tuple[str, str]] = []
        om = OrderManager(
            rest_client=FakeRest(),
            risk_manager=None, notifier=None, db=FakeDb(),
            trading_config=TradingConfig(),
        )
        await om._send_order(
            ticker="000001", qty=1, price=10000, side="sell",
            order_type="market",
            on_rejection=lambda tk, rt: rejections.append((tk, rt)),
            reason="stop_loss",
        )
        assert rejections == [("000001", "9")]
```

테스트가 KiwoomRestClient의 실제 메서드명(`place_order`)을 가정하므로, 다음 Step에서 실제 메서드명 확인 후 조정 필요.

- [ ] **Step 2: 실제 KiwoomRestClient 주문 메서드 확인**

Run: `grep -n "async def" core/kiwoom_rest.py | head -30`

`place_order` 또는 다른 이름인지 확인. 테스트 fake의 메서드명을 실제명과 일치시킨다. (예: `place_order` 대신 `order`라면 fake도 `async def order`로 변경.)

추가로:
Run: `grep -n "_rest_client\." core/order_manager.py`
호출 측 메서드명도 함께 맞춤.

- [ ] **Step 3: 테스트 실행하여 실패 확인**

Run: `pytest tests/test_order_manager.py::TestPreferBestLimit -v`
Expected: FAIL — `_send_order` 가 `prefer_best_limit` / `on_rejection` 키워드 인자를 받지 않음 (TypeError) 또는 변환 미적용

- [ ] **Step 4: OrderManager 수정**

`core/order_manager.py` 상단 import에 추가:
```python
from typing import Callable
from core.kiwoom_rest import KiwoomRestClient, PRICE_LIMIT, PRICE_MARKET, PRICE_BEST_LIMIT
```

기존 매핑 확장:
```python
_ORDER_TYPE_TO_KIWOOM = {
    "limit": PRICE_LIMIT,
    "market": PRICE_MARKET,
    "best_limit": PRICE_BEST_LIMIT,
}
```

기존 `_send_order` 시그니처를 다음으로 교체 (기존 본문 유지하면서 변환/콜백 추가):
```python
async def _send_order(
    self, ticker, qty, price, side,
    order_type: str = "limit",
    prefer_best_limit: bool = False,
    on_rejection: Callable[[str, str], None] | None = None,
    reason: str = "",
    strategy: str = "",
    pnl: float = 0,
    pnl_pct: float = 0,
):
    """order_type: 'limit' / 'market' / 'best_limit'.

    prefer_best_limit=True + order_type='market' 인 경우 키움 코드 '06'으로 전환.
    응답 rt_cd ≠ '0' 시 on_rejection(ticker, rt_cd) 호출 (VI 의심 감지).
    """
    effective_type = order_type
    if prefer_best_limit and order_type == "market":
        effective_type = "best_limit"
        logger.info(f"[VI] {ticker} 매도 → 최유리지정가 전환")
    # 이하 기존 본문에서 _kiwoom_code(order_type) → _kiwoom_code(effective_type)
    # DB INSERT의 order_type 칼럼도 effective_type 사용
```

**중요**: `_send_order` 본문에서 키움 호출(`self._rest_client.place_order(...)` 또는 실제 메서드)의 응답을 변수에 받아 다음을 추가:
```python
    if response is not None and on_rejection is not None:
        rt = response.get("rt_cd")
        if rt is not None and rt != "0":
            try:
                on_rejection(ticker, str(rt))
            except Exception as e:
                logger.warning(f"[VI] {ticker} on_rejection 콜백 예외: {e}")
```

또한 호출 측 두 메서드를 시그니처 확장:
```python
async def execute_sell_stop(
    self, ticker, qty, price, strategy="", pnl=0, pnl_pct=0,
    exit_reason: str = "stop_loss",
    prefer_best_limit: bool = False,
    on_rejection: Callable[[str, str], None] | None = None,
):
    return await self._send_order(
        ticker, qty, price, "sell",
        order_type="market",
        prefer_best_limit=prefer_best_limit,
        on_rejection=on_rejection,
        reason=exit_reason, strategy=strategy, pnl=pnl, pnl_pct=pnl_pct,
    )

async def execute_sell_force_close(
    self, ticker, qty, price, strategy="", pnl=0, pnl_pct=0,
    exit_reason: str = "forced_close",
    prefer_best_limit: bool = False,
    on_rejection: Callable[[str, str], None] | None = None,
):
    return await self._send_order(
        ticker, qty, price, "sell",
        order_type="market",
        prefer_best_limit=prefer_best_limit,
        on_rejection=on_rejection,
        reason=exit_reason, strategy=strategy, pnl=pnl, pnl_pct=pnl_pct,
    )
```

기존 호출자(execute_buy, execute_sell_tp1)는 변경 없음 (기본값 False/None).

- [ ] **Step 5: 테스트 실행하여 통과 확인**

Run: `pytest tests/test_order_manager.py::TestPreferBestLimit -v`
Expected: 2 passed

- [ ] **Step 6: 기존 test_order_manager 회귀 확인**

Run: `pytest tests/test_order_manager.py -v`
Expected: 전체 통과 (기존 테스트는 prefer_best_limit/on_rejection 미전달 → 기본값 사용)

- [ ] **Step 7: 커밋**

```bash
git add core/kiwoom_rest.py core/order_manager.py tests/test_order_manager.py
git commit -m "feat: OrderManager prefer_best_limit + on_rejection 콜백 지원"
```

---

## Task 4: engine_worker 통합

**Files:**
- Modify: `gui/workers/engine_worker.py`
- Create: `tests/test_engine_worker_vi.py`

engine_worker는 PyQt6 시그널과 asyncio 루프를 혼합한 크고 복잡한 파일이다. 통합은 최소 침습으로, 5개 명시된 지점만 수정한다.

### 4.1 VIHandler 인스턴스화

- [ ] **Step 1: import + __init__ 수정**

`gui/workers/engine_worker.py` 상단 import 블록(다른 `from core.` 줄들과 같은 위치)에 추가:
```python
from core.vi_handler import VIHandler
```

`__init__` 본문에서 `self._prev_close: dict[str, float] = {}` (line 145 부근) 다음 줄에 추가:
```python
        self._vi_handler = VIHandler(
            static_pct=self._config.trading.vi_static_pct,
            assumed_duration_sec=self._config.trading.vi_assumed_duration_sec,
            suspected_duration_sec=self._config.trading.vi_suspected_duration_sec,
        )
```

(`self._config.trading`이 `TradingConfig`인지 확인. 만약 다른 속성명이면 실제 명칭 사용. Run: `grep -n "self._config" gui/workers/engine_worker.py | head -10`)

- [ ] **Step 2: 단순 sanity — engine_worker import 가능 확인**

Run: `python -c "from gui.workers.engine_worker import EngineWorker; print('ok')"`
Expected: `ok`

- [ ] **Step 3: 커밋**

```bash
git add gui/workers/engine_worker.py
git commit -m "feat: engine_worker에 VIHandler 인스턴스 추가"
```

### 4.2 _tick_consumer 통합 — 휴리스틱 업데이트 + stop_loss prefer_best_limit

- [ ] **Step 1: 통합 테스트 작성 (먼저 작성, 추후 구현 검증)**

Create `tests/test_engine_worker_vi.py`:
```python
"""tests/test_engine_worker_vi.py — engine_worker × VIHandler 통합."""

from __future__ import annotations

import pytest

from core.vi_handler import VIHandler, VIState


class TestTickConsumerIntegration:
    def test_update_from_tick_uses_prev_close_cache(self):
        """_prev_close[ticker] 값이 vi_handler에 전달되면 VI 추정 발동."""
        h = VIHandler(static_pct=0.095, assumed_duration_sec=60)
        prev_close_cache = {"000001": 10000.0}
        # _tick_consumer가 수행할 호출을 시뮬레이션
        prev = prev_close_cache.get("000001")
        if prev:
            h.update_from_tick("000001", price=10960, prev_close=prev)  # +9.6%
        assert h.is_vi_active("000001") is True

    def test_missing_prev_close_silent_skip(self):
        """prev_close 캐시 미스 시 vi_handler 호출 자체를 건너뜀."""
        h = VIHandler()
        prev_close_cache: dict[str, float] = {}
        prev = prev_close_cache.get("000001")
        if prev:
            h.update_from_tick("000001", price=10960, prev_close=prev)
        assert h.is_vi_active("000001") is False


class TestSignalConsumerIntegration:
    def test_buy_blocked_when_vi_active(self):
        """VI 활성 종목에 매수 신호 → vi_handler.is_vi_active() == True 분기로 차단."""
        h = VIHandler()
        h.flag_suspected("000001", "test")
        assert h.is_vi_active("000001") is True
        # engine_worker의 _signal_consumer에서:
        # if self._vi_handler.is_vi_active(ticker): continue
        # 위 분기로 매수 미실행

    def test_buy_proceeds_when_normal(self):
        h = VIHandler()
        assert h.is_vi_active("000001") is False


class TestForceCloseIntegration:
    def test_force_close_uses_prefer_best_limit_when_vi(self):
        """VI 의심 종목 forced_close 시 prefer_best_limit=True 전달."""
        h = VIHandler()
        h.flag_suspected("000001", "rt_cd=9")
        assert h.should_use_best_limit("000001") is True

    def test_force_close_market_when_normal(self):
        h = VIHandler()
        assert h.should_use_best_limit("000001") is False
```

이 통합 테스트는 EngineWorker 전체를 부팅하지 않고 VIHandler가 engine_worker 통합 지점에서 사용되는 패턴을 검증한다. EngineWorker 자체의 동작은 별도 e2e 테스트 영역.

- [ ] **Step 2: 테스트 실행 (현재는 VIHandler만 사용하므로 통과 예상)**

Run: `pytest tests/test_engine_worker_vi.py -v`
Expected: 6 passed

- [ ] **Step 3: _tick_consumer 수정 — 가격 갱신 직후 update_from_tick 호출**

`gui/workers/engine_worker.py` `_tick_consumer` 메서드 본문에서 다음 라인을 찾는다:
```python
                ticker = tick["ticker"]
                price = tick["price"]
                self._latest_prices[ticker] = price
```
(line 727~729 근처)

바로 다음에 추가:
```python
                # VI 휴리스틱 업데이트 (prev_close 캐시 미스 시 조용히 스킵)
                _prev = self._prev_close.get(ticker)
                if _prev:
                    try:
                        self._vi_handler.update_from_tick(ticker, price, _prev)
                    except Exception as e:
                        logger.warning(f"[VI] {ticker} update_from_tick 예외: {e}")
```

- [ ] **Step 4: _tick_consumer 수정 — check_stop_loss 분기에 prefer_best_limit 전달**

같은 `_tick_consumer` 안에서 `check_stop_loss` 분기 (line 774 부근, `await self._order_manager.execute_sell_stop(`) 호출의 인자를 다음으로 변경:
```python
                    prefer_best = self._vi_handler.should_use_best_limit(ticker)
                    await self._order_manager.execute_sell_stop(
                        ticker=ticker, qty=qty, price=int(price),
                        strategy=strategy_name, pnl=pnl, pnl_pct=pnl_pct,
                        exit_reason=reason_code,
                        prefer_best_limit=prefer_best,
                        on_rejection=lambda tk, rt: self._vi_handler.flag_suspected(tk, f"rt_cd={rt}"),
                    )
```

**중요**: `limit_up_exit` 분기 (line 740 부근, 같은 `execute_sell_stop` 호출이지만 `exit_reason="limit_up_exit"`인 경우)는 **변경하지 않는다**. ADR-018 보호 원칙.

- [ ] **Step 5: import 검증 + 정적 호출 확인**

Run: `python -c "from gui.workers.engine_worker import EngineWorker; print('ok')"`
Expected: `ok`

Run: `grep -n "should_use_best_limit\|update_from_tick\|flag_suspected\|is_vi_active" gui/workers/engine_worker.py`
Expected: 최소 2~3건 (update_from_tick 1, should_use_best_limit 1, flag_suspected 1)

- [ ] **Step 6: 커밋**

```bash
git add gui/workers/engine_worker.py tests/test_engine_worker_vi.py
git commit -m "feat: _tick_consumer에 VI 휴리스틱 업데이트 + stop_loss best_limit 전환"
```

### 4.3 _signal_consumer 통합 — 매수 차단

- [ ] **Step 1: _signal_consumer에서 매수 직전 분기 추가**

`_signal_consumer` (line 969 부근)에서 매수 실행 직전(예: `execute_buy` 호출 또는 신호 처리 직전)에 다음 분기를 추가:

먼저 정확한 위치를 찾는다:
Run: `grep -n "execute_buy\|generate_signal" gui/workers/engine_worker.py | head -20`

매수 의도가 확정되는 지점(시그널이 BUY로 판정된 직후, 주문 전송 전)에 다음 코드 삽입:
```python
                if self._vi_handler.is_vi_active(ticker):
                    logger.info(
                        f"[VI] {ticker} 매수 차단 — "
                        f"state={self._vi_handler.get_vi_state(ticker).value}"
                    )
                    continue
```

`continue` 위치는 `_signal_consumer`가 `while` 루프 또는 `for` 루프 내부인지 확인 후 결정. `while`이면 `continue`, 분기 함수면 `return`.

Run: `grep -n "while self._running\|for .* in " gui/workers/engine_worker.py | grep -A2 "_signal_consumer"`
실제 컨텍스트에 맞게 `continue` 또는 `return` 선택.

- [ ] **Step 2: 정적 검증**

Run: `grep -n "is_vi_active" gui/workers/engine_worker.py`
Expected: 1건 이상

Run: `python -c "from gui.workers.engine_worker import EngineWorker; print('ok')"`
Expected: `ok`

- [ ] **Step 3: 커밋**

```bash
git add gui/workers/engine_worker.py
git commit -m "feat: _signal_consumer에 VI 활성 종목 매수 차단"
```

### 4.4 _force_close 통합

- [ ] **Step 1: _force_close 안에서 prefer_best_limit 전달**

`gui/workers/engine_worker.py` `_force_close` 메서드(line 1374 부근)의 `await self._order_manager.execute_sell_force_close(` 호출을 다음으로 교체:
```python
                    prefer_best = self._vi_handler.should_use_best_limit(ticker)
                    await self._order_manager.execute_sell_force_close(
                        ticker=ticker, qty=qty, price=close_price,
                        strategy=strategy_name, pnl=pnl, pnl_pct=pnl_pct,
                        exit_reason="forced_close",
                        prefer_best_limit=prefer_best,
                        on_rejection=lambda tk, rt: self._vi_handler.flag_suspected(tk, f"rt_cd={rt}"),
                    )
```

대기/재시도 로직은 추가하지 않음 (spec §3 원칙 2).

- [ ] **Step 2: 정적 검증**

Run: `grep -n "execute_sell_force_close" gui/workers/engine_worker.py`
호출이 prefer_best_limit 키워드를 포함하는지 확인.

Run: `python -c "from gui.workers.engine_worker import EngineWorker; print('ok')"`
Expected: `ok`

- [ ] **Step 3: 커밋**

```bash
git add gui/workers/engine_worker.py
git commit -m "feat: _force_close에 VI 의심 시 best_limit 전환"
```

---

## Task 5: 최종 검증

**Files:** (검증만, 수정 없음)

- [ ] **Step 1: 전체 단위 테스트**

Run: `pytest tests/ -v --tb=short`
Expected: **모든 테스트 통과** (기존 248 + 신규 vi_handler 14 + engine_worker_vi 6 + order_manager 2 + settings 1 = 약 271건)

실패 항목이 있으면 원인 분석 후 해당 Task로 돌아가 수정.

- [ ] **Step 2: selftest**

Run: `python selftest.py`
Expected: `7 / 7` 통과

- [ ] **Step 3: VI 로깅 일관성 검사**

Run: `grep -rn -F "[VI]" gui/ core/`
Expected: 최소 5건의 `[VI]` 로그 (update_from_tick STATIC 추정, SUSPECTED 활성, 매도 전환, 매수 차단, 만료).

- [ ] **Step 4: 회귀 grep — limit_up_exit 경로가 prefer_best_limit를 받지 않음**

Run: `grep -B2 -A8 "limit_up_exit" gui/workers/engine_worker.py | grep "prefer_best_limit"`
Expected: 0건 (limit_up_exit는 강제 시장가 유지, ADR-018 보호)

- [ ] **Step 5: hard-coded 임계값 잔여 검사**

Run: `grep -rn "0\.095\|9\.5%\|150\s*#.*VI\|60\s*#.*SUSPECTED" core/ gui/`
Expected: `core/vi_handler.py`의 dataclass 기본값 1~2건만 (그 외 코드는 모두 config 경유)

- [ ] **Step 6: baseline backtest 회귀 검증**

VI는 백테스트 경로에 통합되지 않으므로 baseline PF 변동 없어야 함.

Run: `python scripts/baseline_pf_limit_up.py`
Expected: `PF : 4.363`, `총 거래: 248`, `limit_up_exit: 15` (CLAUDE.md baseline과 일치)

수치가 다르면 백테스터에 VI 코드가 유출됐는지 검토.

- [ ] **Step 7: 최종 커밋 — CLAUDE.md 갱신 (변경 사항 요약)**

`CLAUDE.md`의 `재조립 진행 상태 — 전 Phase 완결` 섹션에 한 줄 추가:
```markdown
- [x] VI 휴리스틱 대응 (2026-05-12) — 시장가 → 최유리지정가 자동 전환, VI 활성 종목 매수 차단. limit_up_exit / forced_close 보호.
```

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md에 VI 휴리스틱 대응 추가"
```

---

## Spec-Plan Coverage

| Spec 섹션 | 구현 Task |
|-----------|-----------|
| §5.1 VIHandler 클래스 | Task 1.1, 1.3 |
| §5.1.1 휴리스틱 임계 (limit_up 제외 포함) | Task 1.2 |
| §5.1.2 lazy 만료 | Task 1.3 (TestExpiry) |
| §5.1.3 should_use_best_limit 매트릭스 | Task 1.3 (TestQueries) |
| §5.1.4 is_vi_active | Task 1.3 (TestQueries) |
| §5.1.5 update_from_ws_0a 스텁 | Task 1.1 Step 3 + Task 1.3 (TestStubs) |
| §5.2 config.yaml/settings 신규 키 | Task 2 |
| §5.3 PRICE_BEST_LIMIT 상수 | Task 3.1 |
| §5.4 OrderManager prefer_best_limit + on_rejection | Task 3.2 |
| §5.5.1 VIHandler 인스턴스화 | Task 4.1 |
| §5.5.2 _tick_consumer 통합 (update + stop_loss 전환, limit_up 무변경) | Task 4.2 |
| §5.5.3 _signal_consumer 매수 차단 | Task 4.3 |
| §5.5.4 _force_close 통합 | Task 4.4 |
| §5.5.5 `_prev_close_cache` | Task 4.2 (기존 `self._prev_close` 재사용 명시) |
| §5.6 risk_manager 무변경 | (의도적 비구현) |
| §6 로깅 규약 | Task 1.1 Step 3 본문 + Task 5 Step 3 검증 |
| §7.1 단위 테스트 8개 | Task 1.2 + 1.3 (실제 14건으로 분할) |
| §7.2 OrderManager 케이스 2건 | Task 3.2 Step 1 |
| §7.3 engine_worker 통합 케이스 | Task 4.2 Step 1 (test_engine_worker_vi.py) |
| §8 에러 처리 | Task 4.2 Step 3 try/except + OrderManager on_rejection try/except |
| §11 회귀 검증 | Task 5 |
| §12 명시적 금지 | Task 5 Step 4 (limit_up_exit 보호 검증) |

모든 spec 섹션이 task로 매핑됨. 누락 없음.
