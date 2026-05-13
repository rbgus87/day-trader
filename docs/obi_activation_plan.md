# OBI 필터 단계적 활성화 계획

> 작성: 2026-05-13 | 현재 상태: `obi_filter_enabled: false`

---

## 전제 조건

| 항목 | 상태 |
|------|------|
| `core/orderbook.py` | 구현 완료 |
| `core/kiwoom_ws.py` 0D 파싱 | 구현 완료 |
| `engine_worker.py` OBI 게이트 | 구현 완료 |
| 0D 필드 코드 확인 | **미완료** — raw 로그 수집 필요 |
| 0D 실제 수신 확인 | **미완료** — 장 시간 WS 수신 확인 필요 |

**OBI 필터는 0D 필드 코드가 확정되기 전까지 활성화하지 않는다.**

---

## Phase 1 — 0D 수신 관찰 (1~3 거래일)

### 목적
- 0D 메시지가 실제로 수신되는지 확인
- `_dispatch_orderbook` 파싱이 정상 동작하는지 확인
- 0D 필드 코드(`_OD_BID_PRICE_FIELDS` 등) 추정값이 올바른지 검증

### 설정
```yaml
# config.yaml (현재 기본값 유지)
obi_filter_enabled: false   # Phase 1: 필터 비활성, 수신만 관찰
```

### 로그 확인 방법
GUI 실행 후 로그 탭 또는 터미널에서 확인:
```
# 0D 구독 요청 전송 로그
[INFO] WS 0D(호가) 구독: N종목

# 호가 파싱 성공 시 (OBI 필터 비활성이라도 스냅샷은 저장됨)
# → 직접 로그는 없으나 WARN이 없으면 정상 파싱 중
[WARN] [OB] 호가 파싱 실패: ...   ← 이 메시지가 나오면 필드 코드 문제
```

### 0D 필드 코드 확인 방법

`core/kiwoom_ws.py`에서 임시 디버그 로그를 추가하거나:
```python
# _dispatch_orderbook 내부에 임시 추가
logger.debug(f"[OD-RAW] {ticker} values keys={list(values.keys())[:10]}")
```

또는 `scripts/test_nxt_api.py`의 Test 3(WS 수신)을 장 시간에 실행하여
0D 메시지의 `values` 키 목록을 확인한다.

### Phase 1 완료 조건
- [ ] 장 시간 0D 메시지 수신 확인 (0D_count > 0)
- [ ] `values` 키 목록 캡처 완료
- [ ] `core/orderbook.py`의 `_OD_*_FIELDS` 상수를 실제 코드로 수정
- [ ] `OrderbookSnapshot.bid_prices[0]` 값이 현재가와 일치하는지 검증

---

## Phase 2 — OBI 필터 활성화 + 로그 모니터링 (3~5 거래일)

### 설정 변경
```yaml
# config.yaml 수정
obi_filter_enabled: true     # 활성화
obi_min: 0.55                # 매수 우위 기준
spread_max_pct: 0.005        # 0.5% 스프레드 상한
ask_wall_block_enabled: false  # 처음에는 ask_wall 비활성 (노이즈 가능성)
```

### 모니터링 지표

**로그에서 확인:**
```
[OBI] 매수세 부족 차단: {ticker} OBI={obi:.3f}
[OBI] 스프레드 과대 차단: {ticker} spread={spread:.4f}
```

**일별 집계:**
- 총 시그널 수 vs OBI 차단 건수
- OBI 차단 후 해당 종목 가격 추이 (이후 상승했는지 → 필터 오작동, 하락했는지 → 정상)

### Phase 2 판단 기준

| 결과 | 조치 |
|------|------|
| OBI 차단율 < 5% | 필터 효과 미미 → `obi_min` 낮추거나 비활성 검토 |
| OBI 차단율 5~20% | 정상 범위. 차단된 종목 추이 추적 |
| OBI 차단율 > 20% | 너무 많이 차단 → `obi_min: 0.50`으로 낮추거나 필드 코드 재확인 |
| 차단 후 상승 > 차단 후 하락 | 필드 코드 반전 가능성 (bid/ask 혼동) → 즉시 비활성 |

---

## Phase 3 — 파라미터 조정 (5일 이후)

Phase 2 로그 데이터 기반으로 파라미터를 조정한다.

### obi_min 조정 기준

| obi_min | 의미 |
|---------|------|
| 0.50 | 필터 없음 (균형 이상이면 모두 통과) |
| 0.55 | 약간 매수 우위 (기본값) |
| 0.60 | 명확한 매수 우위만 진입 |
| 0.65 | 강한 매수 우위만 진입 (차단율 높음) |

### ask_wall 활성화 시점
- Phase 2에서 OBI 차단이 안정적으로 동작한 후
- `ask_wall_block_enabled: true` 전환
- 현재가 근처 3% 매도벽 감지 — 허위 돌파 차단 용도

### spread_max_pct 조정
- 현재 0.5%는 유동주(삼성전자 등)에 적합
- KOSDAQ 소형주는 스프레드가 더 넓으므로 `0.01`로 완화 가능

---

## Phase 4 — 백테스트 불가 한계 인식

OBI 필터는 실시간 전용이므로 백테스트로 PF를 검증할 수 없다.
페이퍼 트레이딩 결과로만 판단한다.

### 판단 지표 (4주 후)

| 지표 | 기준 |
|------|------|
| OBI 필터 통과 후 승률 | > 현재 baseline |
| OBI 필터 차단 후 해당 종목 최대 수익 | < 진입 임계값 |
| forced_close 비율 변화 | 감소하면 긍정적 |

---

## 롤백 절차

OBI 필터가 성능을 저하시킨다면:

1. `config.yaml`에서 `obi_filter_enabled: false`로 즉시 롤백
2. GUI 재시작 없이 다음 진입 시부터 적용됨 (단, config hot-reload 미구현 시 재시작 필요)
3. 백테스트 baseline PF 4.881은 OBI 필터와 무관하므로 복원 불필요

---

## 참고 파일

- `core/orderbook.py` — OrderbookSnapshot / OrderbookManager
- `core/kiwoom_ws.py` — `_dispatch_orderbook`, `_OD_*_FIELDS` TODO 상수
- `docs/nxt_api_investigation.md` — NXT 지원 여부 조사
- `scripts/test_nxt_api.py` — WS 0D 수신 확인 스크립트
