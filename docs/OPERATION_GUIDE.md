# 페이퍼 운영 가이드

> 작성일: 2026-04-12 (Phase 3 Day 12 — 페이퍼 시작 전)
> 대상 시스템: Phase 1 필터 + Phase 2 ATR + Day 10 방어 + Phase 3 12종목 universe

---

## 현재 시스템 스냅샷

| 항목 | 값 |
|---|---|
| `paper_mode` | **true** (실거래 아님) |
| `force_strategy` | momentum |
| `initial_capital` | 1,000,000원 |
| `max_positions` | 3 |
| `max_trades_per_day` | 2 |
| `cooldown_minutes` | 120 |
| Universe | **12종목** (코스닥 11 / 코스피 1) |

1년(2025-04-01 ~ 2026-04-10) 백테스트 최종: **PnL +213,513 / PF 2.32 / 거래 229 / PF>1 12/12**.

---

## 사전 점검 체크리스트 (최초 시작 전 1회)

| 항목 | 확인 |
|---|---|
| `paper_mode: true` | ✅ |
| `initial_capital: 1,000,000` | ✅ |
| Universe 12종목 + market 필드 완비 | ✅ |
| Phase 1 필터 활성화 (ADX 20 + market) | ✅ |
| Phase 2 ATR 활성화 (stop 1.5, tp 3.0, trail 2.5) | ✅ |
| Day 10 방어 활성화 (daily -2%, blacklist) | ✅ |
| 키움 API 연결 정상 (토큰 발급 확인) | ✅ |
| DB 최신 데이터 (intraday / ticker_atr / index) | ✅ |
| 회귀 테스트 233 passed | ✅ |

---

## 일일 루틴

### 아침 (08:30 이전)

1. **GUI 실행** (`python gui.py`), 상단에 `[PAPER]` 뱃지 확인
2. **보유 포지션 없는지 확인** (전일 마감 시 forced_close가 정상 작동했다면 포지션 0이어야 함)
3. **WebSocket 연결 확인** — 상태바 또는 로그
4. **알림 수신 상태 확인** — 텔레그램 "단타 매매 시스템 시작 (GUI)" 메시지

### 장중 (09:00 ~ 15:30)

- 매수/매도 신호 알림 모니터링
- **개입 금지 원칙**: 페이퍼 단계에선 시스템 판단 신뢰
  - 예외: 외부 이벤트(장애·시스템 오류) 발생 시 강제청산
- **일일 -2% 도달**: `[RISK] 일일 손실 한도 도달` 알림 확인 → 자동 매매 중단 (보유 포지션은 정상 관리)

### 장 마감 후 (15:10 ~ 15:30)

- **15:10 강제청산 알림** 수신 확인
- **15:30 일일 보고서** 텔레그램 수신 확인 (`daily_pnl` 테이블에 기록됨)
- 당일 거래 DB 기록 검증
  ```bash
  python -c "
  import sqlite3
  conn = sqlite3.connect('daytrader.db')
  cur = conn.cursor()
  cur.execute('SELECT ticker, side, qty, price, pnl, exit_reason, traded_at FROM trades WHERE date(traded_at)=date(\"now\") ORDER BY traded_at')
  for r in cur.fetchall(): print(r)
  "
  ```

---

## 주간 루틴 (금요일 장 마감 후)

1. **주간 PF / PnL / 청산 분포** 확인
   ```bash
   python -c "
   import sqlite3
   from collections import Counter
   conn = sqlite3.connect('daytrader.db')
   cur = conn.cursor()
   cur.execute(\"SELECT exit_reason, pnl FROM trades WHERE side='sell' AND date(traded_at) >= date('now', '-7 days')\")
   rows = cur.fetchall()
   ec = Counter(r[0] for r in rows)
   pnl = sum(r[1] or 0 for r in rows)
   print(f'주간 거래 {len(rows)}건, PnL {pnl:+,.0f}')
   for k, n in ec.most_common():
       print(f'  {k}: {n}')
   "
   ```
2. **블랙리스트 상태 점검** — 현재 블랙 처리된 종목 목록
3. 필요 시 `scripts/extract_profitable.py` 재실행해 universe 후보 비교

## 월간 루틴 (월 초)

1. 1개월 성과 평가 (PF / PnL / MDD)
2. 시장 환경 변화 대응 — 코스피/코스닥 MA 추이 확인
3. 파라미터 튜닝 검토 (그리드 재실행 여부)

## 분기 루틴

1. **Universe 재선별** — 1년 롤링 백테스트 기준 `extract_profitable`
2. **ATR 캐시 갱신** — `python scripts/calculate_atr.py`
3. 새로 추가된 종목에 `market` 필드 부여 — `python scripts/update_universe_market.py`

---

## 실거래 전환 기준

### 전환 조건 (모두 충족)

- [ ] 페이퍼 **2개월 이상** 운영
- [ ] **PF 1.5+** 유지
- [ ] 1개월 최대 낙폭(MDD) **-5% 이하**
- [ ] **약세장 1회 이상** 경험 (발생했다면)
- [ ] 일일 루틴 **무결점 수행 30일+**

### 전환 절차

1. `config.yaml` → `paper_mode: false` (신중하게, 커밋 전 수동 검증)
2. **자본의 10%** 이하로 시작 (예: 실제 1,000만원 중 100만원)
3. 1개월 문제 없으면 **20%**
4. 3개월 문제 없으면 **50%**
5. 전체 자본 투입은 **6개월 이상** 검증 후 결정

### 즉시 중단 시그널

실거래 중 다음 발생 시 즉시 `paper_mode: true` 로 복귀:
- 누적 손실 **-10% 초과**
- 연속 손실일 **5일 이상**
- 시스템 장애(WebSocket 재연결 실패, API 오류 반복)
- 예상 외 패턴(텔레그램 알림 체감상 비정상)

---

## 미해결 과제 / 주의사항

1. **약세장 미검증**: 1년 데이터가 전반적 강세장. 약세장 대응 방어책(Day 10)은 실전 트리거 미경험
2. **섹터 집중**: 12종목 중 91%가 코스닥 — 코스닥 전반 약세 시 시스템 취약
3. **종목 드리프트**: 1년 선별 결과가 앞으로 1년 최적을 보장 안 함 — **분기 재선별 필수**
4. **Intraday 트레일링 포기**: Day 9 A/B/C 3실험 모두 실패. forced_close(15:30)가 지배적 청산 — 실전도 동일 패턴 예상
5. **trades 테이블 누적**: 과거 백테스트/페이퍼 기록이 DB에 남아있음. 블랙리스트가 참조하므로 **실거래 전 `trades` 테이블 클린업** 고려

---

## 주요 파일 위치

| 용도 | 경로 |
|---|---|
| 전체 설정 | `config.yaml` |
| Universe | `config/universe.yaml` |
| 환경 변수 (API 키 등) | `.env` |
| DB | `daytrader.db` |
| 로그 (loguru 기본) | `logs/` (해당 시) |
| Phase 1 결과 | `docs/PHASE1_RESULTS.md` |
| Phase 3 결과 | `docs/PHASE3_RESULTS.md` |
| 본 가이드 | `docs/OPERATION_GUIDE.md` |

---

## 비상 연락/참조

- 키움 OpenAPI+: https://apiportal.kiwoom.com/
- 프로젝트 커밋 이력: `git log --oneline`
- 테스트 실행: `python -m pytest tests/ -q`
- 단일 백테스트: `python -m backtest.compare_strategies --strategy momentum --start 2025-04-01 --end 2026-04-10`
