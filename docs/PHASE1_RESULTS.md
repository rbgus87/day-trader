# Phase 1: 진입 품질 필터 최적화 결과

> 확정일: 2026-04-12
> 전략: MomentumStrategy (전일 고점 돌파)
> 기간: 2025-04-01 ~ 2026-04-10 (약 1년)
> 유니버스: 60종목 (코스피 23 / 코스닥 37)
> 초기 자본: 1,000,000원 · 비용: 수수료 0.015% · 세금 0.18% · 슬리피지 0.03%

---

## 최종 조합: **ADX20 + 시장 필터 (L)**

| 항목 | 변경 전 (필터 없음) | Phase 1 최종 (L) | 개선 |
|---|---:|---:|---:|
| 거래수 | 655 | 501 | -23% |
| Profit Factor | 1.23 | **1.46** | +19% |
| 총 PnL | +154,739 | **+208,303** | **+35%** |
| PF>1 종목 | 30 | **35** | +5 |

### config.yaml 변경

```yaml
trading:
  market_filter_enabled: true       # Phase 1 신규
  market_ma_length: 5

momentum:
  adx_enabled: true
  adx_length: 14
  adx_min: 20                       # 25 → 20
  rvol_enabled: false               # true → false
  vwap_enabled: false               # true → false
```

기타 모멘텀 파라미터는 유지:
`stop_loss_pct=-0.030`, `trailing_stop_pct=0.005`, `tp1_pct=0.16`,
`max_trades_per_day=2`, `cooldown_minutes=120`.

---

## 주요 발견

### 1. 시장 필터가 가장 큰 단일 효과
- 시장만(H): 655건 → 510건 (-22%), PnL +154k → **+187k (+21%)**
- 시장 필터 단독으로 PF를 1.23 → 1.40으로 끌어올림
- 코스피/코스닥 MA5 대비 약세 시장 진입 차단이 가짜 돌파 방어에 효과적

### 2. ADX 25는 과도, 20이 최적
- ADX20만(K): 645건 / PF 1.24 — ADX25(B, 644건 / PF 1.24)와 동일
- **ADX20 + 시장(L)**: PnL +208k, ADX25 + 시장 조합보다 우수
- 추세 강도 기준을 완화해도 시장 필터가 품질을 보장

### 3. RVol 은 엄격하면 거래 부족, 완화하면 PnL 붕괴
- RVol3만(F): 203건 / PF 1.81 / PnL +140k — PF 좋지만 거래량 적음
- RVol2.0 + 시장(I): 247건 / PF 1.31 / **PnL +81k** — 완화 시 순이익 급락
- 결론: **RVol 비활성화가 유리**

### 4. VWAP 필터 무효
- C(ADX+RVol) vs D(C + VWAP): 거래 -3건, PF 동일, PnL +273원
- 실효성 없음 → 제거

---

## 14개 조합 비교표

| 조합 | 거래 | PF | 총 PnL | PF>1 |
|---|---:|---:|---:|---:|
| A. 필터 없음 | 655 | 1.23 | +154,739 | 30 |
| B. ADX25 | 644 | 1.24 | +158,386 | 31 |
| C. ADX25+RVol3 | 197 | 1.78 | +129,879 | 20 |
| D. ADX25+RVol3+VWAP | 194 | 1.78 | +130,152 | 20 |
| E. 전부 (ADX25) | 156 | 2.26 | +150,467 | 19 |
| F. RVol3만 | 203 | 1.81 | +139,968 | 19 |
| G. RVol3+시장 | 162 | 2.15 | +144,657 | 18 |
| H. 시장만 | 510 | 1.40 | +187,355 | 33 |
| I. RVol2.0+시장 | 247 | 1.31 | +81,057 | 22 |
| J. RVol2.5+시장 | 200 | 1.66 | +130,032 | 21 |
| K. ADX20만 | 645 | 1.24 | +160,413 | 31 |
| **L. ADX20+시장 (최종)** | **501** | **1.46** | **+208,303** | **35** |
| M. ADX20+RVol2+시장 | 242 | 1.39 | +96,603 | 22 |
| N. ADX20+RVol2.5+시장 | 196 | 1.78 | +142,857 | 20 |

### 선정 기준
- 거래수 ≥ 200 (통계 신뢰도)
- PF>1 종목 ≥ 25 (종목 분산)
- PnL 최대화

L이 세 기준 모두에서 최상위.

---

## 인프라 변경 요약 (Day 3–4)

- `core/market_filter.py`: 코스피(001)/코스닥(101) MA5 기반 실시간 판단기
- `scripts/update_universe_market.py`: universe.yaml에 `market` 필드 자동 부여
- `data/db_manager.py`: `index_candles` 테이블 추가
- `scripts/collect_index_data.py`: ka20006으로 지수 일봉 수집 (각 3,000일)
- `backtest/backtester.py`: `ticker_market`, `market_strong_by_date` 기반 일별 매매 차단 + `build_market_strong_by_date` 헬퍼
- `scripts/grid_search_filters.py`: 14개 조합 ProcessPool 병렬 백테스트

---

## 남은 이슈 / Phase 2 후보

- `backtest/compare_strategies.py`는 아직 시장 필터 미지원 → 단발 검증은 `grid_search_filters.py`가 유일한 진실 소스
- Momentum 외 전략(Pullback/Flow/Gap 등)에도 동일한 필터 적용 시 개선 여지 미측정
- 지수 데이터는 12년치 확보됨 — 다년도 regime 교차 검증 가능
