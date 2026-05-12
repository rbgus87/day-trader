# VI Handler 설계 — 변동성완화장치 휴리스틱 감지 및 주문 전환

> 작성: 2026-05-12
> 상태: 승인됨 (사용자 검토 대기)
> 관련: ADR-018 (상한가 즉시 청산), CLAUDE.md baseline

## 1. 배경

한국거래소(KRX)는 가격 급변 시 2분 + 랜덤종료 30초 동안 단일가 매매로 전환하는 **변동성완화장치(VI)** 를 운용한다.

- **정적VI**: 직전 단일가/체결가 대비 전일종가가 ±10% 변동 시 (KOSPI200은 ±6%) 발동
- **동적VI**: 직전 체결가 대비 ±3~6% 변동 시 발동

day-trader는 KOSPI/KOSDAQ **모멘텀 급등주**를 매매하므로 VI 발동 빈도가 높을 수밖에 없는 종목군이다. 현재 시스템은 VI 감지/대응 로직이 전혀 없어 다음 문제가 가능하다.

1. VI 발동 중 시장가 주문이 거부됨 → 손절 실패 → 포지션 불일치
2. VI 직후 단일가 매매 구간에 진입 신호가 발생 → 추격 매수
3. 강제청산(15:10) 시 VI 종목이 미청산 상태로 일과 마감

## 2. 목표 / 비목표

### 목표
- VI 발동(또는 임박) 추정 시 **시장가 주문을 최유리지정가(키움 코드 "06")로 자동 전환**
- VI 활성 종목 **신규 매수 차단**
- baseline PF의 38.4%를 기여하는 `limit_up_exit` 엣지(ADR-018) **보호**
- 향후 WS "0A"(기세) 또는 REST `vi_cls_code` 통합 여지를 위한 **확장 포인트** 제공

### 비목표
- "VI 턴" 매매 전략 (불공정거래 리스크)
- 100% 정확한 VI 상태 판정 (보수적 false negative 수용)
- 기존 전략 로직(generate_signal, trailing stop) 변경

## 3. 설계 원칙

1. **limit_up_exit 우선** — 상한가(+30%) 도달 시 VI 판정과 무관하게 즉시 매도. ADR-018 안전망(체결 실패 시 stop을 상한가×0.99로 상향)이 이미 존재한다.
2. **forced_close 무차단** — 15:10 강제청산은 보류·재시도 없이 진행. 단 시장가 → 최유리지정가 전환만 적용.
3. **주문 차단이 아니라 전환** — VI 의심 시에도 주문 자체는 항상 시도. 시장가 → 최유리지정가(키움 "06") 변환만 수행.
4. **휴리스틱은 보수적** — false positive(거래 기회 손실)가 false negative(주문 거부 1회)보다 손해가 크다. 정적VI 추정 임계 ±9.5%.
5. **주문 거부가 가장 정확한 VI 신호** — REST 응답 `rt_cd ≠ "0"` 시 즉시 SUSPECTED 상태로 전환. 휴리스틱은 보조.
6. **VIHandler는 무상태 외부 의존성 없음** — 인메모리 dict만 사용. 재시작 시 초기화.

## 4. 아키텍처

```
                   ┌──────────────────────────────────────┐
[WS tick 0B]  ───▶ │ engine_worker._tick_consumer         │
                   │   ├─ vi_handler.update_from_tick()   │
                   │   ├─ check_limit_up (변경 없음)        │  ── ADR-018 보호
                   │   └─ check_stop_loss                  │
                   │        └─ prefer_best_limit=         │
                   │             vi.should_use_best_limit │
                   └────────────────┬─────────────────────┘
                                    │
[REST 주문 응답]  ────────────────┐  ▼
                                  │ OrderManager._send_order
                                  │   └─ rt_cd ≠ "0" 시
                                  └──▶ vi_handler.flag_suspected(reason)

[signal_queue] ───▶ engine_worker._signal_consumer
                       └─ if vi_handler.is_vi_active(t): block

[15:10 cron]   ───▶ engine_worker._force_close
                       └─ prefer_best_limit 적용 (대기/재시도 없음)

[향후]  WS "0A" ─▶ vi_handler.update_from_ws_0a (현재는 stub pass)
        REST ka10001.vi_cls_code ─▶ 동일 hook
```

## 5. 컴포넌트

### 5.1 `core/vi_handler.py` (신규)

```python
from datetime import datetime, timedelta
from enum import Enum
from loguru import logger

class VIState(Enum):
    NORMAL = "normal"
    STATIC_VI = "static_vi"   # 가격 휴리스틱 기반 추정
    SUSPECTED = "suspected"   # REST 주문 거부 기반

class VIHandler:
    """VI 발동 추정 + 주문 전환 의사결정 (인메모리, 보수적 휴리스틱)."""

    def __init__(
        self,
        static_pct: float = 0.095,         # 정적VI 추정 임계 (전일종가 대비)
        assumed_duration_sec: int = 150,   # 추정 지속시간 (2분 + 랜덤 30초)
        suspected_duration_sec: int = 60,  # SUSPECTED 만료 (키움 일시 장애 대비)
    ): ...

    # 상태 갱신
    def update_from_tick(self, ticker: str, price: float, prev_close: float) -> None
    def update_from_ws_0a(self, ticker: str, payload: dict) -> None  # 스텁 (TODO)
    def flag_suspected(self, ticker: str, reason: str) -> None

    # 조회
    def is_vi_active(self, ticker: str) -> bool          # 매수 차단용
    def should_use_best_limit(self, ticker: str) -> bool # 시장가 → "06" 전환용
    def get_vi_state(self, ticker: str) -> VIState
```

#### 5.1.1 휴리스틱 (`update_from_tick`)
```
change_pct = (price - prev_close) / prev_close
limit_up_price = prev_close * 1.30   # 상한가 (호가 절사 무시)

if abs(change_pct) >= static_pct and price < limit_up_price:
    state = STATIC_VI, expires_at = now + assumed_duration_sec
else:
    (변동 없음)
```

상한가 도달 종목(`price >= limit_up_price * 0.99`)은 휴리스틱 대상에서 제외 → `limit_up_exit` 경로가 항상 우선 실행되도록 보장. 단, `check_limit_up` 자체는 engine_worker가 VI 무관하게 호출하므로 이중 안전망.

#### 5.1.2 만료 (lazy)
모든 조회 메서드(`is_vi_active`, `should_use_best_limit`, `get_vi_state`)는 호출 시점에 expires_at을 비교해 자동 NORMAL 복귀. 별도 cron 불필요.

#### 5.1.3 `should_use_best_limit` 매트릭스
| state | 만료 전 | 만료 후 |
|-------|---------|---------|
| NORMAL | False | False |
| STATIC_VI | True | False |
| SUSPECTED | True | False |

#### 5.1.4 `is_vi_active` (매수 차단)
`get_vi_state(ticker) != NORMAL` 와 동치. SUSPECTED도 매수 차단 대상.

#### 5.1.5 `update_from_ws_0a` 스텁
```python
def update_from_ws_0a(self, ticker: str, payload: dict) -> None:
    """TODO: 키움 WS '0A'(기세) 메시지의 VI 발동 필드 확정 후 구현.
    실제 페이로드 샘플 수집 → 단위 테스트 추가 → 본문 작성."""
    pass
```

### 5.2 `config.yaml` 신규 키 (trading 섹션)

```yaml
trading:
  # VI 휴리스틱 (정적VI 추정용; 동적VI는 현재 미사용)
  vi_static_pct: 0.095            # 전일종가 대비 ±9.5% 이상 → STATIC_VI 추정
  vi_assumed_duration_sec: 150    # 2분 + 랜덤 30초
  vi_suspected_duration_sec: 60   # 주문 거부 기반 SUSPECTED 만료
```

`config/settings.py:TradingConfig`에 동일 필드 추가 (기본값 동일).

### 5.3 `core/kiwoom_rest.py` 신규 상수

```python
PRICE_BEST_LIMIT = "06"   # 최유리지정가 (매도: 매수1호가 / 매수: 매도1호가)
```

### 5.4 `core/order_manager.py` 변경

```python
_ORDER_TYPE_TO_KIWOOM = {
    "limit": PRICE_LIMIT,
    "market": PRICE_MARKET,
    "best_limit": PRICE_BEST_LIMIT,
}

async def _send_order(
    self, ticker, qty, price, side,
    order_type: str = "limit",
    prefer_best_limit: bool = False,   # 신규
    reason: str = "", strategy="", pnl=0, pnl_pct=0,
    on_rejection: Callable[[str, str], None] | None = None,  # 신규
):
    effective_type = order_type
    if prefer_best_limit and order_type == "market":
        effective_type = "best_limit"
        logger.info(f"[VI] {ticker} 매도 → 최유리지정가 전환")

    result = await self._rest.order(..., order_type=_kiwoom_code(effective_type))
    rt_cd = result.get("rt_cd") if result else None
    if rt_cd is not None and rt_cd != "0" and on_rejection is not None:
        on_rejection(ticker, rt_cd)

    # DB 기록: effective_type 그대로 저장
    ...
```

`execute_sell_stop`, `execute_sell_force_close`에 `prefer_best_limit`, `on_rejection` 파라미터를 패스스루로 추가. `execute_sell_tp1`/매수는 변경 없음.

**DB 영향**: `trades.order_type` 컬럼에 새 값 `'best_limit'` 등장 가능. 컬럼 타입은 TEXT라 마이그레이션 불필요. 대시보드/리포트가 enum 검증 시 추가 처리 필요한지 후속 확인.

### 5.5 `gui/workers/engine_worker.py` 변경

#### 5.5.1 `__init__`
```python
from core.vi_handler import VIHandler
self._vi_handler = VIHandler(
    static_pct=trading_cfg.vi_static_pct,
    assumed_duration_sec=trading_cfg.vi_assumed_duration_sec,
    suspected_duration_sec=trading_cfg.vi_suspected_duration_sec,
)
self._prev_close_cache: dict[str, float] = {}   # 전일종가 캐시 (08:05 OHLCV 시점에 채움)
```

#### 5.5.2 `_tick_consumer` 통합 (3곳)

**(a)** 가격 갱신 직후, `check_limit_up` 호출 전:
```python
prev_close = self._prev_close_cache.get(ticker)
if prev_close:
    self._vi_handler.update_from_tick(ticker, price, prev_close)
```

**(b)** `check_limit_up` 분기 — **변경 없음** (ADR-018 보호). `limit_up_exit` 시 `execute_sell_stop`에 `prefer_best_limit=False` 명시 전달.

**(c)** `check_stop_loss` 분기:
```python
prefer_best = self._vi_handler.should_use_best_limit(ticker)
await self._order_manager.execute_sell_stop(
    ..., prefer_best_limit=prefer_best,
    on_rejection=lambda tk, rt: self._vi_handler.flag_suspected(tk, f"rt_cd={rt}"),
)
```

#### 5.5.3 `_signal_consumer` 통합 (매수 직전 1지점)
```python
if self._vi_handler.is_vi_active(ticker):
    logger.info(f"[VI] {ticker} 매수 차단 — state={self._vi_handler.get_vi_state(ticker).value}")
    continue
```

#### 5.5.4 `_force_close` 통합
```python
prefer_best = self._vi_handler.should_use_best_limit(ticker)
await self._order_manager.execute_sell_force_close(
    ..., prefer_best_limit=prefer_best,
    on_rejection=lambda tk, rt: self._vi_handler.flag_suspected(tk, f"rt_cd={rt}"),
)
```

대기/재시도 로직은 추가하지 않음. spec 원안의 "최대 2분 대기 × 2회 + 15:15 최종 시도"는 사용자 추가 지시("주문 차단 안 함, 전환만")에 따라 제거.

#### 5.5.5 `_prev_close_cache` 채우기
08:05 자동화 잡(`apply_prev_ohlcv` 등)이 종료된 직후 또는 종목 구독 시점에 `risk_manager.get_prev_close(ticker)` 또는 DB `daily_candles` 최신값 조회. 구체 구현은 implementation plan에서 결정.

### 5.6 `risk_manager.py` — 변경 없음

사용자 결정에 따라 `vi_cooldown_until` 필드는 도입하지 않음. VIHandler가 단일 진실 공급원.

## 6. 로깅 규약

모두 `logger.info("[VI] ...")` 또는 `logger.warning("[VI] ...")`. 형식:

| 사유 | 레벨 | 포맷 |
|------|------|------|
| STATIC 추정 | info | `[VI] {ticker} STATIC 추정 — change={pct:+.2f}%, expires_at={hh:mm:ss}` |
| SUSPECTED 활성 | warning | `[VI] {ticker} SUSPECTED — 주문 거부 (rt_cd={rt})` |
| 매도 전환 | info | `[VI] {ticker} 매도 → 최유리지정가 전환` |
| 매수 차단 | info | `[VI] {ticker} 매수 차단 — state={state}` |
| lazy 해제 | debug | `[VI] {ticker} 만료 → NORMAL` |

## 7. 테스트

### 7.1 `tests/test_vi_handler.py` (신규, 단위)
1. `update_from_tick` change_pct=+0.095 → STATIC_VI
2. `update_from_tick` change_pct=+0.094 → NORMAL 유지
3. `update_from_tick` 상한가 도달(+0.30) → NORMAL 유지 (limit_up_exit 보호)
4. STATIC_VI 후 assumed_duration_sec 경과 → 조회 시 NORMAL 자동 복귀
5. `flag_suspected` → SUSPECTED + `should_use_best_limit() == True`
6. `update_from_ws_0a({"ticker": "X", "payload": ...})` 호출 무예외
7. `is_vi_active`/`should_use_best_limit` 매트릭스 (3 state × 만료 전후 = 6 케이스)
8. config 임계값 주입 시 임계 적용 (static_pct=0.05 → +5.1%에서 발동)

### 7.2 `tests/test_order_manager.py` 추가 케이스
9. `_send_order(order_type="market", prefer_best_limit=True)` → 키움 코드 "06" 호출 확인 (mock)
10. `_send_order` 응답 rt_cd="9" → `on_rejection` 콜백 호출 확인

### 7.3 `tests/test_engine_worker_vi.py` (신규, 통합)
11. `_signal_consumer` 매수 신호 + vi_handler.is_vi_active=True → 매수 미실행
12. `_tick_consumer` check_limit_up 경로는 `prefer_best_limit=False` (강제 시장가)
13. `_tick_consumer` check_stop_loss + VI 활성 → `prefer_best_limit=True` 전달

기존 248개 테스트 회귀 없음 보장. `pytest tests/` 전체 통과 + `python selftest.py` 7/7 OK.

## 8. 에러 처리

| 상황 | 처리 |
|------|------|
| `prev_close_cache` 미스 | `update_from_tick` 호출 자체 스킵 (조용히) |
| VIHandler 내부 예외 | _tick_consumer가 try/except로 잡고 warning 로그. 매매 진행은 막지 않음 |
| 키움 REST 응답 파싱 실패 (rt_cd 추출 불가) | `flag_suspected` 호출 X (모르면 NORMAL 유지) |
| best_limit 주문도 거부 | OrderManager가 기존 실패 경로로 처리. VI 해제까지 대기 로직 없음 (사용자 결정) |

## 9. 위험 / 트레이드오프

- **±9.5% 임계의 false negative**: KOSPI200 ±6% VI는 휴리스틱이 감지 못함. 그러나 사용자 결정으로 false positive 회피가 우선. 향후 WS "0A" 통합 시 보완.
- **SUSPECTED 60초 만료**: 키움 일시 장애로 인한 거부를 영구 VI로 오인하는 것을 방지. 60초는 단일가 매매(150초)보다 짧지만, 재거부 시 재플래그되므로 회복력 있음.
- **DB `order_type` 새 값 `'best_limit'`**: 컬럼은 TEXT라 마이그레이션 불필요. 후속 리포트/대시보드 호환 확인 필요.
- **`_prev_close_cache` 일관성**: 08:05 OHLCV 갱신 잡과 동기화 필요. 캐시 미스 시 휴리스틱이 동작하지 않으므로, SUSPECTED 경로(주문 거부 기반)가 안전망 역할.

## 10. 향후 확장 포인트

- `update_from_ws_0a` 본문 구현 (WS "0A" 페이로드 샘플 확보 후)
- `update_from_rest_ka10001` 추가 — `vi_cls_code` 필드 사용 (필드명 확정 후)
- 동적VI 감지 — 직전 체결가 대비 변동 계산 (현재 미구현)
- `risk_manager.vi_cooldown_until` 도입 (스프레드 확대 대응) — 현재는 보류

## 11. 변경 파일 목록

신규:
- `core/vi_handler.py`
- `tests/test_vi_handler.py`
- `tests/test_engine_worker_vi.py`

수정:
- `config.yaml` (trading 섹션 3개 신규 키)
- `config/settings.py` (TradingConfig 3개 필드)
- `core/kiwoom_rest.py` (PRICE_BEST_LIMIT 상수)
- `core/order_manager.py` (_send_order, execute_sell_stop, execute_sell_force_close 시그니처 확장)
- `gui/workers/engine_worker.py` (5.5절 통합 지점)
- `tests/test_order_manager.py` (추가 케이스 2개)

검증:
- `pytest tests/` 전체 통과
- `python selftest.py` 7/7 OK
- `grep -F "[VI]" gui/ core/` 로 로깅 일관성 확인 (대괄호 리터럴)

## 12. 비범위 / 명시적 금지

- **VI 턴 매매 전략** (불공정거래 리스크)
- **MomentumStrategy.generate_signal 수정 금지**
- **trailing stop/breakeven 로직 수정 금지**
- **risk_manager 인터페이스 변경 금지** (vi_cooldown 도입 보류)
- **vi_static_pct/vi_assumed_duration_sec 하드코딩 금지** (반드시 config 외부화)
