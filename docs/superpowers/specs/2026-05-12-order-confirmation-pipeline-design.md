# Order Confirmation Pipeline 설계 — 주문 접수와 체결 분리

> 작성: 2026-05-12
> 상태: 승인됨 (사용자 검토 대기)
> 관련: ADR-007 (positions 테이블), ADR-018 (limit_up_exit), VI Handler (2026-05-12)

## 1. 배경

현 시스템은 OrderManager의 `execute_buy` / `execute_sell_*`가 `rt_cd=="0"` 응답을 받으면 즉시 `register_position` / `settle_sell`을 호출한다. 그러나 `rt_cd=="0"`는 **주문 접수 성공**일 뿐 **체결 완료**가 아니다.

다음 시나리오에서 포지션-잔고 불일치가 발생한다:

1. **VI 단일가 매매 진입**: 시장가 주문이 접수는 되지만 단일가 매매 종료까지 미체결
2. **호가 공백**: 매수 잔량 부족으로 부분 체결
3. **상한가 잔량 소진**: 매도 주문이 접수되지만 체결 거부
4. **WS 통신 지연**: 주문은 체결됐지만 통보 지연으로 다음 tick에서 stop_loss 재트리거

WS "00"(주문체결) 메시지를 받는 `_order_confirmation_consumer`가 이미 존재하지만 현재는 로그만 출력하고 상태에 반영하지 않는다.

## 2. 목표 / 비목표

### 목표
- 주문 접수와 체결을 분리하는 OrderTracker 도입
- 매도 `settle_sell` 호출을 체결 확인 시점으로 지연
- pending 매도 중인 ticker에 대한 재진입(중복 주문) 가드
- 10초 타임아웃 + REST 폴백 1회 + 자연 재시도 메커니즘

### 비목표
- PaperOrderManager 동작 변경 (즉시 체결 가정 유지)
- 백테스트 경로(`backtest/backtester.py`)에 tracker 도입 (baseline PF 영향 없도록)
- 키움 WS "00" 메시지 필드 코드의 확정 (실 페이로드 캡처 후 별도 작업)
- 재시작 시 진행 중 주문 복구 (인메모리 한정, 후속 작업)

## 3. 설계 원칙

1. **단일 진실 공급원 = OrderTracker**. 재진입 가드도 체결 상태도 tracker 조회. risk_manager는 status 필드를 가지되 결정 권한은 tracker가 보유.
2. **paper_mode는 tracker 미사용**. 현 즉시 settle 흐름 유지. PaperOrderManager 변경 금지.
3. **TIMEOUT 시 자연 재시도**. 매도 TIMEOUT 후 tracker entry를 삭제하면 다음 tick에서 stop_loss/limit_up_exit가 자연스럽게 재트리거. 별도 강제 재시도 로직 없음.
4. **limit_up_exit는 raise_stop_to_limit_up_floor 메커니즘 보존**. TIMEOUT을 "no result" 시나리오로 동일 처리 — stop을 상한가×0.99로 상향.
5. **OrderTracker는 인메모리 무외부의존**. 재시작 시 초기화. 운영 가이드로 보완.
6. **백테스트 무영향**. backtester는 tracker 미사용. baseline PF 4.36 변동 없음.

## 4. 아키텍처

```
                  ┌────────────────────────────────────────┐
                  │ engine_worker._signal_consumer (BUY)   │
                  │   OrderManager.execute_buy → rt_cd="0" │
                  │                              order_no  │
                  │   paper_mode?                          │
                  │    ├─ yes → register_position 즉시 ────┼──→ risk_manager (status=confirmed)
                  │    └─ no  → tracker.submit(...)        │
                  │             register_position(...,     ├──→ risk_manager (status=pending)
                  │               status='pending')        │
                  └────────────────────────────────────────┘

                  ┌────────────────────────────────────────┐
                  │ engine_worker._tick_consumer           │
                  │   ticker = tick.ticker                 │
                  │   if tracker.get_pending(ticker):      │
                  │     update highest_price only          │
                  │     continue                           │
                  │   else: 기존 stop_loss / limit_up /    │
                  │         trailing 흐름                  │
                  │                                        │
                  │   매도 발생 시:                          │
                  │     OrderManager.execute_sell_stop     │
                  │     → rt_cd="0" → tracker.submit       │
                  │     settle_sell 호출 X (지연)            │
                  └────────────────────────────────────────┘

[WS "00" 체결] → _order_confirmation_consumer
                   ├─ 필드 파싱 (모든 코드 TODO)
                   └─ tracker.on_fill(order_no, qty, price)
                       FILLED 시점:
                         if 매수: risk_manager.mark_confirmed(ticker)
                         if 매도: risk_manager.settle_sell(ticker, price, qty)
                                  trade_executed signal emit

[1s 주기] → _order_tracker_timeout_checker (신규 background task)
              for order in tracker.get_unfilled_older_than(10):
                REST ka10070 잔고 폴백 1회
                ├─ 체결 확인됨 → tracker.on_fill → 정상 흐름
                └─ 미체결: 매수 → kt10001 cancel_order + 알림
                          매도 → tracker.remove + 알림 + 카운터++
                                  (다음 tick에서 자연 재시도)
```

## 5. 컴포넌트

### 5.1 `core/order_tracker.py` (신규)

```python
"""core/order_tracker.py — 주문 접수와 체결을 분리하는 인메모리 상태 추적기.

paper_mode에서는 사용하지 않는다 (PaperOrderManager는 즉시 체결 가정).
"""

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
    """주문번호 기반 인메모리 체결 상태 추적기.

    - submit/on_fill/mark_* 로 상태 변경
    - get_pending(ticker)로 재진입 가드 조회
    - get_unfilled_older_than으로 타임아웃 감지
    """

    def __init__(self, timeout_seconds: float = 10.0):
        self._timeout = timedelta(seconds=timeout_seconds)
        self._orders: dict[str, PendingOrder] = {}   # order_no → PendingOrder
        self._ticker_index: dict[str, str] = {}      # ticker → 최근 active order_no

    # ── 상태 변경 ──
    def submit(self, order_no: str, ticker: str, side: str, qty: int) -> None
    def on_fill(self, order_no: str, filled_qty: int, filled_price: float) -> PendingOrder | None
    def mark_failed(self, order_no: str, reason: str) -> None
    def mark_timeout(self, order_no: str) -> None
    def remove(self, order_no: str) -> None

    # ── 조회 ──
    def get_pending(self, ticker: str) -> PendingOrder | None
    def get_by_order_no(self, order_no: str) -> PendingOrder | None
    def get_unfilled_older_than(self, seconds: float) -> list[PendingOrder]
```

#### 5.1.1 `submit` 의미

- `_orders[order_no] = PendingOrder(...)` (status=PENDING)
- `_ticker_index[ticker] = order_no` (덮어쓰기 — 같은 ticker의 이전 주문은 이미 종결됐다고 가정)
- 같은 order_no 재호출 시 idempotent (덮어쓰지 않음)

#### 5.1.2 `on_fill` 누적 + 상태 전이

```
filled_qty += incoming_qty
filled_price = volume-weighted average
status:
  filled_qty == 0           → PENDING
  0 < filled_qty < requested → PARTIAL
  filled_qty >= requested   → FILLED
```

- 이미 `FILLED` 상태에서 추가 `on_fill` → 경고 로그 + 무시 (중복 통보 방어)
- `FAILED` / `TIMEOUT` 상태에서 호출 → 경고 + 무시
- 반환값: 갱신된 PendingOrder (호출자가 status를 즉시 판단할 수 있게)

#### 5.1.3 `get_pending(ticker)` — 재진입 가드 핵심

`_ticker_index[ticker]`로 order_no 조회 → PendingOrder의 status가 PENDING 또는 PARTIAL이면 반환, FILLED/FAILED/TIMEOUT이면 None.

#### 5.1.4 `get_unfilled_older_than(seconds)`

PENDING 또는 PARTIAL 상태의 PendingOrder 중 `(datetime.now() - submitted_at).total_seconds() > seconds`인 항목 list. 타임아웃 체커가 사용.

#### 5.1.5 remove vs mark_*

- `mark_failed` / `mark_timeout`: 상태 변경하되 dict에 보관 (디버깅 흔적). 다만 ticker_index는 즉시 정리 → `get_pending(ticker)` 가 None을 반환하도록 (재진입 가능).
- `remove`: dict에서 완전 제거 (테스트 / 명시적 정리용).

### 5.2 `config.yaml` 신규 키

```yaml
trading:
  order_confirmation_timeout_sec: 10.0
  order_timeout_consecutive_threshold: 3  # 같은 ticker 연속 TIMEOUT 후 긴급 알림
```

`config/settings.py:TradingConfig` 동일 필드 추가.

### 5.3 `risk/risk_manager.py` 변경

- `register_position(..., status: str = "pending")` 파라미터 추가, Position dict에 `"status": status` 저장. 기본값 "pending"이지만 paper_mode 호출 측에서 `"confirmed"` 명시.
- 신규 헬퍼:
  ```python
  def mark_confirmed(self, ticker: str) -> None:
      pos = self._positions.get(ticker)
      if pos:
          pos["status"] = "confirmed"
  ```
- `settle_sell` 무변경 (engine_worker가 호출 시점만 늦춤)
- `check_stop_loss` / `check_limit_up` 등 변경 없음 (재진입 가드는 engine_worker가 책임)

### 5.4 `core/kiwoom_rest.py` 신규 메서드

```python
async def cancel_order(self, order_no: str, ticker: str, qty: int) -> dict:
    """미체결 주문 취소 (kt10001). 매수 TIMEOUT 시 사용."""
    body = {
        "orig_ord_no": order_no,
        "stk_cd": ticker,
        "ord_qty": qty,
    }
    return await self.request("POST", EP_ORDER, API_STOCK_CANCEL, data=body)
```

`EP_ORDER`는 기존 주문 엔드포인트(`/api/dostk/ordr` 또는 동등). 실제 키움 cancel 엔드포인트가 다르면 별도 상수 정의. 본 spec은 동일 path 사용 가정.

`get_account_positions()` 또는 동등 메서드는 `ka10070` 이미 존재한다고 가정 (확인 필요). 신규 추가가 필요하면 plan에서 결정.

### 5.5 `gui/workers/engine_worker.py` 변경 (5지점)

#### 5.5.1 `__init__` + 모듈 상단

WS "00" 필드 코드 상수 (TODO 명시):
```python
# core/order_tracker_constants.py 또는 engine_worker 모듈 상단
# TODO: 키움 WS '00'(주문체결) 메시지 필드 코드는 미검증.
# 실 페이로드 캡처 후 확정 필요. 운영 전 raw 로그 1회 수집 필수.
_WS_FIELD_ORDER_NO = "9001"      # 주문번호 (추정)
_WS_FIELD_FILLED_PRICE = "10"    # 체결가 (추정)
_WS_FIELD_FILLED_QTY = "900"     # 체결량 (추정)
```

`__init__`에 `self._order_tracker = None` 플레이스홀더. `_run_engine`에서 instantiate:
```python
self._order_tracker = OrderTracker(
    timeout_seconds=self._config.trading.order_confirmation_timeout_sec,
)
self._timeout_counters: dict[str, int] = {}  # ticker → 연속 TIMEOUT 카운터
```

#### 5.5.2 `_run_engine` task 등록

기존 task 리스트에 추가:
```python
asyncio.create_task(self._order_tracker_timeout_checker(), name="order_timeout_checker"),
```

#### 5.5.3 `_order_tracker_timeout_checker` 신규 메서드

```python
async def _order_tracker_timeout_checker(self):
    """1초 주기로 PendingOrder 타임아웃 감지 + REST 폴백."""
    while self._running and not self._stop_event.is_set():
        try:
            await asyncio.sleep(1.0)
            timeout_sec = self._config.trading.order_confirmation_timeout_sec
            stale = self._order_tracker.get_unfilled_older_than(timeout_sec)
            for order in stale:
                logger.warning(f"[ORDER-TRACK] {order.order_no} TIMEOUT — REST 폴백")
                # REST 폴백: ka10070 잔고 1회 조회
                confirmed = await self._verify_fill_via_rest(order)
                if confirmed:
                    # 잔고에 반영됨 → 체결 처리
                    self._order_tracker.on_fill(
                        order.order_no, confirmed["qty"], confirmed["price"],
                    )
                    await self._handle_fill(order.order_no)
                else:
                    # 미체결 확정
                    self._order_tracker.mark_timeout(order.order_no)
                    self._timeout_counters[order.ticker] = (
                        self._timeout_counters.get(order.ticker, 0) + 1
                    )
                    if order.side == "buy":
                        try:
                            await self._rest_client.cancel_order(
                                order.order_no, order.ticker, order.requested_qty,
                            )
                        except Exception as e:
                            logger.error(f"[ORDER-TRACK] cancel_order 실패: {e}")
                    if self._notifier:
                        self._notifier.send_urgent(
                            f"[ORDER-TRACK] {order.ticker} {order.side} TIMEOUT "
                            f"({order.order_no})"
                        )
                    # 연속 TIMEOUT 임계 초과 시 추가 알림 (1분 cooldown은 다음 PR)
                    if self._timeout_counters[order.ticker] >= self._config.trading.order_timeout_consecutive_threshold:
                        if self._notifier:
                            self._notifier.send_urgent(
                                f"[ORDER-TRACK][CRITICAL] {order.ticker} 연속 TIMEOUT "
                                f"{self._timeout_counters[order.ticker]}회"
                            )
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[ORDER-TRACK] timeout_checker 오류: {e}")
```

`_verify_fill_via_rest`는 헬퍼 메서드 — `ka10070` 호출 후 ticker별 잔고 변화를 비교. 구현 디테일은 plan에서.

#### 5.5.4 `_signal_consumer` 매수 직후

기존 `execute_buy` 성공 직후:
```python
                result = await self._order_manager.execute_buy(...)
                if result:
                    is_paper = getattr(self._config, "paper_mode", True)
                    initial_status = "confirmed" if is_paper else "pending"
                    self._risk_manager.register_position(
                        ticker=signal.ticker,
                        ...,
                        status=initial_status,
                    )
                    if not is_paper:
                        self._order_tracker.submit(
                            order_no=result["order_no"],
                            ticker=signal.ticker,
                            side="buy",
                            qty=result["qty"],
                        )
                        logger.info(f"[ORDER-TRACK] {result['order_no']} SUBMIT {signal.ticker} buy {result['qty']}")
                    strategy.on_entry()
                    # ... 기존 ATR-DBG 등은 변경 없음
```

#### 5.5.5 `_tick_consumer` 재진입 가드 + 매도 settle 지연

매 tick에서 가격 갱신 / VI 업데이트 후, exit check 직전:
```python
                # 주문 진행 중이면 highest_price만 갱신, exit 스킵
                pending = self._order_tracker.get_pending(ticker) if self._order_tracker else None
                if pending is not None:
                    # trailing 위한 highest_price 갱신은 계속
                    if pos.get("highest_price", 0) < price:
                        pos["highest_price"] = price
                    continue
```

(`if self._order_tracker` 가드는 paper_mode에서 tracker가 None일 때 안전을 위해.)

기존 `check_limit_up` / `check_stop_loss` 분기의 매도 실행 후 `settle_sell` 호출 제거. 대신 `tracker.submit`만:
```python
                if self._risk_manager.check_stop_loss(ticker, price):
                    # ... 기존 reason_code 계산 ...
                    is_paper = getattr(self._config, "paper_mode", True)
                    prefer_best = self._vi_handler.should_use_best_limit(ticker)
                    result = await self._order_manager.execute_sell_stop(
                        ticker=ticker, qty=qty, price=int(price),
                        strategy=strategy_name, pnl=pnl, pnl_pct=pnl_pct,
                        exit_reason=reason_code,
                        prefer_best_limit=prefer_best,
                        on_rejection=lambda tk, rt: self._vi_handler.flag_suspected(tk, f"주문 거부 (rt_cd={rt})"),
                    )
                    if result is None:
                        continue  # 주문 자체 실패 — VI 등
                    if is_paper:
                        # 페이퍼: 즉시 settle (현 동작)
                        self._risk_manager.settle_sell(ticker, price, qty)
                        # 기존 신호 emit 등
                        ...
                    else:
                        # 실모드: tracker에 등록, 체결 확인 시 settle
                        self._order_tracker.submit(
                            result["order_no"], ticker, "sell", qty,
                        )
                        logger.info(f"[ORDER-TRACK] {result['order_no']} SUBMIT {ticker} sell {qty}")
                    continue
```

`limit_up_exit` 분기도 동일 패턴. **단, ADR-018 보호 원칙상 `prefer_best_limit`는 전달하지 않음** (현 코드와 동일). limit_up_exit가 `result is None`인 경우 기존 `raise_stop_to_limit_up_floor` 로직 그대로. 추가로 TIMEOUT 발생 시에도 `raise_stop_to_limit_up_floor`를 호출하도록 timeout_checker에서 분기 처리:
```python
                    if order.ticker in self._limit_up_exit_pending:
                        new_stop = self._risk_manager.raise_stop_to_limit_up_floor(order.ticker)
                        logger.warning(f"[ORDER-TRACK] limit_up_exit TIMEOUT → stop 상향: {order.ticker}")
                        self._limit_up_exit_pending.discard(order.ticker)
```

`self._limit_up_exit_pending: set[str]` — engine_worker `__init__`에 추가. limit_up_exit 분기에서 `tracker.submit` 직전에 `set.add(ticker)`, `_handle_fill` 또는 timeout 처리 시 `set.discard(ticker)`. OrderTracker는 exit_reason 정보를 보유하지 않으므로 engine_worker가 책임.

#### 5.5.6 `_order_confirmation_consumer` 본문 작성

```python
async def _order_confirmation_consumer(self):
    """WS '00' 체결통보 처리 → OrderTracker 갱신 → 포지션 정산."""
    while self._running and not self._stop_event.is_set():
        try:
            exec_data = await asyncio.wait_for(self._order_queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break
        try:
            if not self._order_tracker:
                logger.debug(f"[ORDER-TRACK] tracker 미초기화 — skip: {exec_data}")
                continue
            values = exec_data.get("values", {})
            order_no = str(values.get(_WS_FIELD_ORDER_NO, ""))
            filled_qty = abs(int(values.get(_WS_FIELD_FILLED_QTY, 0)))
            filled_price = abs(float(values.get(_WS_FIELD_FILLED_PRICE, 0)))
            if not order_no or filled_qty == 0:
                logger.warning(f"[ORDER-TRACK] 무효 체결 메시지 무시: {exec_data}")
                continue
            updated = self._order_tracker.on_fill(order_no, filled_qty, filled_price)
            if updated is None:
                logger.warning(f"[ORDER-TRACK] {order_no} 알 수 없는 주문번호 — 무시")
                continue
            logger.info(
                f"[ORDER-TRACK] {order_no} FILL {updated.filled_qty}/{updated.requested_qty} "
                f"@ {filled_price:,.0f} (status={updated.status.value})"
            )
            if updated.status == OrderStatus.FILLED:
                await self._handle_fill(order_no)
        except Exception as e:
            logger.error(f"[ORDER-TRACK] _order_confirmation_consumer 오류: {e}")
```

`_handle_fill(order_no)` 헬퍼:
- order = tracker.get_by_order_no(order_no)
- 매수: risk_manager.mark_confirmed(ticker)
- 매도: risk_manager.settle_sell(ticker, filled_price, filled_qty) + trade_executed signal emit
- 연속 TIMEOUT 카운터 reset: `self._timeout_counters[ticker] = 0`

#### 5.5.7 `_force_close` 통합

기존 `execute_sell_force_close` 호출은 그대로 두되, real_mode면 tracker.submit + settle_sell 보류:
```python
                    result = await self._order_manager.execute_sell_force_close(...)
                    if result is None:
                        continue
                    if is_paper:
                        self._risk_manager.settle_sell(ticker, float(close_price), qty)
                    else:
                        self._order_tracker.submit(
                            result["order_no"], ticker, "sell", qty,
                        )
```

15:10 강제청산에서도 TIMEOUT은 동일 메커니즘 (timeout_checker가 처리). 단 force_close 후 짧은 시간(15:15까지) tracker에 항목 있으면 추가 알림.

### 5.6 backtester 무영향

`backtest/backtester.py` 변경 없음. tracker 미사용. `register_position`이 새 `status` 파라미터를 받지만 기본값이 "pending"이므로 backtest 경로 영향 없음.

다만 backtester의 register_position 호출이 status를 명시적으로 'confirmed'로 전달하도록 변경하여 의미론적 명확성 확보:
```python
# backtester.py (해당 위치)
self._risk_manager.register_position(..., status="confirmed")
```

(미변경 시 기본값 "pending"이라 backtest 종료 시 모든 포지션이 pending으로 보일 수 있음 — 무해하지만 보기 안 좋음.)

### 5.7 `core/paper_order_manager.py` 무변경

원칙대로 PaperOrderManager는 손대지 않는다. paper_mode 분기는 engine_worker가 담당.

## 6. 로깅 규약

| 사유 | 레벨 | 포맷 |
|------|------|------|
| 주문 등록 | info | `[ORDER-TRACK] {order_no} SUBMIT {ticker} {side} {qty}` |
| 부분 체결 | info | `[ORDER-TRACK] {order_no} PARTIAL {filled_qty}/{requested_qty} @ {price:,}` |
| 완전 체결 | info | `[ORDER-TRACK] {order_no} FILL {filled_qty}/{requested_qty} @ {price:,} (status=filled)` |
| 매도 정산 | info | `[ORDER-TRACK] {order_no} FILLED → settle_sell {ticker}` |
| 매수 확정 | info | `[ORDER-TRACK] {order_no} FILLED → mark_confirmed {ticker}` |
| 타임아웃 | warning | `[ORDER-TRACK] {order_no} TIMEOUT — REST 폴백` |
| 연속 TIMEOUT 임계 | warning (텔레그램) | `[ORDER-TRACK][CRITICAL] {ticker} 연속 TIMEOUT {N}회` |
| 재진입 가드 발동 | debug | `[ORDER-TRACK] {ticker} pending {side} — exit 스킵` |
| limit_up_exit TIMEOUT | warning | `[ORDER-TRACK] limit_up_exit TIMEOUT → stop 상향: {ticker}` |

## 7. 테스트

### 7.1 `tests/test_order_tracker.py` (신규, 단위)

1. `submit` → status PENDING + `get_pending(ticker)` 비None
2. `on_fill(full qty)` → FILLED + `get_pending(ticker)` None (재진입 가능)
3. `on_fill(partial)` → PARTIAL → `get_pending` 비None
4. `on_fill(partial)` 추가 → FILLED 완료
5. FILLED 후 추가 `on_fill` → 경고 + 무시 (중복 통보 방어)
6. FAILED 후 `on_fill` → 무시
7. `get_unfilled_older_than(0)` → 즉시 PENDING/PARTIAL 모두 반환
8. `mark_timeout` → status=TIMEOUT, `get_pending` None
9. `remove` 후 `get_by_order_no` None
10. 알 수 없는 order_no `on_fill` → None 반환

### 7.2 `tests/test_engine_worker_order_tracking.py` (신규, 통합)

VIHandler 스타일을 따라 EngineWorker 전체 부팅 없이 OrderTracker + Risk Manager 모의로 시나리오 검증:

11. **real_mode 매수 시나리오**:
    - submit → register_position(status='pending')
    - on_fill (full) → mark_confirmed
    - Position dict status='confirmed' 확인
12. **real_mode 매도 시나리오**:
    - submit (side='sell') → settle_sell 호출 안 됨
    - on_fill → settle_sell 호출 (한 번만)
13. **paper_mode 시나리오**:
    - tracker.submit 미호출 → settle_sell 즉시 호출 (현 동작)
14. **재진입 가드**:
    - sell submit 상태에서 get_pending → 비None → exit 분기 스킵
15. **TIMEOUT → 매수 cancel + 알림**:
    - submit 후 11초 경과 → get_unfilled_older_than(10) 1건
    - mark_timeout 후 get_pending None
16. **TIMEOUT → 매도 자연 재시도**:
    - 매도 submit 후 TIMEOUT → mark_timeout → 다음 tick에서 stop_loss 재트리거 가능 (즉 get_pending None)

### 7.3 기존 테스트 회귀

- `tests/test_order_manager.py` — OrderManager는 REST 요청만 담당하므로 변경 없음. 회귀 0건 보장.
- `tests/test_risk_manager.py` — register_position에 status 파라미터 추가했으므로 기본값 호출이 여전히 동작하는지 확인. `mark_confirmed` 헬퍼 단위 테스트 추가.

## 8. 에러 처리

| 상황 | 처리 |
|------|------|
| WS "00" 메시지의 order_no 필드 누락 | warning 로그 + 스킵 |
| 알 수 없는 order_no 체결 통보 | warning 로그 + 무시 (현재 추적 안 하는 주문) |
| `on_fill` 시 filled_qty가 음수 또는 0 | warning + 무시 |
| timeout_checker REST 폴백 예외 | logger.error + 다음 주기 재시도 |
| `cancel_order` REST 실패 | logger.error + 텔레그램 알림 |
| `_order_tracker = None` 상태에서 consumer 동작 | debug 로그 + skip (paper_mode 또는 초기화 전) |
| 매도 TIMEOUT 후 같은 ticker가 또 매도 발생 → tracker 새 entry | 정상 (자연 재시도) |
| 연속 TIMEOUT 임계(3회) 초과 | 추가 텔레그램 알림 (1분 cooldown은 후속) |

## 9. 위험 / 트레이드오프

- **paper_mode 운영 중**: tracker 코드 실 검증은 단위 테스트로만. 실모드 전환 전 raw WS "00" 페이로드 캡처 + 필드 확정이 필수.
- **WS "00" 필드 코드 미확정**: `9001`/`10`/`900` 추정값으로 시작. 운영 시점 페이로드 1회 캡처로 확정 → 후속 PR. 잘못된 필드 코드는 `on_fill`이 호출되지 않아 모든 매도가 TIMEOUT으로 흘러가지만, 자연 재시도 + 알림으로 운영 가시성 확보.
- **타임아웃 무한 루프**: 같은 ticker가 계속 TIMEOUT되면 매 10초마다 새 주문 시도. 임계 3회 후 텔레그램 알림은 있으나 시도는 계속됨. 1분 cooldown은 후속 작업으로 분리.
- **재시작 시 진행 중 주문 분실**: 인메모리. 엔진 재시작 직후 키움 잔고 / 미체결 조회 후 수동 확인 권장.
- **backtester 영향**: status 파라미터 기본값 "pending" → backtester 코드 명시적으로 "confirmed" 전달. 미변경 시 시뮬 결과는 동일하지만 의미가 모호. **백테스트 baseline PF 4.36 변동 없음 보장**.
- **WS "00" vs 부분체결**: 키움 WS "00"이 부분체결마다 발송된다는 가정. 1번에 누적 발송이면 `on_fill`의 누적 로직이 부분체결을 PARTIAL로 인식 못함. 페이로드 캡처 후 확정.

## 10. 변경 파일 목록

신규:
- `core/order_tracker.py`
- `tests/test_order_tracker.py`
- `tests/test_engine_worker_order_tracking.py`

수정:
- `config.yaml` (trading 섹션 2개 신규 키)
- `config/settings.py` (TradingConfig 2개 필드)
- `core/kiwoom_rest.py` (`cancel_order` 메서드)
- `risk/risk_manager.py` (`register_position` status 파라미터 + `mark_confirmed` 헬퍼)
- `gui/workers/engine_worker.py` (7지점: __init__, _run_engine, _signal_consumer, _tick_consumer×2, _force_close, _order_confirmation_consumer + 신규 _order_tracker_timeout_checker + _handle_fill + _verify_fill_via_rest 헬퍼)
- `backtest/backtester.py` (register_position 호출에 status="confirmed" 명시)
- `tests/test_risk_manager.py` (register_position + mark_confirmed 회귀 케이스)

## 11. 향후 확장 포인트

- **연속 TIMEOUT cooldown** — 같은 ticker가 N회 TIMEOUT 후 1분간 신규 매도 시도 중지 (현재는 알림만)
- **WS "00" 필드 확정** — 운영 전 실 페이로드 캡처 → 상수 갱신
- **재시작 복구** — 엔진 시작 시 키움 잔고 + 미체결 조회 → tracker 자동 복원
- **부분체결 가중평균 가격 정밀화** — 현재는 단순 last-fill 가격 사용 가능. VWAP 계산 추가
- **체결 확인 알림 토글** — ADR-008 notifications 섹션에 `order_tracking_critical` 추가

## 12. 비범위 / 명시적 금지

- PaperOrderManager 로직 변경 금지
- `register_position` 즉시 호출 제거 금지 (매수 시점에 status='pending'으로 등록만)
- 키움 WS "00" 필드 코드를 확정적으로 단언 금지 (모두 TODO 주석)
- backtester에 tracker 도입 금지
- limit_up_exit의 `raise_stop_to_limit_up_floor` 메커니즘 변경 금지
- VI Handler와의 통합 변경 금지 (현 prefer_best_limit / on_rejection 흐름 그대로)
