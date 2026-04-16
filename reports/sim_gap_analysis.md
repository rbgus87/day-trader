# 시뮬 vs 실측 갭 분석

> 생성: 2026-04-16
> 실측: backtest_single.py (PF 3.02, 279건)
> 시뮬: simulate_f_atr_combined.py (PF 3.21, 277건)

---

## 갭 요약

| 지표 | 실측 (backtester) | 시뮬 (wrapper) | 차이 |
|------|-----------------|--------------|------|
| PF | 3.02 | 3.21 | -0.19 |
| 거래수 | 279 | 277 | +2 |
| stop_loss | 68 (24.4%) | 15 (5.4%) | +19%p |
| trailing_stop | 5 (1.8%) | 19 (6.9%) | -5.1%p |
| forced_close | 206 (73.8%) | 243 (87.7%) | -13.9%p |

---

## 원인: backtester의 stop_loss 우선 체크 (코드 구조 문제)

### backtester 청산 순서 (backtester.py:218-383)

```
매 캔들마다:
  ① if low <= position["stop_loss"]:        ← 218행, 라벨 "stop_loss"
  ② elif position.get("tp1_hit"):           ← 286행
       if high > highest_price:
           trailing 재계산 → stop_loss = max(old, new)  ← 328행
       if low <= position["stop_loss"]:      ← 332행, 라벨 "trailing_stop"
```

**①번이 ②번보다 먼저 실행.** `position["stop_loss"]`는 trailing이 갱신한 값도 포함하므로, trailing이 올린 stop에 low가 닿아도 ①에서 "stop_loss"로 라벨링.

**②번의 "trailing_stop" 라벨에 도달하려면:**
- ①번이 False (low > stop_loss) 이고
- ②번 블록 내에서 고점 경신 → trailing 재계산 → stop_loss 상향
- 상향된 stop_loss에 low가 닿는 경우
- 이건 **같은 캔들에서 high 경신 + low 하락이 동시에 일어나는 극히 드문 경우** → 5건(1.8%)만 발생

### wrapper 청산 순서 (simulate_f_atr_combined.py:95-140)

```
진입 시:
  stop_loss = max(ATR_stop, trailing(highest))  ← 111-112행, 즉시 trailing 적용

매 캔들마다:
  ① if high > highest_price:
       trailing 재계산 → stop_loss = max(old, new)  ← 121-124행
  ② if low <= stop_loss:
       라벨 = "trailing_stop" if stop > entry*0.975 else "stop_loss"  ← 132행
```

**wrapper는 trailing 갱신이 stop 체크보다 먼저.** 또한 진입 즉시 trailing을 계산하므로 초기부터 trailing이 활성.

---

## 차이 3가지

### 차이 1: 진입 캔들 trailing 계산

| | backtester | wrapper |
|---|---|---|
| 진입 시 | `highest_price = high` (206행), trailing 미계산 | `stop = max(ATR_stop, trailing(high))` (111-112행) |
| 효과 | 첫 캔들 stop = ATR stop only (-8%) | 첫 캔들부터 trailing 반영 |

**영향**: backtester에서 두 번째 캔들 low가 ATR stop(-8%)에 닿으면 "stop_loss". wrapper에서는 trailing이 이미 올린 stop에 닿으면 "trailing_stop".

### 차이 2: 캔들 내 실행 순서

| | backtester | wrapper |
|---|---|---|
| 순서 | stop체크 → trailing갱신 | trailing갱신 → stop체크 |
| 코드 | 218행 → 288행 | 121행 → 128행 |

**영향**: backtester에서 trailing이 stop을 올릴 수 있었지만, stop 체크가 먼저 실행되어 이전 값으로 판정.

### 차이 3: 라벨링 로직

| | backtester | wrapper |
|---|---|---|
| "stop_loss" | 218행의 모든 청산 | `stop <= entry * 0.975` |
| "trailing_stop" | 332행의 청산만 (극히 드문) | `stop > entry * 0.975` |

**영향**: backtester는 trailing이 올린 수익 구간 청산도 "stop_loss"로 라벨링. wrapper는 가격 기준으로 구분.

---

## 실증: stop_loss 68건의 실제 청산 사유

5종목 샘플(31건) 분석에서 stop_loss 7건 중:

| PnL% | 실제 사유 |
|------|---------|
| -4.64% | 순수 ATR stop 손절 |
| -5.62% | 순수 ATR stop 손절 |
| -2.55% | ATR stop 또는 trailing 하강 |
| -1.44% | trailing이 올린 stop에 체결 (수익 미달) |
| +0.27% | trailing이 올린 stop에 체결 (소폭 수익) |
| +3.66% | trailing이 올린 stop에 체결 (수익) |
| +13.26% | trailing이 크게 올린 stop에 체결 (대폭 수익) |

**stop_loss 7건 중 3건(43%)이 수익 청산** — trailing이 stop을 올렸는데 "stop_loss"로 라벨링된 것.

전체 68건에도 동일 비율 적용 시:
- 순수 손절: ~39건 (57%)
- trailing에 의한 수익/소폭 손실 청산: ~29건 (43%)

---

## 결론

### 갭 원인: **backtester의 라벨링 구조 (실측이 정확, 시뮬이 부정확)**

1. **실측 PF 3.02가 진짜 PF**. 시뮬 PF 3.21은 wrapper의 trailing 우선 계산으로 인해 과대 추정.
2. **stop_loss 24.4%는 순수 손절이 아님**. trailing이 올린 stop에 체결된 수익 거래도 "stop_loss"로 라벨링됨. 실질적 청산 분포는 "순수 손절 ~14% / trailing 의한 청산 ~12% / forced_close 74%"에 가까움.
3. **trailing_stop 1.8%가 낮은 이유**: 같은 캔들에서 high 경신 + low 하락이 동시에 일어나는 경우만 해당. 대부분의 trailing 청산은 이전 캔들에서 올려놓은 stop에 다음 캔들 low가 닿는 패턴이라 ①에서 잡힘.

### 이전 시뮬 결과 재해석

| 시뮬 결과 | 재해석 |
|----------|--------|
| I(ATR≥6%) PF 3.21 | **과대 추정** → 실제 3.02 |
| F(Pure trailing) PF 2.68 | **과대 추정** → 실제 ~2.5 추정 |
| A(Baseline) PF 2.55 | **과대 추정** → 실제 ~2.4 추정 |

시뮬 간 **상대적 순위는 유효** (F > A, I > H 등). 절대값만 ~0.2 과대.

### 향후 시뮬 방식 권장

**옵션 A (권장): 실제 backtester 직접 사용**
- config 파일을 동적으로 변경 → backtest_single.py 실행
- 느리지만 실측과 100% 일치
- 라벨링 괴리 없음

**옵션 B: wrapper 폐기**
- 기존 시뮬 스크립트(simulate_*.py)는 탐색용으로만 유지
- 최종 수치는 항상 backtest_single.py로 측정
- 시뮬 PF와 실측 PF의 ~0.2 갭을 항상 감안

**라벨링 수정은 불필요**: "stop_loss" 라벨이 순수 손절만 의미하지 않더라도, PF 자체에는 영향 없음 (PnL 계산은 동일). 라벨은 분석 편의일 뿐.
