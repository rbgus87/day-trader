# day-trader 운영 매뉴얼

## 일일 운영 흐름 (정상)

| 시각 | 동작 | 주체 |
|---|---|---|
| 08:00 | GUI 시작 (`python gui.py`) | **운영자** |
| 08:00 | 토큰 사전 갱신 (자동 cron) | 시스템 |
| 08:05 | 전일 OHLCV 갱신 (자동 cron, ADR-006) | 시스템 |
| 08:30 | 스크리닝 (자동 cron) | 시스템 |
| 08:50~ | WS 연결 + 틱 수신 | 시스템 |
| 09:00 | 개장 | 시장 |
| 09:05~12:00 | 매수 가능 시간 (signal_block_until / buy_time_end) | 시스템 |
| 15:10 | 미청산 포지션 강제청산 (자동 cron) | 시스템 |
| 15:30 | 일일 보고서 텔레그램 (자동 cron) | 시스템 |
| 15:30 이후 | GUI 종료 | **운영자** |

## 자동 안전망 (운영자 재시작 못 했을 때) — ADR-006

| 시각 | 동작 |
|---|---|
| 00:01 | `_daily_reset` — 리스크 카운터 리셋 + 전략 재등록 + OHLCV 갱신 |
| 08:05 | `_refresh_prev_day_ohlcv` — 전일 OHLCV 재조회 + 주입 |
| GUI 시작 시 | `_check_uptime_sanity` — 24시간 이상 가동 감지 시 텔레그램 안내 |

텔레그램 안내 예:
- `[자동] 일일 리셋 완료 — 60종목, 카운터 초기화`
- `[안내] GUI 26시간 이상 가동 중\n마지막 시작: 2026-04-14T13:51:59`
- `[경고] 전일 OHLCV 갱신 실패 — TimeoutError`

## 시작 전 체크리스트

- [ ] `python selftest.py` → 7/7 OK
- [ ] `python gui.py` 실행
- [ ] 상단 상태 패널: KOSPI/KOSDAQ 강세 확인
- [ ] WS 연결됨 표시 확인
- [ ] 텔레그램에 "단타 매매 시스템 시작" 메시지 수신 확인

## 비정상 종료 시 복구

1. 텔레그램으로 마지막 알림 시각 확인
2. `python selftest.py` 실행 — 환경 깨짐 여부 확인
3. `python gui.py` 재시작
4. 포지션 정합성:
   ```bash
   python -c "import sqlite3; c=sqlite3.connect('daytrader.db'); \
       print(c.execute(\"SELECT * FROM positions WHERE status='open'\").fetchall())"
   ```
5. 키움 API 보유잔고와 DB positions 불일치 시 GUI 상단 `포지션 불일치` 알림 → 강제청산 처리

## 데이터 백업

- **자동**: 매일 15:35 `backups/daytrader_backup_YYYYMMDD.db` (7일 보관, engine_worker `_safe_backup_db`)
- **수동 권장**: 주 1회 `cp daytrader.db daytrader_manual_YYYYMMDD.db`
- **Phase 완료 시 필수**: `cp daytrader.db daytrader_phase_N_YYYYMMDD.db`

## 일일 운영 종료 후 (15:30 이후)

```bash
# 당일 DB 정합성 검증
python scripts/check_db_integrity.py
# 기대: [OK] 불일치 0건, WARN 있으면 내용 확인

# 전체 기간 정합성
python scripts/check_db_integrity.py --all
```

검증 항목:
- trades.sum(pnl) == daily_pnl.total_pnl
- 미청산 positions (status='open') 0건
- 당일/누적 장부 정합 (buy 수량 == sell 수량)
- exit_reason / order_type / strategy 도메인 검증

## 운영 중 점검 (주 1회 권장)

```bash
# 백테스트 baseline 재확인
python scripts/backtest_single.py
# 기대: PF 2.91 / 185건 / 60종목

# 검증 명령어 전체 실행
# docs/verification_commands.md 참조
```

## 긴급 상황 대응

| 상황 | 조치 |
|---|---|
| 일일 손실 한도 도달 (`[HALT]` 알림) | 매수 차단됨. 보유 포지션은 정상 청산 대기. 다음 날 자동 해제 |
| WS 연결 끊김 | `_health_check`가 30초마다 자동 재연결 시도. 계속 실패 시 GUI 재시작 |
| 텔레그램 알림 중단 | 봇 토큰 확인 후 `gui.py` 재시작 |
| 매매 의도치 않은 작동 | GUI "일시정지" 버튼 (is_trading_halted 플래그) |

## 아키텍처 문서

- `CLAUDE.md` — 프로젝트 개요 + 전략
- `docs/spec/backtester_behavior.md` — 백테스터 명세 (baseline 진실)
- `docs/spec/live_baseline_comparison.md` — 라이브 대조 매트릭스
- `docs/adr/` — 주요 아키텍처 결정
- `docs/verification_commands.md` — 검증 명령어 모음
