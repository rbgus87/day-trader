# CLAUDE.md — day-trader

> **최종 수정**: 2026-04-15 (재조립 착수 시점)
> 이 문서는 **백테스트에서 검증된 사실만** 기재한다.
> 라이브 전용 부속(사이징/자본관리/리스크/알림 등)은 Phase 2에서 순차 추가.

---

## 프로젝트 개요

day-trader는 **KOSPI/KOSDAQ 모멘텀 단타 시스템**이다.
당일 전일 고가 돌파 + ADX 추세 + 시장 필터를 충족하는 종목을 09:05 ~ 12:00 사이에 진입하고, 당일 15:10에 강제 청산한다.

---

## 전략

- **MomentumStrategy 단일 운영.**
- Flow / Pullback / Gap / OpenBreak / BigCandle 등은 `strategy/archive/`에 격리되어 있으며 운영 경로에 포함되지 않는다.

### 진입 조건

- 전일 고가 돌파
- ADX(14) ≥ 20
- 시장 필터 통과 (KOSPI 종목은 KOSPI MA5, KOSDAQ 종목은 KOSDAQ MA5 이상)
- 거래 시각 09:05 ≤ now ≤ 12:00
- 동시 보유 포지션 `max_positions = 3` 이하

### 청산 경로 (4종)

| reason | 트리거 |
|------|------|
| `stop_loss` | ATR(14) × 1.5 기반 손절선, 하한 1.5% / 상한 8.0% |
| `tp1_hit` | ATR(14) × 3.0 기반 1차 익절, 하한 3% / 상한 25% |
| `trailing_stop` | TP1 이후 Chandelier (최고가 − ATR × 2.5), 하한 2% / 상한 10% |
| `forced_close` | 15:10 미청산 포지션 일괄 청산 |

---

## 유니버스

- **60종목** (KOSPI 24 + KOSDAQ 36)
- `config/universe.yaml`에 기록
- 생성: `scripts/generate_universe.py` (KRX Open API)
- 필터: 시총 상위 + 거래대금 ≥ 50억 + ATR(14) ≥ 2% + max_total 60
- **분기 1회 재생성 권장**

---

## 사이징

- **1주 단위 비율 시뮬레이션** (백테스트 기준).
- 자본금·리스크·분할매수 등 정교한 사이징은 **Phase 2 재조립 대상**. 현재 라이브 코드의 리스크 2% / 자본 분배 로직은 baseline과 일치하지 않으므로 재구성 전에 의존하지 않는다.
- 판단 근거: `backtest/backtester.py`에 `position_size` / `shares` / `capital` / `buy_amount` / `entry_capital` / `trade_size` 키워드가 존재하지 않는다 (검증 명령어 참조).

---

## 백테스트 결과 (baseline)

- **Profit Factor 2.91**
- 연 거래 건수 약 185건
- PF > 1 종목 수: 31 / 60
- 기준 커밋: `85242f5` — "α 후보 확정 — 60종목 + buy_time 12:00 + 시장필터 (PnL +288k, PF 2.91)"

---

## 디렉토리 구조

```
day-trader/
├── gui.py                   # 엔트리포인트 (GUI / `--selftest` / `--version`)
├── config.yaml              # 매매 파라미터
├── config/
│   ├── settings.py          # AppConfig dataclass
│   └── universe.yaml        # 유니버스 60종목
├── strategy/
│   ├── base_strategy.py
│   ├── momentum_strategy.py # 운영 전략
│   └── archive/             # 격리된 과거 전략 5개
├── backtest/
│   └── backtester.py        # baseline — 1주 단위 비율 시뮬
├── screener/                # 후보 수집 + 08:30 스크리닝
├── core/                    # 주문 실행 (재조립 대상)
├── risk/                    # 리스크 관리 (재조립 대상)
├── data/                    # 캔들 빌더 + DB
├── notification/            # 텔레그램 (재조립 대상)
├── tests/                   # pytest
└── docs/
    ├── adr/                 # Architecture Decision Records
    ├── legacy/              # 재조립 전 문서 보존
    └── verification_commands.md
```

---

## 커밋 컨벤션

```
fix: 요약
feat: 기능 추가
refactor: 구조 변경 (동작 변경 없음)
test: 테스트 추가/수정
docs: 문서 업데이트
```

---

## 테스트

```bash
pytest tests/ -v                      # 전체
pytest tests/test_risk_manager.py -v  # 특정 모듈
pytest tests/ --cov=. --cov-report=term-missing
```

---

## 관련 시스템

- **quant-system**: 멀티팩터 퀀트 (분기 리밸런싱)
- **swing-trader**: 스윙 매매 자동화
- 세 시스템은 독립 DB. 공통 모듈(`retry.py`, `auth.py`)만 공유.

---

## 재조립 진행 상태 (2026-04-15 ~)

백테스트 환경을 baseline으로 라이브 운영 부속을 재조립 중.

- [x] 사이징 (ADR-002) — 1주 단위, 백테스트와 동일
- [x] 주문 실행 — `PaperOrderManager` / `OrderManager` 정상
- [x] 리스크 관리 (Phase 2-B) — `daily_max_loss`, `blacklist`, `consecutive_loss_rest` 일치
- [x] 자본 관리 — `available_capital` 유지 (사이징 재검토는 후속 ADR)
- [x] 일일 리셋 + 전일 OHLCV 자동화 (ADR-006) — 자정 `_daily_reset`, 08:05 OHLCV 갱신, 24h 안내
- [ ] 알림 (Telegram) — Phase 3 검토 대상 (중복/누락 점검)
- [ ] DB 기록 스펙 — Phase 3 검토 대상 (`tp2_price` 컬럼 등 잔재)
- [ ] 라이브 수수료·슬리피지 — 후속 ADR

각 영역 추가 시 이 문서와 `docs/adr/` 동시 갱신.
검증 명령어는 `docs/verification_commands.md` 참조.

## 일일 운영

운영 매뉴얼: [`docs/runbook.md`](docs/runbook.md)

- **정상**: 매일 08:00 `python gui.py` 시작, 15:30 이후 종료
- **안전망** (ADR-006): 00:01 자동 리셋, 08:05 OHLCV 갱신, 24h 가동 안내
- **검증**: `python selftest.py` → 7/7 OK + `docs/verification_commands.md`
