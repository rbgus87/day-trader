# Phase 후속 작업 TODO

> 최종 갱신: 2026-04-17 (페이퍼 시작 준비 완결)
> 현 baseline: **PF 3.28** (1주 가중) / 279건 / 60종목 / Pure trailing + 고정 -8% 손절
> 페이퍼 시작: 2026-04-17 (금), 자본 300만원

---

## 🔴 페이퍼 시작 전 필수 — 모두 완료

### 1. 청산 메커니즘 미세 튜닝

- [x] atr_trail_multiplier 미세 그리드 — **x1.0 최적 확인** (reports/multiplier_fine_grid.md)
- [x] atr_trail_min_pct / max_pct 그리드 — **R0 (min2%/max10%) 최적 확인** (reports/trail_range_grid.md)
- [x] stop_loss 조정 — **현재 설정 유지 (-8%)** (reports/stop_loss_grid.md)

### 2. 진입 조건 재검증

- [x] ADX 임계값 그리드 — **ADX 20 유지** (reports/adx_grid.md)
- [x] 거래량 비율 그리드 — **ratio 2.0 유지** (reports/volume_ratio_grid.md)
- [x] buy_time_end 그리드 — **12:00 유지** (reports/buy_time_grid.md)

### 3. 시스템 구조 검증

- [x] 시장 필터 효과 — **MA5 유지** (reports/market_filter_grid.md)
- [x] 분할 매수 — **entry_1st_ratio 1.0 적용** (reports/entry_split_grid.md)

### 4. 견고성 검증

- [x] Walk-Forward 검증 — **통과** (ADR-011, reports/walk_forward_simple.md)

### 5. 페이퍼 준비

- [x] Universe 재생성 (60종목, ATR 6%, 2026-04-15 기준)
- [x] 분봉 수집 (60종목 100% 커버리지)
- [x] 페이퍼 자본 300만원 설정 (ADR-013)
- [x] 주간 유니버스 자동 갱신 (ADR-012)
- [x] 일일 분봉 자동 수집 (ADR-014)

---

## 🔴 시뮬 vs 실측 갭 분석

### 6. 시뮬 wrapper vs 실제 backtester 괴리

- [ ] 시뮬 wrapper(simulate_*.py) vs backtest_single.py 청산 우선순위 차이 분석
- [ ] stop_loss 68건(24.4%)의 실제 청산 경로 세부 분석
- [ ] 향후 시뮬 작업 시 정책 결정 (wrapper vs backtester 직접 사용)

---

## 🟡 페이퍼 1주차 진행 중 + 회고

### 7. 페이퍼 결과 검증

- [ ] 페이퍼 결과 vs 백테스트 baseline 갭 측정 (갭 > 30% 시 원인 조사)
- [ ] 슬리피지 실측 (현재 추정 0.03%)
- [ ] NXT 체결 섞임 여부 확인

### 8. 사이징 정책 재검토

- [ ] 자본 가중 PF 1.88 수렴 원인 분석
- [ ] 사이징 대안 비교 (금액 균등 / 변동성 조정 / 저가주 제외)
- [ ] PF<1 종목 동적 제거 정책 설계

### 9. Dead config 정리

- [ ] screening_top_n 향후 재활용 또는 제거 결정

---

## 🟢 라이브 가기 전

### 10. 라이브 인프라

- [ ] 라이브 수수료 모델 (키움 계좌별 수수료율 반영)
- [ ] 자동 재시작 정책

---

## 🔵 미루기 가능 (성능 최적화)

- [ ] 주말/공휴일 자동 리셋 skip
- [ ] 시총 상한 추가 (대형주 자리 점유 방지)
- [ ] 실시간 ATR 계산 (현재 일봉 25일 평균)
- [ ] 백테스트 성능 최적화 (ProcessPool 캐시 개선)

---

## 작업 원칙

- 각 시뮬 후 결과 docs에 기록
- dead config/code 즉시 정리 (잔재 금지)
- 새 baseline 측정 후 CLAUDE.md 갱신
- 의사결정마다 ADR 작성
- 시뮬 wrapper 사용 시 backtester와의 괴리 명시
