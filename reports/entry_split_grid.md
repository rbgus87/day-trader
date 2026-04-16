# 분할 매수 (entry_1st_ratio) 효과 검증

> 생성: 2026-04-16
> 결론: **Dead code — 시뮬 불필요**

---

## 사전 조사 결과

### 코드 추적

| 파일 | entry_1st_ratio 참조 | 역할 |
|------|---------------------|------|
| config.yaml | `entry_1st_ratio: 0.55` | 설정값 |
| config/settings.py | `entry_1st_ratio: float = 0.55` | 기본값 |
| core/paper_order_manager.py:91 | `qty_1st = int(total_qty * entry_1st_ratio)` | 1차 매수 수량 계산 |
| core/order_manager.py:75 | 동일 | 라이브 1차 매수 |
| **backtest/backtester.py** | **참조 없음** | 1주 단위, 분할 개념 없음 |
| **gui/workers/engine_worker.py** | **execute_buy_2nd 호출 없음** | 2차 매수 미실행 |

### execute_buy_2nd 호출자

```
grep "execute_buy_2nd" 전체 → 2개 파일에서 정의만 있고, 호출하는 코드 없음
```

### 실제 동작

1. **백테스트**: entry_1st_ratio 미참조. 1주 단위 시뮬이므로 분할 매수 개념 자체 없음.
2. **라이브/페이퍼**: engine_worker가 `execute_buy(total_qty)` 호출 → paper_order_manager가 `total_qty * 0.55` = 55% 수량만 매수 → **나머지 45%는 영원히 미매수.** 2차 매수 트리거 로직 미구현.

### 영향

- 라이브/페이퍼에서 의도한 자본의 55%만 사용 중
- 나머지 45%는 유휴 자본
- 백테스트와 라이브의 행동 불일치 (백테스트는 100%, 라이브는 55%)

---

## 권장

**entry_1st_ratio를 1.0으로 변경 (분할 매수 비활성)**

이유:
1. 2차 매수 로직이 미구현 (dead code)
2. 현재 55%만 매수하여 자본 비효율
3. 백테스트(100%)와 라이브(55%) 행동 불일치 해소
4. 분할 매수 전략은 향후 ADR에서 설계 + 구현 후 재도입

### config.yaml 변경 (권장)

```yaml
# Before
entry_1st_ratio: 0.55  # dead code — 2차 매수 미구현

# After
entry_1st_ratio: 1.0   # 100% 1차 매수 (분할 매수 미구현이므로 전량)
```

---

## TODO 완료

- [x] 분할 매수 효과 검증 — **dead code 확인, entry_1st_ratio 1.0 변경 권장**
  - 2차 매수 트리거 미구현 (execute_buy_2nd 호출자 없음)
  - 백테스트에 영향 없음 (미참조)
  - 라이브만 영향 (55% → 100%)
