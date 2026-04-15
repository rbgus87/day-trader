# CLAUDE.md — day-trader CLI 컨텍스트 브릿지

> **최종 수정**: 2026-03-29
> Claude Code CLI가 자동으로 읽는 루트 레벨 컨텍스트 파일.
> `docs/` 하위가 아닌 **프로젝트 루트**에 위치해야 CLI가 인식합니다.

---

## 프로젝트 개요

키움증권 REST API + WebSocket 기반 **단타(데이) 자동매매 시스템**.
하루 1종목 선별 → 4개 전략 중 자동 선택 → 당일 매매 → 15:10 강제 청산.

| 항목 | 내용 |
|------|------|
| 레포 | `rbgus87/day-trader` |
| Python | 3.14 (시스템), 백테스트는 3.12 |
| DB | SQLite `daytrader.db` (quant.db/swing.db와 완전 분리) |
| 브로커 | 키움 OpenAPI+ REST + WebSocket |
| 알림 | Telegram |
| 이벤트 루프 | asyncio + `WindowsSelectorEventLoopPolicy` (Windows) |
| GUI | PyQt6 (Catppuccin Mocha 테마) |
| 상태 | 34 commits, 11,620 lines, 테스트 125개 |

---

## 디렉토리 구조

```
day-trader/
├── main.py                  # 엔트리포인트 (asyncio 파이프라인)
├── gui.py                   # GUI 엔트리포인트
├── config.yaml              # 매매 파라미터 (최적화 결과 반영)
├── config/
│   ├── settings.py          # AppConfig dataclass (yaml+.env 통합)
│   └── universe.yaml        # 스크리닝 대상 48종목
├── core/
│   ├── auth.py              # OAuth2 토큰 관리
│   ├── kiwoom_rest.py       # REST 클라이언트 (주문/조회/시장스냅샷)
│   ├── kiwoom_ws.py         # WebSocket (체결/호가/체결통보)
│   ├── order_manager.py     # 실매매 주문 실행기
│   ├── paper_order_manager.py  # 페이퍼 주문 시뮬레이터
│   ├── rate_limiter.py      # 슬라이딩 윈도우 Rate Limiter
│   └── retry.py             # Exponential Backoff + Jitter
├── data/
│   ├── candle_builder.py    # 틱→1분봉/5분봉+VWAP 실시간 생성
│   └── db_manager.py        # aiosqlite CRUD + 스키마
├── strategy/
│   ├── base_strategy.py     # ABC (Signal dataclass, 복수매매)
│   ├── orb_strategy.py      # Opening Range Breakout
│   ├── vwap_strategy.py     # VWAP 회귀
│   ├── momentum_strategy.py # 전일 고점 돌파
│   └── pullback_strategy.py # 눌림목 매매
├── screener/
│   ├── candidate_collector.py  # 유니버스→candidate dict 생성
│   ├── pre_market.py           # 4단계 필터 (기본/기술/수급/이벤트)
│   ├── strategy_selector.py    # 시장 상황→전략 자동 선택
│   └── realtime_scanner.py     # 장중 실시간 모니터링
├── risk/
│   └── risk_manager.py      # 손절/일일한도/연속손실/장애복구
├── notification/
│   └── telegram_bot.py      # 비동기 텔레그램 알림
├── backtest/
│   ├── backtester.py        # pure pandas 백테스트 엔진
│   ├── data_collector.py    # 과거 분봉 수집 배치
│   ├── batch_collector.py   # 대량 분봉 수집
│   ├── optimizer.py         # Optuna 최적화
│   ├── optimize_all.py      # 전략별 최적화 배치
│   └── run_all_strategies.py
├── gui/                     # PyQt6 GUI (QThread 래퍼)
├── utils/
│   └── market_calendar.py   # 장 시간/공휴일 판정
├── tests/                   # pytest (125 tests)
└── docs/
    ├── daytrading_prd.md    # PRD v1.0
    ├── dev_setup.md         # 개발 환경 세팅
    └── SURGERY_GUIDE.md     # 수술 가이드 (이 파일과 함께 생성)
```

---

## 파이프라인 아키텍처

```
[WS 수신]  →  tick_queue(10000)
                    ↓
[캔들 빌더] →  candle_queue(1000)   ← 1분/5분봉 + VWAP
                    ↓
[전략 엔진] →  signal_queue(100)    ← generate_signal()
                    ↓
[주문 실행] →  order_queue(100)     ← execute_buy/sell
                    ↓
[체결 확인]                          ← WS 체결통보 / REST 재조회
```

스케줄: 08:30 스크리닝 → 09:05 신호 활성 → 15:10 강제 청산 → 15:30 리포트

---

## 확인된 버그 목록 (우선순위순)

### CRITICAL — 실매매 전 반드시 수정

**~~FIX-1: 실시간 포지션 모니터링 부재 (손절/익절 미작동)~~ (수정 완료)**
- `risk_manager`에 `check_stop_loss()`, `check_tp1()`, `update_trailing_stop()` 메서드 존재
- **그러나 파이프라인 어디에서도 호출하지 않음**
- 틱 수신 시 포지션 보유 종목의 현재가를 체크하는 로직 자체가 없음
- 결과: 손절/TP1/트레일링 스톱이 실시간으로 작동하지 않음
- 위치: `main.py` tick_consumer / candle_consumer, `gui/workers/engine_worker.py`

**~~FIX-2: config/parameter 불일치 (스윙 시스템과 동일 패턴)~~ (수정 완료)**
- `config.yaml` (최적화 결과): tp1_pct=0.03, max_trades=3, cooldown=15분
- `settings.py` 기본값: tp1_pct=0.02, max_trades=5, cooldown=10분
- **치명적**: `config.yaml`의 `orb.volume_ratio=0.0` (비활성) vs `settings.py` 기본값 `1.0` (활성)
- `from_yaml()`이 `orb_volume_ratio` 로드 시 기본값이 `1.0`으로 설정됨 (160행)
- 백테스트에서 TradingConfig 직접 생성 시 최적화 전 값으로 실행됨

**~~FIX-3: initial_capital이 risk_manager에 미연결~~ (수정 완료)**
- `config.yaml`: `initial_capital: 1_000_000`
- `risk_manager._daily_capital` 초기값: `0.0`
- `signal_consumer`에서 `capital <= 0`이면 하드코딩 `10_000_000` 사용
- 결과: config의 자본금 설정이 무시되고, 포지션 사이징이 10배 과대

**~~FIX-4: OrderManager (실매매) DB 기록 누락~~ (수정 완료)**
- `PaperOrderManager`: trades 테이블에 기록 O
- `OrderManager`: trades 테이블에 기록 **X**
- 실매매 모드에서 일일 보고서, 연속 손실 체크 등이 모두 불가

### HIGH — 백테스트 신뢰성 + 데이터 정합성

**~~FIX-5: 백테스트 비용 모델 불일치~~ (수정 완료)**
- `backtester.py` 하드코딩: `SLIPPAGE_RATE = 0.00005` (0.005%)
- `config.yaml` backtest 섹션: `slippage: 0.0003` (0.03%) — **6배 차이**
- Backtester 생성자에서 config.yaml 값을 자동으로 읽지 않음
- `commission` 파라미터가 매수/매도 동일 적용 (config.yaml은 각각 별도)

**~~FIX-6: PaperOrderManager strategy 하드코딩~~ (수정 완료)**
- 72행: `strategy='paper'` 고정
- 실제 전략명(orb/vwap/momentum/pullback)이 아닌 'paper'로 기록
- daily_pnl 전략별 분석, risk_manager의 성과 집계가 무의미해짐

**~~FIX-7: 백테스터 분할매매 미반영~~ (수정 완료)**
- 실전: 분할 매수(55%+45%), 분할 매도(TP1 50% + trailing)
- 백테스트: all-in / all-out 단일 포지션
- TP1 후 손절선 본전 이동 로직도 미시뮬레이션

**~~FIX-8: StrategySelector 임계값 불일치~~ (수정 완료)**
- `config.yaml`: orb_gap=0.8%, momentum_etf=2.0%, vwap_range=0.8%
- `strategy_selector.py` DEFAULT: 0.5%, 1.5%, 0.5%
- PRD: 0.5%, 1.5%, 0.5%
- config.yaml의 값이 로드되긴 하지만, 기본값과 PRD 사이의 불일치 정리 필요

### MEDIUM — 운영 안정성

**~~FIX-9: CandleBuilder ts 포맷 (시간만, 날짜 없음)~~ (수정 완료)**
- `candle_builder.py` 38행: `"ts": f"{time_str[:2]}:{time_str[2:4]}:00"`
- 날짜 정보 없이 "09:05:00" 형태
- DB 스키마는 ISO8601 기대, 멀티데이 분석 시 날짜 구분 불가

**~~FIX-10: market_calendar 2026년 공휴일만 하드코딩~~ (수정 완료)**
- 2027년부터 모든 공휴일을 거래일로 간주
- 외부 라이브러리(holidays) 또는 연도별 관리 필요

**~~FIX-11: force_close에서 private dict 직접 접근~~ (수정 완료)**
- `main.py` 287행: `risk_manager._positions.items()`
- public 인터페이스 (예: `get_open_positions()`) 로 교체 필요

**~~FIX-12: DB 백업 메커니즘 부재~~ (수정 완료)**
- quant-system은 리밸런싱 전 자동 백업
- day-trader는 백업 로직 없음

**~~FIX-13: 수급 데이터 미구현 (항상 0)~~ (경고 로그 추가)**
- `candidate_collector.py`: `institutional_buy: 0`, `foreign_buy: 0` 고정
- 스크리닝 수급 필터가 사실상 무의미

---

## 수술 작업 시 주의사항

1. **테스트 먼저 실행**: `pytest tests/ -v` → 현재 상태 확인 후 작업
2. **config 계층 존중**: config.yaml → settings.py → 코드 하드코딩 순으로 우선순위
3. **asyncio 주의**: Windows SelectorEventLoop 제약, Lock/Queue 사용 패턴 유지
4. **PaperOrderManager ↔ OrderManager 인터페이스 동기화**: 한쪽 수정 시 반대쪽도 반드시 확인
5. **백테스트 수정 후 기존 결과와 비교**: 비용 모델 변경 시 동일 데이터로 before/after 검증

---

## 커밋 컨벤션

```
fix: [FIX-N] 요약 (한글 OK)
feat: 기능 추가
refactor: 구조 변경 (동작 변경 없음)
test: 테스트 추가/수정
docs: 문서 업데이트
```

---

## 테스트

```bash
# 전체 테스트
pytest tests/ -v

# 특정 모듈
pytest tests/test_risk_manager.py -v

# 커버리지
pytest tests/ --cov=. --cov-report=term-missing
```

---

## 관련 시스템

- **quant-system** (`rbgus87/quant-system`): 멀티팩터 퀀트 (분기 리밸런싱)
- **swing-trader** (`rbgus87/swing-trader`): 스윙 매매 자동화
- 세 시스템은 독립 DB, 공통 모듈(retry.py, auth.py) 공유 구조
