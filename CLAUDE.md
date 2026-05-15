# CLAUDE.md — day-trader

> **최종 수정**: 2026-05-14 (JSONL 구조화 로깅 + 장중 시장 필터 구현)
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
3. **시장 필터 MA5** — PF 영향 1.52 (비활성 시 PF 2.459, 2026-05-13 3-scenario 재측정 / 기존 구간 ~04-10 기준)

### 현재 활성 파라미터 전체 (2026-05-14)

| 파라미터 | 값 | 비고 |
|---------|---|------|
| `momentum_volume_ratio` | 2.0 | 전일 전체 거래량 대비 |
| `min_breakout_pct` | 0.03 | 돌파폭 ≥ 3% |
| `max_entry_above_breakout_pct` | 0.10 | 돌파 후 최대 추격 10% |
| `buy_time_end` | 12:00 | 오전 매수 제한 |
| `max_positions` | 3 | 동시 보유 상한 |
| `atr_stop_enabled` | true | ATR×2.0 비례 손절 (clamp 4~15%) |
| `atr_trail_min_pct` | 0.025 | Chandelier trail 하한 2.5% |
| `atr_trail_max_pct` | 0.080 | Chandelier trail 상한 8% |
| `momentum_fade_threshold` | -0.008 | fade 진입 ROC 임계값 |
| `momentum_fade_min_profit` | 0.03 | fade 발동 최소 수익 3% |
| `breakeven_stop_trigger` | 0.03 | BE 발동 수익 3% |
| `intraday_block_threshold` | -0.01 | 장중 필터 차단 -1% |
| `intraday_resume_threshold` | -0.005 | 장중 필터 해제 -0.5% |
| `intraday_check_interval_min` | 10 | 장중 필터 갱신 주기 (분) |
| `stale_position_exit_enabled` | false | 비활성 확정 |
| `afternoon_entry_enabled` | false | 비활성 확정 |
| `volatility_sizing_enabled` | false | 비활성 확정 |
| `obi_filter_enabled` | false | 0D 필드 코드 확인 전 |

### 진입 조건

- 전일 고가 돌파 (**ADR-016**: 돌파폭 ≥ 3%, `min_breakout_pct: 0.03`)
- 당일 누적 거래량 ≥ 전일 거래량 × 2.0
- ADX(14) ≥ 20
- 시장 필터 통과 (KOSPI 종목은 KOSPI MA5, KOSDAQ 종목은 KOSDAQ MA5 이상)
- 거래 시각 09:05 ≤ now ≤ 12:00
- 동시 보유 포지션 `max_positions = 3` 이하

### 청산 경로 (6종, ADR-010/017/018 + 2026-05-12)

| reason | 트리거 |
|------|------|
| `limit_up_exit` | **ADR-018**: 상한가 (키움 ka10001 `upl_pric`, 실패 시 전일종가 × 1.30 호가 절사 fallback) 도달 시 즉시 시장가 매도. 체결 실패 시 stop을 상한가 × 0.99로 상향 |
| `stop_loss` | ATR×2.0 비례 손절 (clamp 4%~15%). ATR 미가용 시 fallback -8% (`stop_loss_pct: -0.080`) |
| `trailing_stop` | 진입 즉시 Chandelier (최고가 − ATR × 1.0), 하한 2.5% / 상한 8%. **time_decay 적용**: 12:00~ ATR×0.7, 13:30~ 0.5, 14:30~ 0.3 (hard floor 1.0%) |
| `breakeven_stop` | **ADR-017**: peak_return ≥ 3% 도달 시 stop을 entry+1%로 상향, 이후 되돌림 |
| `momentum_fade` | **2026-05-12**: 수익 ≥ +3% + 보유 ≥ 15분 + ROC(10분) ≤ −0.8% 시 즉시 청산. 손실 포지션 미적용 |
| `forced_close` | 15:10 미청산 포지션 일괄 청산 |

> TP1 분할매도(`tp1_hit`)는 ADR-010에서 폐기. `atr_tp_enabled: false`.
> ATR 기반 손절 2026-05-13 그리드에서 재활성화 (`atr_stop_enabled: true`, mult=2.0). ADR-010 폐기 당시는 ATR≥6% 유니버스라 전부 클램핑이었으나 현재 ATR≥4%로 확대되어 재검토.

---

## 유니버스

운영/백테스트 유니버스를 분리해 백테스트 baseline이 자동 갱신에 깨지지 않도록 한다.

| 파일 | 용도 | 갱신 |
|------|------|------|
| `config/universe.yaml` | 운영 (engine_worker, GUI, core/*) | 조건검색 + 월 07:30 자동 갱신 (ADR-012) |
| `config/universe_backtest.yaml` | 백테스트/그리드/시뮬 (scripts/*) | **고정** — 절대 자동 갱신 안 됨 |

### 백테스트 baseline (universe_backtest.yaml)

- **41종목** (KOSDAQ 32 + KOSPI 9, ATR ≥ 6% 필터)
- 생성: `scripts/generate_universe.py --min-atr 0.06 --max-stocks 40` (KRX Open API)
- 필터: 시총 상위 + 거래대금 ≥ 50억 + ATR(14) ≥ 6% + 코스닥 우선 보충
- 60종목 테스트 결과 PF 2.73 (41종목 3.41 대비 −20%), ATR 상위 40종목은 PF 1.92

---

## 사이징

- **1주 단위 비율 시뮬레이션** (백테스트 기준).
- 자본금·리스크·분할매수 등 정교한 사이징은 후속 검토 대상.
- 페이퍼 자본 300만원, 포지션당 100만원 (max_positions=3).

---

## 백테스트 결과 (baseline, 2026-05-14)

- **Profit Factor 4.798** (1주 가중, 41종목, 2025-04-01 ~ 2026-04-10, **장중 필터 포함** — ATR×2.0 손절 + trail_min 2.5%/max 8% + time_decay + momentum_fade + 돌파폭 ≥ 3% + BE3 + 상한가 청산 + max_entry_above_breakout_pct 10% + intraday_market_filter)
- 연 거래 건수 221건 / 총 PnL +287,892 / 거래당 PnL +1,302
- 청산 분포: forced_close 92 / breakeven_stop 49 / momentum_fade 42 / stop_loss 26 / trailing_stop 7 / limit_up_exit 5
- **장중 필터 제외 PF 4.881** (228건, PnL +295,690) — 필터 적용으로 7건 차단, PF −1.7%
- **확장 기간 측정** (2026-04-11 ~ 05-12): baseline PF 0.022 / 14건 / PnL −52,619 → 장중 필터 PF 0.031 / 12건 / **PnL −38,296** (2건 차단, 27% 개선). KOSPI +31.58% 상승장 환경.
- **거래량 필터 그리드 검증** (2026-05-13): volume_by_time / breakout_surge 모두 baseline PF 3.5 미달 → 비활성 확정. 전일 전체×2.0 거래량 필터가 핵심 엣지.
- **max_entry_above_breakout_pct 그리드** (2026-05-13): [3%→PF 2.544 / 5%→3.162 / 7%→4.032 / **10%→PF 4.817 / PnL 293K**]. 10%만 기준(PF≥3.5, PnL≥250K) 통과. `max_entry_above_breakout_pct: 0.05 → 0.10` 갱신.
- **stale_exit 그리드** (2026-05-13): 16조합 전체 PF < baseline×0.95 → 비활성 확정. 최고 PF 3.142(60min/0.01) — PnL +177K로 baseline +294K 대비 −40%. `stale_position_exit_enabled: false` 유지.
- **afternoon_entry 그리드** (2026-05-13): 8조합 전체 PF < baseline×0.95 → 비활성 확정. 최고 조합(end=14:00/bp=7%/vr=3.0): PF 4.544 / PnL +306K / aft# 46 / aft_PF 2.314 — PF 기준 0.03 미달. `afternoon_entry_enabled: false` 유지.
- **변동성 기반 포지션 사이징 그리드** (2026-05-13): 12조합 전체 MDD 기준 미달. `volatility_sizing_enabled: false` 유지. `reports/volatility_sizing_grid.md`.
- **ATR 비례 손절 + trail 그리드** (2026-05-13): Stage1 mult=2.0 → PF 4.791 / Stage2 trail_min=0.025/max=0.08 → PF 4.881 / Stage3 breakeven 현행 유지. `atr_stop_enabled: true`, `atr_trail_min_pct: 0.025`, `atr_trail_max_pct: 0.08` 확정. SL# 32→27. `reports/atr_stop_grid.md`.
- **장중 시장 필터 검증** (2026-05-14): 일봉 close/open 근사 기준 기존 구간 PF 4.881 → **4.798** (7건 차단, −1.7%). 확장 구간(2026-04-11~05-12) PnL **-52,619 → -38,296** (2건 차단, 27% 개선). `intraday_market_filter_enabled: true`, `block_threshold: -0.01`, `resume_threshold: -0.005`, `check_interval: 10min`.
- **갭업 기준가 조정 + 시그널 스코어링 그리드** (2026-05-14): Stage1 갭업 5조합 — GAP-5%만 PF 4.653 통과하나 PnL +271K로 baseline +295K 대비 −24K. Stage2 스코어링 6조합 — SC-60 PF 5.338 최고이나 PnL +268K로 −28K 감소, NEW 구간(-57K)은 baseline(-52K) 대비 악화. **두 기능 모두 비활성 확정.** `gap_breakout_adjust_enabled: false`, `signal_scoring_enabled: false`. `reports/gap_score_grid.md`.
- **갭 전략 27조합 그리드** (2026-05-16): gap_min×pullback_min×force_close 3×3×3. 갭 단독 PF 전 조합 < 1.0 (최고 0.771), 선정 기준(PF≥1.5) 미달. 합산 PF 최고 1.856으로 baseline 4.881 대비 −62%. **전 조합 비활성 확정.** `gap_pullback_enabled: false` 유지. `reports/gap_pullback_grid.md`.
- **이전 baseline** (장중 필터 미포함)
  - 장중 필터 제외 (2026-05-14): PF 4.881 / 228건 / +295,690 / SL# 27
  - 고정 -8% 손절 + trail_min=0.02/max=0.10 (2026-05-13): PF 4.817 / 229건 / +293,532 / SL# 32
  - momentum_fade(thr=-0.008, mp=0.03) + max_entry=5% (2026-05-13): PF 3.73 / 247건 / +278,979 / fc% 38.1%
  - time_decay + momentum_fade(thr=-0.005, mp=0.01) (2026-05-12): PF 3.80 / 250건 / +225,523 / forced_close 27.6% / fade 104건
  - 거래세 0.20% / VI + Order Confirmation (2026-05-12 직전): PF 4.36 / 248건 / forced_close 134 (54%) / trailing_stop 4 (1.6%)
  - 거래세 0.15% 시: PF 4.56 / 248건 / +297,059 (세율 과소 반영 — ADR-009 폐기 수치)
  - ADR-017 (BE3): PF 4.28 / 254건 / 강세 5.88 / 횡보 2.45 / 약세 3.11
  - ADR-016 (돌파폭 3%): PF 3.88 / 240건 / 약세 2.16
  - ADR-010 (base): PF 3.28 ~ 3.41 / 거래 273~279건
- **Walk-Forward 검증** (ADR-011, ADR-017 이전): 학습 PF 5.11 → 검증 PF 4.05 (-21%, 통과)

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
│   ├── universe.yaml        # 운영 유니버스 (자동 갱신 대상)
│   └── universe_backtest.yaml  # 백테스트 유니버스 41종목 (고정)
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
    ├── adr/                 # ADR-001 ~ ADR-018
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
- [x] 비용 모델 통일 (ADR-009) — tax 0.20% (KOSPI/KOSDAQ 공통, 2025 기준), PaperOrderManager PnL 비용 반영
- [x] Pure trailing + ATR 6% (ADR-010)
- [x] Walk-Forward 검증 (ADR-011)
- [x] 유니버스 주간 자동 갱신 (ADR-012)
- [x] max_positions + 페이퍼 자본 확정 (ADR-013)
- [x] 분봉 자동 수집 (ADR-014)
- [x] 돌파 폭 하한 3% (ADR-016) — PF 3.41 → 3.88, 약세 PF 1.55 → 2.16
- [x] Breakeven Stop BE3 (ADR-017) — PF 3.88 → 4.28, 약세 PF 2.16 → 3.11
- [x] 상한가 즉시 청산 (ADR-018) — PF 4.28 → 4.56 (+6.5%), limit_up_exit 15건 (6.0%, 거래당 +8.64%)
  - 2026-05-12 거래세 0.15% → 0.20% 정정 후 동일 시스템 baseline PF 4.36 (−4.4%)
- [x] VI 휴리스틱 대응 (2026-05-12) — 시장가 → 최유리지정가 자동 전환, VI 활성 종목 매수 차단. limit_up_exit / forced_close 보호. 백테스트 baseline PF 4.36 변동 없음.
- [x] Order Confirmation Pipeline (2026-05-12) — real_mode WS '00' 체결통보까지 settle_sell 보류. OrderTracker 재진입 가드. paper_mode/backtester 영향 없음. 백테스트 baseline PF 4.36 변동 없음.
- [x] Time-Decayed Trailing + Momentum Fade Exit (2026-05-12) — forced_close 비율 27.6% (이전 54%), 신규 청산 경로 momentum_fade 104건 (41.6%). PF 3.80 (이전 4.36 대비 −12.8%, PnL +225,523 — 자리 점유 해소 vs 수익 감소 트레이드오프).
- [x] Momentum Fade 파라미터 갱신 (2026-05-13) — threshold −0.005→−0.008, min_profit 0.01→0.03. PF 3.73 / forced_close 38.1% / PnL +278,979 / fade 45건(18.2%). 이전 대비 PnL +53K(+23%) 개선, fade 건수 −59건 감소.
- [x] 거래량 필터 그리드 + 스크리너 강화 (2026-05-13) — volume_by_time·breakout_surge 비활성 확정. 스크리너: prev_close≥prev_high×97% / 전일 상한가 제외 / 거래대금≥30억 / min_market_cap 1000억 / min_atr_pct 4%.
- [x] 시장 필터 전략 3-Scenario 검증 (2026-05-13) — A)완전차단 PF 3.727 / B)비활성 PF 2.459 / C)50%축소 PF 2.937 (기존 구간 ~04-10 기준). A) 완전 차단 유지 확정. 약세 시장 거래(C: 88건) PF=0.494 — 사이즈 축소도 손실. `reports/market_filter_strategy.md` 참조.
- [x] 틱 레벨 돌파 감지 + max_entry_above_breakout_pct 그리드 (2026-05-13) — BreakoutInfo 데이터클래스, _tick_consumer 즉시 진입 경로, 백테스트 breakout_price 반영. 그리드 10%→PF 4.817/PnL 293K 통과 → `max_entry_above_breakout_pct: 0.10` 확정.
- [x] stale_exit + afternoon_entry 구현 (2026-05-13) — risk_manager.check_stale_position(), _check_buy_time_limit() 오후 창 지원. backtester/engine_worker 통합. config default: 비활성 (검증 후 활성화 예정).
- [x] max_positions × capital 그리드 (2026-05-13) — equal-capital PF: max_pos=3 PF 1.989(최고) / max_pos=2 PF 1.818 / MDD 19%→13%→10%→8%. 현재 max_pos=3 최적 확정. `reports/positions_capital_grid.md`.
- [x] stale_exit + afternoon_entry 파라미터 그리드 (2026-05-13) — stale_exit 16조합 전체 PF < baseline×0.95 비활성 확정. afternoon_entry 8조합 전체 비활성 확정 (최고 조합 PF 4.544, 기준 4.576 미달). baseline 갱신: PF 4.817 / 229건 / PnL +293,532. `reports/stale_exit_grid.md`, `reports/afternoon_entry_grid.md`.
- [x] 변동성 기반 포지션 사이징 구현 + 그리드 (2026-05-13) — settings.py/config.yaml 5개 필드 추가, backtester.py ATR 계산+PnL 재산정, engine_worker.py 사이징 블록. 그리드 12조합 전체 MDD 기준(< baseline 0.41%) 미달 → `volatility_sizing_enabled: false` 유지. `reports/volatility_sizing_grid.md`.
- [x] ATR 비례 손절 + trail 범위 3단계 그리드 (2026-05-13) — Stage1 atr_stop(36조합): mult=2.0 → PF 4.791/SL#30. Stage2 trail범위(9조합): min=0.025/max=0.08 → PF 4.881/SL#27. Stage3 breakeven(9조합): 현행(3%/1%) 유지. baseline PF 4.817 → **4.881** (+1.3%), SL# 32→27(-15.6%). `reports/atr_stop_grid.md`.
- [x] 호가(0D) 구독 + OBI 필터 구현 (2026-05-13) — `core/orderbook.py` (OrderbookSnapshot/OrderbookManager), kiwoom_ws.py 0D 파싱, engine_worker.py OBI/스프레드/매도벽 3단계 진입 게이트. 실시간 전용 — 백테스트 baseline PF 4.881 변동 없음. 0D 필드 코드 미확정(TODO 상수), `obi_filter_enabled: false` (0D 수신 확인 전). `docs/obi_activation_plan.md` 참조.
- [x] 페이퍼 운용 전 통합 점검 (2026-05-13) — `scripts/pre_paper_check.py` (파라미터/DB/universe/REST/WS 6개 항목). `scripts/test_nxt_api.py` (NXT API 실측, 장외 시간 수동 실행). `docs/nxt_api_investigation.md` (NXT 조사, 코드 미구현).
- [x] JSONL 구조화 로깅 (2026-05-14) — `utils/logging_config.py` (_JsonlSink daily rotation·30일 보관·thread-safe), `scripts/analyze_paper_log.py` (장후 분석). engine_worker.py logger.bind(event=...) 주요 경로 17종. `tests/test_logging_config.py` 10개.
- [x] 장중 시장 필터 구현 (2026-05-14) — `core/market_filter.py` refresh_intraday() / is_intraday_blocked(), engine_worker.py APScheduler 10분 갱신 + signal_consumer 체크, backtester.py build_intraday_blocked_by_date() 일봉 근사. 기존 구간 PF 4.798 / 확장 구간 손실 27% 개선. `tests/test_intraday_market_filter.py` 13개.
- [x] 시그널 스코어링 + 갭업 기준가 조정 구현 + 그리드 (2026-05-14) — `core/signal_scorer.py` 5요소 100점 스코어러, Signal.context 전달, engine_worker/backtester 통합. `tests/test_signal_scorer.py` 17개. 그리드 결과: 갭업 조정 비활성 확정(GAP-5% PF 4.653 / PnL −24K 열세), 스코어링 비활성 확정(SC-60 PF 5.338 / PnL −28K, NEW 구간 비개선). `reports/gap_score_grid.md`.

---

## 확인 필요 항목 (페이퍼 운용 중)

| 항목 | 현황 | 조치 |
|------|------|------|
| 0D 필드 코드 | TODO 상수 (추정값) | 장 시간 raw 로그 수집 후 `core/orderbook.py` 수정 |
| OBI 필터 실효성 | 비활성 (`obi_filter_enabled: false`) | Phase 1→2→3 순서로 활성화 (`docs/obi_activation_plan.md`) |
| NXT API 지원 | 미확인 | NXT 시간에 `python scripts/test_nxt_api.py` 실행 |
| 확장 기간 PF | 2026-04-11~05-12 구간 PF 1.08 (KOSPI +31.58% 상승장) | 페이퍼 실측으로 실시간 PF 추적 |

검증 명령어: `docs/verification_commands.md`
후속 작업: [`docs/phase_followup_todo.md`](docs/phase_followup_todo.md)
OBI 활성화: [`docs/obi_activation_plan.md`](docs/obi_activation_plan.md)

## 일일 운영

운영 매뉴얼: [`docs/runbook.md`](docs/runbook.md)

- **정상**: 매일 08:00 `python gui.py` 시작, 15:30 이후 종료
- **안전망** (ADR-006): 00:01 자동 리셋, 08:05 OHLCV 갱신, 24h 가동 안내
- **자동화**: 월 07:30 유니버스 갱신, 평일 15:35 분봉 수집
- **검증**: `python selftest.py` → 7/7 OK + `docs/verification_commands.md`
