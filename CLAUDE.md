# CLAUDE.md — day-trader

> **최종 수정**: 2026-04-17 (페이퍼 시작 준비 완결)
> 이 문서는 **백테스트에서 검증된 사실만** 기재한다.

---

## 프로젝트 개요

day-trader는 **KOSPI/KOSDAQ 모멘텀 단타 시스템**이다.
당일 전일 고가 돌파 + ADX 추세 + 시장 필터를 충족하는 종목을 09:05 ~ 12:00 사이에 진입하고, 당일 15:10에 강제 청산한다.

**페이퍼 시작**: 2026-04-17 (금), 자본 300만원.

---

## 전략

- **MomentumStrategy 단일 운영.**
- Flow / Pullback / Gap / OpenBreak / BigCandle 등은 `strategy/archive/`에 격리되어 있으며 운영 경로에 포함되지 않는다.

### 시스템 엣지 TOP 3

1. **거래량 비율 2.0** — PF 영향 1.89 (비활성 시 PF 1.38)
2. **오전 매수 제한 12:00** — PF 영향 2.03 (비활성 시 PF 1.24)
3. **시장 필터 MA5** — PF 영향 1.08 (비활성 시 PF 2.19)

### 진입 조건

- 전일 고가 돌파
- ADX(14) ≥ 20
- 시장 필터 통과 (KOSPI 종목은 KOSPI MA5, KOSDAQ 종목은 KOSDAQ MA5 이상)
- 거래 시각 09:05 ≤ now ≤ 12:00
- 동시 보유 포지션 `max_positions = 3` 이하

### 청산 경로 (3종, ADR-010)

| reason | 트리거 |
|------|------|
| `stop_loss` | 고정 -8% 손절 (`stop_loss_pct: -0.080`) |
| `trailing_stop` | 진입 즉시 Chandelier (최고가 − ATR × 1.0), 하한 2% / 상한 10% |
| `forced_close` | 15:10 미청산 포지션 일괄 청산 |

> TP1 분할매도(`tp1_hit`)는 ADR-010에서 폐기. `atr_tp_enabled: false`.
> ATR 기반 손절(`atr_stop_enabled`)도 ADR-010에서 폐기 (고정 -8%로 단순화).

---

## 유니버스

- **60종목** (KOSDAQ 40 + KOSPI 20, ATR ≥ 6% 필터)
- `config/universe.yaml`에 기록
- 생성: `scripts/generate_universe.py --min-atr 0.06` (KRX Open API)
- 필터: 시총 상위 + 거래대금 ≥ 50억 + ATR(14) ≥ 6% + max_total 60
- **주간 자동 갱신** (월 07:30, ADR-012)

---

## 사이징

- **1주 단위 비율 시뮬레이션** (백테스트 기준).
- 자본금·리스크·분할매수 등 정교한 사이징은 후속 검토 대상.
- 페이퍼 자본 300만원, 포지션당 100만원 (max_positions=3).

---

## 백테스트 결과 (baseline, 2026-04-16 ADR-010 청산 튜닝 완결)

- **Profit Factor 3.28** (1주 가중, 41종목, Pure trailing + 고정 -8% 손절)
- 연 거래 건수 279건
- 총 PnL +285,588 (1주 단위, 거래세 0.15% 반영)
- PF > 1 종목 수: 29 / 41
- 청산 분포: forced_close 208 (74.6%) / stop_loss 65 (23.3%) / trailing_stop 6 (2.2%)
- **Walk-Forward 검증** (ADR-011): 학습 PF 5.11 → 검증 PF 4.05 (-21%, 통과)

---

## 페이퍼 자본 시뮬 (ADR-013)

- max_positions 3, 자본 300만원
- PF 3.24, 수익률 86.5%, Max DD 14.6%

---

## 자동화 정책

| 잡 | 주기 | 시각 | ADR |
|----|------|------|-----|
| 토큰 갱신 | 매일 | 08:00 | — |
| 전일 OHLCV | 매일 | 08:05 | ADR-006 |
| 스크리닝 | 매일 | 08:30 | — |
| 매수 차단 해제 | 매일 | 09:05 | — |
| 매수 차단 | 매일 | 12:00 | — |
| 강제 청산 | 매일 | 15:10 | — |
| 일일 보고서 | 매일 | 15:30 | — |
| 분봉 수집 | 평일 | 15:35 | ADR-014 |
| 일일 리셋 | 매일 | 00:01 | ADR-006 |
| 유니버스 갱신 | 월요일 | 07:30 | ADR-012 |

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
│   ├── backtester.py        # baseline — 1주 단위 비율 시뮬
│   ├── batch_collector.py   # 분봉 배치 수집
│   └── data_collector.py    # 분봉 수집기
├── screener/                # 후보 수집 + 08:30 스크리닝
├── core/                    # 주문 실행
├── risk/                    # 리스크 관리
├── data/                    # 캔들 빌더 + DB
├── notification/            # 텔레그램
├── tests/                   # pytest
└── docs/
    ├── adr/                 # ADR-001 ~ ADR-014
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

## 재조립 진행 상태 — 전 Phase 완결

- [x] 사이징 (ADR-002) — 1주 단위, 백테스트와 동일
- [x] 주문 실행 — `PaperOrderManager` / `OrderManager` 정상
- [x] 리스크 관리 (Phase 2-B) — `daily_max_loss`, `blacklist`, `consecutive_loss_rest` 일치
- [x] 자본 관리 — `available_capital` 유지
- [x] 일일 리셋 + 전일 OHLCV 자동화 (ADR-006)
- [x] DB 기록 스펙 (ADR-007) — `positions` 활성화, `tp2_price`/`system_log` 제거
- [x] 알림 정책 (ADR-008) — 12종 토글, 포맷 통일
- [x] 비용 모델 통일 (ADR-009) — tax 0.15%, PaperOrderManager PnL 비용 반영
- [x] Pure trailing + ATR 6% (ADR-010)
- [x] Walk-Forward 검증 (ADR-011)
- [x] 유니버스 주간 자동 갱신 (ADR-012)
- [x] max_positions + 페이퍼 자본 확정 (ADR-013)
- [x] 분봉 자동 수집 (ADR-014)

검증 명령어: `docs/verification_commands.md`
후속 작업: [`docs/phase_followup_todo.md`](docs/phase_followup_todo.md)

## 일일 운영

운영 매뉴얼: [`docs/runbook.md`](docs/runbook.md)

- **정상**: 매일 08:00 `python gui.py` 시작, 15:30 이후 종료
- **안전망** (ADR-006): 00:01 자동 리셋, 08:05 OHLCV 갱신, 24h 가동 안내
- **자동화**: 월 07:30 유니버스 갱신, 평일 15:35 분봉 수집
- **검증**: `python selftest.py` → 7/7 OK + `docs/verification_commands.md`
