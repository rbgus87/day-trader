# SURGERY_GUIDE.md — day-trader 수술 가이드

> **최종 수정**: 2026-03-29
> 코드베이스 전수 감사 결과 기반 체계적 수술 계획.
> Claude Code CLI에서 SURGERY_PROMPTS.md의 프롬프트를 순서대로 실행.

---

## 감사 요약

| 항목 | 수치 |
|------|------|
| 총 코드 | 11,620 lines (Python) |
| 커밋 | 34 |
| 테스트 | 125 (23 파일) |
| CRITICAL 버그 | 4건 |
| HIGH 버그 | 4건 |
| MEDIUM 버그 | 5건 |

---

## Phase 1 — CRITICAL 수정 (실매매 블로커 제거)

### FIX-1: 실시간 포지션 모니터링 파이프라인 추가

**문제**: 손절/TP1/트레일링 스톱이 실시간으로 작동하지 않음.
`risk_manager`에 메서드는 있으나 파이프라인에서 호출하는 곳이 없음.

**수정 범위**:
- `main.py` — `tick_consumer()` 또는 별도 `position_monitor()` 태스크 추가
- `gui/workers/engine_worker.py` — 동일하게 적용

**수정 방향**:
```
tick_consumer에서:
  1. 틱 수신 시 해당 종목 포지션 존재 여부 확인
  2. check_stop_loss() → True이면 execute_sell_stop() 즉시 호출
  3. check_tp1() → True이면 execute_sell_tp1() 호출 + mark_tp1_hit()
  4. update_trailing_stop() 호출 (TP1 히트 후 고점 갱신)
```

**주의**: tick_consumer는 candle_builder.on_tick()도 호출해야 하므로,
포지션 모니터링이 캔들 빌딩을 블로킹하면 안 됨.
→ 별도 태스크로 분리하거나, tick을 양쪽 Queue로 fan-out 하는 구조 권장.

**검증**:
- 테스트: mock tick으로 stop_loss 가격 전달 → execute_sell_stop 호출 확인
- 테스트: mock tick으로 tp1 가격 전달 → execute_sell_tp1 호출 확인
- 기존 테스트 깨지지 않는지 확인

---

### FIX-2: config/parameter 일관성 수술

**문제**: `settings.py` 기본값 ↔ `config.yaml` 최적화 값 ↔ PRD 사이 불일치.

**불일치 항목 전체 목록**:

| 파라미터 | settings.py 기본값 | config.yaml 값 | 올바른 값 |
|---------|-------------------|----------------|----------|
| tp1_pct | 0.02 | 0.03 | **0.03** (최적화) |
| max_trades_per_day | 5 | 3 | **3** (최적화) |
| cooldown_minutes | 10 | 15 | **15** (최적화) |
| orb_volume_ratio | **1.0** | 0.0 | **0.0** (비활성화) |
| pullback_min_gain_pct | 0.03 | 0.04 | **0.04** (최적화) |
| pullback_stop_loss_pct | -0.015 | -0.018 | **-0.018** (최적화) |
| momentum_stop_loss_pct | -0.015 | -0.008 | **-0.008** (최적화) |

**수정 방향**:
1. `settings.py`의 TradingConfig 기본값을 config.yaml 최적화 결과로 통일
2. `from_yaml()` 160행: `orb_volume_ratio` 기본값을 `0.0`으로 변경
3. 원칙: **config.yaml이 진실의 원천(source of truth)**, settings.py 기본값은 yaml 미로드 시 안전 폴백

**검증**:
- `TradingConfig()` (기본 생성) vs `AppConfig.from_yaml()` 값 비교 테스트 추가
- 기존 전략 테스트가 config 기본값에 의존하는 경우 수정

---

### FIX-3: initial_capital → risk_manager 연결

**문제**: config의 자본금이 risk_manager에 전달되지 않아 포지션 사이징이 10배 과대.

**수정 범위**:
- `main.py` 113행 부근: risk_manager 생성 후 `set_daily_capital(config.trading.initial_capital)` 호출
- `gui/workers/engine_worker.py`: 동일
- `config/settings.py`: TradingConfig에 `initial_capital` 필드 추가 (현재 누락)
- `config.yaml` → `settings.py` 로딩에 initial_capital 매핑 추가

**현재 상태**:
- `config.yaml`에 `trading.initial_capital: 1_000_000` 존재
- `TradingConfig`에 initial_capital 필드 **없음**
- `signal_consumer`에서 `capital <= 0`일 때 하드코딩 10,000,000 사용

**수정 방향**:
```python
# settings.py TradingConfig에 추가
initial_capital: int = 1_000_000

# main.py에서 연결
risk_manager.set_daily_capital(config.trading.initial_capital)
```

**검증**:
- risk_manager.available_capital이 config 값과 일치하는지 테스트
- signal_consumer의 하드코딩 폴백 제거 또는 경고 로그 추가

---

### FIX-4: OrderManager (실매매) DB 기록 추가

**문제**: 실매매 모드에서 체결 내역이 DB에 기록되지 않음.

**수정 범위**: `core/order_manager.py`

**수정 방향**:
- `execute_buy()`, `_send_order()` 성공 시 trades 테이블에 INSERT
- PaperOrderManager와 동일한 스키마 사용
- strategy 필드는 signal에서 전달받거나, 호출자가 전달

**주의**: OrderManager에 `db: DbManager` 의존성은 이미 생성자에 있음 (사용 안 할 뿐).

**검증**:
- execute_buy 후 trades 테이블에 행 존재 확인
- daily_report가 실매매 데이터로 정상 생성되는지 확인

---

## Phase 2 — HIGH 수정 (백테스트 신뢰성)

### FIX-5: 백테스트 비용 모델 config.yaml 연동

**문제**: 하드코딩 슬리피지(0.005%)와 config.yaml(0.03%)이 6배 차이.

**수정 범위**: `backtest/backtester.py`, `backtest/run_all_strategies.py`

**수정 방향**:
1. Backtester 생성 시 config.yaml의 backtest 섹션에서 값 로드
2. `settings.py`에 BacktestConfig dataclass 추가 (또는 AppConfig에 포함)
3. 모듈 레벨 상수(`ENTRY_FEE_RATE` 등)를 deprecated 마킹, 생성자 파라미터 우선

```python
@dataclass(frozen=True)
class BacktestConfig:
    commission: float = 0.00015
    tax: float = 0.0018
    slippage: float = 0.0003     # config.yaml 값 기준
    initial_capital: int = 1_000_000
```

**검증**:
- 동일 데이터로 수정 전/후 백테스트 결과 비교 (슬리피지 영향 확인)

---

### FIX-6: PaperOrderManager strategy 필드 수정

**문제**: 모든 거래가 strategy='paper'로 기록됨.

**수정 범위**: `core/paper_order_manager.py`

**수정 방향**:
- `execute_buy()`, `_simulate_order()`에 `strategy: str` 파라미터 추가
- 호출자(signal_consumer)에서 `active_strategy`의 전략명 전달
- 하위 호환: 기본값 `strategy="unknown"` 설정

**연쇄 수정**:
- `main.py` signal_consumer: execute_buy 호출 시 전략명 전달
- `gui/workers/engine_worker.py`: 동일
- OrderManager도 동일하게 strategy 파라미터 추가 (FIX-4와 함께)

---

### FIX-7: 백테스터 분할매매 시뮬레이션

**문제**: 실전의 분할 매수/매도가 백테스트에 미반영.

**수정 범위**: `backtest/backtester.py` `run_backtest()`

**수정 방향** (복잡도 높음, 단계적 접근):

**단계 A — TP1 분할 매도만 우선 구현**:
```
진입 시: 전체 자금으로 진입 (현행 유지)
TP1 도달 시: 50% 청산 + 손절선 본전 이동 + 나머지 trailing
trailing stop 또는 forced_close로 나머지 청산
```

**단계 B — 분할 매수는 Phase 4에서 전략 재설계 시 함께 검토**:
- 분할 매수의 2차 진입 조건이 전략별로 다르므로 단순 비율 분할보다 복잡
- 현 단계에서는 all-in 진입 + 분할 청산이 현실적

**검증**:
- TP1 히트 시 pnl 계산이 50% 물량 기준인지 확인
- 나머지 물량의 trailing stop 또는 forced_close pnl이 별도 계산되는지 확인

---

### FIX-8: StrategySelector 임계값 정리

**문제**: config.yaml / strategy_selector.py DEFAULT / PRD 사이 3중 불일치.

**수정 방향**:
1. PRD 원본값(0.5/1.5/0.5)과 config.yaml 최적화값(0.8/2.0/0.8) 중 선택
2. `strategy_selector.py`의 DEFAULT 상수를 config.yaml과 통일
3. 원칙: config.yaml이 source of truth

---

## Phase 3 — MEDIUM 수정 (운영 안정성)

### FIX-9: CandleBuilder ts 포맷에 날짜 포함

**수정 방향**: 틱의 날짜 정보를 활용하거나 `datetime.now().date()` 결합
```python
# 현재: "09:05:00"
# 변경: "2026-03-29T09:05:00"
```

### FIX-10: market_calendar 연도 확장

**수정 방향**: `holidays` 패키지 도입 또는 2027년 공휴일 추가.
최소한 현재 연도 +1년까지는 커버.

### FIX-11: force_close private dict 접근 제거

**수정 방향**: `risk_manager`에 `get_open_positions() -> dict` public 메서드 추가.

### FIX-12: DB 백업 메커니즘

**수정 방향**: 스케줄러에 일 1회(15:35) `daytrader.db` → `daytrader_backup_{date}.db` 복사 추가.
7일 보관 후 자동 삭제.

### FIX-13: 수급 데이터 placeholder 표시

**수정 방향**: 당장 구현 불가하면 로그에 "수급 데이터 미구현" 명시적 경고 추가.
향후 키움 API 투자자별 매매동향 조회 연동.

---

## Phase 4 — 전략 재설계 (Phase 1~3 완료 + 페이퍼 트레이딩 데이터 확보 후)

> **주의**: Phase 4는 Phase 1~3 수술이 완료되고, 최소 2주 이상의
> 페이퍼 트레이딩 데이터가 축적된 후에 착수한다.

- 4개 전략의 실전 성과 데이터 기반 전략 재편성 검토
- 분할 매수 로직 전략별 세분화
- 수급 데이터 통합 후 스크리닝 고도화
- 백테스트 데이터 수집 완료 후 walk-forward 검증

---

## 수술 실행 순서

```
Phase 1 (CRITICAL):
  Prompt 1 → FIX-2 (config 일관성) + FIX-3 (initial_capital)
  Prompt 2 → FIX-1 (실시간 포지션 모니터링)
  Prompt 3 → FIX-4 (OrderManager DB 기록) + FIX-6 (strategy 필드)

Phase 2 (HIGH):
  Prompt 4 → FIX-5 (백테스트 비용 모델)
  Prompt 5 → FIX-7 (백테스터 분할매도) + FIX-8 (selector 임계값)

Phase 3 (MEDIUM):
  Prompt 6 → FIX-9~13 일괄
```

각 Prompt는 `docs/SURGERY_PROMPTS.md`에 copy-paste 가능한 형태로 제공.
