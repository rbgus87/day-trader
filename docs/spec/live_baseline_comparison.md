# Baseline 8종 라이브 대조

> **목적**: Phase 1 명세에서 도출한 "반드시 동일해야 할 8종"을 라이브 코드와 1:1 대조.
>
> **조사 범위**:
> - 백테스트: `backtest/backtester.py`, `strategy/base_strategy.py`, `strategy/momentum_strategy.py`
> - 라이브: `main.py`, `gui/workers/engine_worker.py`, `risk/risk_manager.py`, `core/market_filter.py`
>
> **판정 기준**: ✅ 동일 / ⚠️ 유사(의도 같으나 세부 차이) / ❌ 불일치 / ❓ 확인 불가

---

## 1. 15:10 강제청산

### 백테스트
- `backtest/backtester.py:396-412` — TP1 미히트 마지막 캔들 강제청산
- `backtest/backtester.py:288-305` — TP1 히트 + 마지막 캔들 동시 시 잔여 forced_close
- `backtest/backtester.py:377-393` — TP1 히트 상태 마지막 캔들 forced_close
- **트리거**: `idx == len(candles) - 1` — 당일 마지막 1분봉
- **exit_price**: `row["close"] × (1 − slippage)` (해당 캔들 종가)
- **수수료**: commission + tax 모두 반영
- **pnl**: `(net_exit − net_entry) × remaining_ratio` (TP1 미히트는 remaining 미적용 — §4.6 bug)

### 라이브 — `main.py:384-401` (`force_close` 함수)
- **트리거**: APScheduler cron `hour=15, minute=10` (`main.py:509-512`)
- **exit_price**: `int(pos.get("entry_price", 0))` — **진입가를 청산가로 사용** (!)
- **수수료**: PaperOrderManager/OrderManager 내부 로직에 위임 (별도 반영 없음)
- **pnl 계산**: 함수 내부 PnL 계산 **없음** (price=entry_price이므로 pnl=0)

### 라이브 — `gui/workers/engine_worker.py:838-863` (`_force_close`)
- **트리거**: APScheduler cron `hour=15, minute=10` (L269-272)
- **exit_price**: `self._latest_prices.get(ticker, pos.get("entry_price", 0))` — **최신 틱 가격 우선, 폴백 진입가**
- **pnl 계산**: L846-847 `(close_price − entry) × qty` 직접 계산
- **settle_sell 호출**: `self._risk_manager.settle_sell(ticker, close_price, qty)` (L854)

### 판정: ❌ 불일치 (main.py가 결정적 결함)

**차이점**:
1. **main.py는 최신가를 사용하지 않음** — `price=entry_price`로 고정 청산 → 손익 항상 0으로 기록됨 (심각한 버그)
2. engine_worker.py는 `_latest_prices` 추적으로 합리적이지만, 백테스트의 "마지막 분봉 종가"와 완전 동일하지는 않음 (틱 수신 주기 / 시장 마감 직전 변동성)
3. 백테스트는 수수료를 exit_price_slipped에 바로 적용. 라이브는 order_manager에 위임.

**수정 방안**:
- `main.py:384-401` 전면 재작성 — engine_worker.py 스타일로 최신가 추적 + pnl 계산
- 또는 main.py 제거하고 engine_worker.py 단일 엔트리 통합 (ADR 결정 필요)

---

## 2. 09:05 신호차단

### 백테스트
- `strategy/base_strategy.py:24-25` — 클래스 상수 하드코딩:
  ```python
  BLOCK_UNTIL = time(9, 5)
  MARKET_CLOSE = time(15, 20)
  ```
- `strategy/base_strategy.py:59-62` `is_tradable_time()`:
  ```python
  return self.BLOCK_UNTIL <= now <= self.MARKET_CLOSE
  ```
- `can_trade()` (L64-75) 내부에서 호출, False면 `generate_signal` 차단
- `set_backtest_time(ts.time())` (backtester.py:171)로 시뮬 시각 주입

### 라이브
- **동일한 `BaseStrategy` 클래스 사용** — 하드코딩 값 공유
- `_backtest_time` 이 `None`이므로 `datetime.now().time()` 사용 (`base_strategy.py:61`)

### 판정: ✅ 동일

**차이점 없음.** 단, `config.yaml` 의 `trading.signal_block_until: "09:05"` 키는 **backtester, 라이브 어디서도 읽지 않음** (Phase 1 모순 #6). 현재 값이 하드코딩과 같아서 문제 없지만 config 변경 시 침묵 무시됨.

**수정 방안 (Phase 2 ADR 대상)**:
- config 값을 읽도록 변경 (양쪽 모두) 또는 config 키 제거

---

## 3. 일일 리셋 (포지션/카운터/블랙리스트 갱신)

### 백테스트
- `backtest/backtester.py:620` — 일자 루프 내 `strategy.reset()` 호출
- `strategy/base_strategy.py:93-97` `reset()`:
  ```python
  self._trade_count = 0
  self._last_exit_time = None
  self._has_position = False
  ```
- MomentumStrategy의 `reset()` (L236-238) 은 super().reset() 호출 — 전일 고가/거래량은 `_setup_strategy_day` 재주입
- 블랙리스트/휴식은 날짜별 in-memory 재계산 (새 일자 진입 시 자동 갱신)

### 라이브 — `main.py:384-401` (force_close)
- `risk_manager.save_daily_summary()` — DB에 당일 집계 저장
- `risk_manager.reset_daily()` (L337-340): `_daily_pnl=0, _halted=False, _positions.clear()`
- `active_strategies = {}` — **전략 dict 완전 비움**
- **`strategy.reset()` 미호출**

### 라이브 — `gui/workers/engine_worker.py:855-863`
- 동일한 3단계 + `_candle_history.clear()`, `_daily_halt_notified = False`
- 역시 **`strategy.reset()` 미호출**

### 판정: ❌ 불일치 (구조적)

**차이점**:
1. **`active_strategies = {}` → 전략이 아예 사라짐**. `risk_manager.reset_daily()` 후 재등록 로직이 **없음**. 즉 같은 프로세스가 15:10 이후에도 살아있어도 **다음날 매매 불가** (startup 경로 L548-614에서 한 번만 등록).
2. 실운영 모델: "매일 프로세스 재시작" 전제. 이 전제가 문서화 안 됨.
3. 백테스트는 한 프로세스 내에서 365일을 연속 처리하므로 `strategy.reset()`으로 전략 인스턴스를 재활용. 라이브는 인스턴스 폐기 후 재생성 의존.

**수정 방안**:
- A안: `reset_daily` 후 active_strategies 재등록 로직 추가 (전략 인스턴스 재생성)
- B안: 각 strategy에 reset() 호출 + prev_day 데이터 재주입 루프 추가
- C안: 현행 구조 유지하되 CLAUDE.md에 "매일 재시작 필수" 명문화 (가장 단순)

→ Phase 2 ADR 결정 필요

---

## 4. 전일 OHLCV 주입 (set_prev_day_data)

### 백테스트 — `backtest/backtester.py:637-666` `_setup_strategy_day`
- **데이터 소스**: 전일 `day_df` (1분봉 전체)
- `prev_high = float(prev_day_df["high"].max())` (L649)
- `prev_volume = int(prev_day_df["volume"].sum())` (L650)
- `strategy.set_prev_day_data(prev_high, prev_volume)` (L651)
- 매일 호출

### 라이브 — `main.py:589-614` / `engine_worker.py:357-386`
- **데이터 소스**: 키움 REST API `rest_client.get_daily_ohlcv(ticker)` — 일봉 직접 조회
- `items[1]` (index 1 = 전일) 에서 `high_pric`, `acml_vol` 추출
- 값 추출: `abs(float(prev.get("high_pric", 0)))`, `abs(int(prev.get("acml_vol", prev.get("acml_vlmn", 0))))`
- **startup 시 단 한 번 호출** — 매일 갱신 로직 없음

### 판정: ⚠️ 유사

**차이점**:
1. **소스가 다름**: 백테스트는 1분봉 집계, 라이브는 일봉 API. 같은 날에 대해 같은 값이 나와야 정상이지만 API가 독립 집계라 미세한 차이 가능성.
2. **갱신 주기**: 백테스트는 매일, 라이브는 startup 시 1회. Day 3 이상 연속 운영 시 **Day N의 "전일 고가"가 Day 0 기준 값**으로 stale.
3. `acml_vol` vs `acml_vlmn` 필드명 폴백 — API 스펙 변동 대비 (라이브 특유 방어 코드)

**수정 방안**:
- 15:10 force_close 직후 또는 다음 날 08:00 토큰 갱신 시 전일 OHLCV 재조회 + set_prev_day_data 갱신
- §3의 "일일 리셋" 수정 시 함께 처리

---

## 5. 시장 필터 (KOSPI/KOSDAQ MA5)

### 백테스트 — `backtest/backtester.py:25-61`, `573-578`
- `build_market_strong_by_date(db_path, ma_length=5)`: `index_candles` DB 테이블에서 index_code 001(KOSPI)/101(KOSDAQ) 조회
- 각 날짜 `i`에 대해 **직전 ma_length 일 종가 평균**으로 MA 계산, `strong = close > MA`
- `run_multi_day_cached` 내 일별 skip 판정 (L573-578):
  ```python
  if market_filter_enabled and ticker_market in ("kospi", "kosdaq"):
      if not strong.get(ticker_market, True):
          skip_day = True
  ```

### 라이브 — `core/market_filter.py:46-114` `MarketFilter`
- **데이터 소스**: 키움 `get_index_daily(index_code)` API
- `_check_index`: `items[0]`=현재, `items[1..ma_length]`=MA 기간. `current > MA` 여부 반환
- `is_allowed(market)`: market="kospi" → `kospi_strong`, "kosdaq" → `kosdaq_strong`, unknown → OR
- `refresh()`: 비동기 호출, 실패 시 보수적 False (매수 차단)

### 라이브 적용 — `engine_worker.py:389-407, 681-687`
```python
# 시장 필터 초기 갱신 (startup)
await self._market_filter.refresh()
# signal_consumer에서 매수 차단
if not self._market_filter.is_allowed(market):
    continue
```

### 라이브 적용 — `main.py`
- **`main.py`에는 시장 필터가 전혀 없음** (`_market_filter` 키워드 0건)

### 판정: ❌ 불일치 (main.py 결정적 결함)

**차이점**:
1. **main.py 미구현**: signal_consumer(L279-326)에 시장 필터 체크 없음. engine_worker.py와 양립할 수 없는 상태.
2. 로직 자체는 등가 (current > MA). 다만 MA 포함 범위가 백테스트는 "직전 N일", 라이브는 "items[1..N]" (items[0]=현재 제외) — **의도 동일**.
3. **갱신 주기**: 백테스트는 일자별 자동. 라이브는 **startup 1회만**. 프로세스 연속 운영 시 시장 상태 stale.
4. 실패 정책: 백테스트는 "데이터 없으면 허용" (`strong is None`). 라이브는 "보수적 False (매수 차단)". **정반대**.

**수정 방안**:
- main.py 에 MarketFilter 통합 (또는 main.py 제거)
- 매일 08:30 스크리닝 시 `market_filter.refresh()` 함께 호출
- 실패 정책 통일 (baseline이 "데이터 없으면 허용"이라 라이브도 맞추거나 ADR로 변경)

---

## 6. 블랙리스트 (N일 손실 M회 → 휴식)

### 백테스트 — `backtest/backtester.py:581-599`
- 트리거: `blacklist_enabled=True`
- 로직: 현재 일자 `date` 기준 `cutoff = date − blacklist_lookback_days`, `all_trades` (in-memory) 중 `cutoff ≤ exit_date < date` 범위 + `pnl < 0` 건수 ≥ `blacklist_loss_threshold` 면 skip
- **종목 독립 집계 아님** — 전체 거래 중 손실 건수 (ticker 구분 없음)

```python
if recent_losses >= bl_threshold:
    skip_day = True
```

### 라이브 — `risk/risk_manager.py:191-227` `is_ticker_blacklisted(ticker, ...)`
- 트리거: `blacklist_enabled=True`
- 로직: DB `trades` 테이블 조회 — `ticker=? AND side='sell' AND pnl<0 AND date(traded_at)>=since` COUNT
- `since = now − blacklist_lookback_days`
- **종목별 독립 집계** — 해당 ticker의 손실 건수만

### 판정: ❌ 불일치 (집계 단위 다름)

**차이점**:
1. **백테스트는 전체 거래, 라이브는 종목별 거래**로 집계 기준이 완전히 다름.
2. 예: 라이브는 "종목 A가 5일간 3번 손실 → A만 블랙", 백테스트는 "60종목 전체가 5일간 3번 손실 → 모든 종목 block"
3. Phase 1 명세(docs/spec/backtester_behavior.md:288)의 기술이 **부정확** — "최근 lookback 내 손실 거래" 라고만 써서 ticker별인지 전체인지 애매.
4. 두 집계 방식은 **완전히 다른 결과**를 낼 수 있음.

**수정 방안**:
- A안: 라이브를 백테스트에 맞춤 → "전체 최근 손실 M회 이상이면 전일자 skip" (지금 라이브는 ticker별)
- B안: 백테스트를 라이브에 맞춤 → "종목별 블랙리스트" (PF 재측정 필요)
- **ADR 결정 필수** — baseline PF 2.91 출처가 어느 버전인지 확인 후 결정

→ 만약 PF 2.91이 A안 기준이면 라이브를 대대적 수정. B안 기준이면 backtester 수정.

현재 코드 기준으로 **A안이 baseline** (backtester 구현).

---

## 7. 연속손실 휴식 (consecutive_loss_rest)

### 백테스트 — `backtest/backtester.py:602-614`
- 트리거: `consecutive_loss_rest_enabled=True`
- 소스: `daily_pnl_by_date` (in-memory, 이전 날짜들 집계)
- 로직: 현재 date 직전부터 역순으로 `daily_pnl < 0` 카운트, 최초 `>= 0` 만나면 중단
- `consecutive >= consecutive_loss_threshold` 면 skip

```python
consecutive = 0
for d in past_dates:
    if daily_pnl_by_date[d] < 0:
        consecutive += 1
    else:
        break
if consecutive >= rest_threshold:
    skip_day = True
```

### 라이브 — `risk/risk_manager.py:151-189` `is_in_loss_rest`
- 트리거: `consecutive_loss_rest_enabled=True`
- 소스: DB `trades` 테이블, `side='sell' AND date(traded_at) < today` GROUP BY date
- 로직: `ORDER BY dt DESC LIMIT max(threshold*2, 10)` 로 내림차순 조회, 손실일 카운트, 흑자일 만나면 중단

### 판정: ✅ 동일

**차이점 없음.** 두 구현 모두 같은 의미론:
- "어제부터 역순으로 연속 손실일 카운트"
- "N개 이상이면 오늘 skip"

구현 소스(in-memory vs DB)만 다름. 같은 거래 이력 → 같은 결과.

**주의**: Phase 1 명세 §10.1의 `consecutive_loss_rest_days` config 키는 **양쪽 모두 읽지 않음** (하드코딩 1일). 백테스트는 "그날만 skip → 다음날 다시 판정", 라이브도 같음. 일치.

---

## 8. 쿨다운 (1트레이드/일/종목)

### 백테스트 — `strategy/base_strategy.py:64-112`
- `can_trade()`: `_has_position`, `_trade_count < _max_trades`, `_is_cooldown_elapsed()`, `is_tradable_time()` 모두 통과해야 True
- `on_entry()`: `_has_position=True, _trade_count += 1`
- `on_exit()`: `_has_position=False, _last_exit_time = backtest_time or now`
- `_is_cooldown_elapsed()`: `(now − _last_exit_time) / 60 >= _cooldown_minutes`
- 매일 `reset()` → `_trade_count=0`

### 라이브
- **동일한 BaseStrategy 클래스 사용**
- `strategy.on_entry()` 호출 위치: `momentum_strategy.py:generate_signal` 내부가 아닌 **backtester 쪽에만 있음** (`backtester.py:206`)
- **라이브는 `strategy.on_entry()` / `on_exit()` 호출 지점이 없음**

grep 검증:
```
$ grep -n "strategy.on_entry\|strategy.on_exit" main.py gui/workers/engine_worker.py risk/risk_manager.py
(결과 없음)
```

### 판정: ❌ 불일치 (결정적 결함)

**차이점**:
- 백테스트: `on_entry()`, `on_exit()` 훅이 backtester에서 호출됨 → `_trade_count`, `_last_exit_time`, `_has_position` 상태 관리 작동
- 라이브: **훅 호출 없음** → `_trade_count` 영원히 0, `_has_position` 영원히 False, `_last_exit_time` 영원히 None → `can_trade()` 항상 True
- 결과: `max_trades_per_day` (2) 와 `cooldown_minutes` (120) 설정이 **라이브에서 완전히 무시됨**

**단**: 라이브는 risk_manager에 포지션이 있는지 별도 체크 (`candle_consumer` L258 `get_position(ticker)`, signal_consumer L289 `max_positions` 체크)로 "동시 포지션"은 막음. 하지만 "하루에 같은 종목 재진입" 막는 로직이 **없음**.

**수정 방안**:
- A안: signal_consumer에서 `strategy.on_entry()` 호출, 매도 시 `strategy.on_exit()` 호출
- B안: risk_manager에 ticker별 당일 trade_count 추적 추가
- C안: config의 `max_trades_per_day=2`가 "같은 종목 하루 2회"면 A안이 의미 맞음

**오늘(4/15) 실제 매매 기록**으로 검증: 027360, 005930, 032820 각 1건씩 — max_trades_per_day=2 미달이라 현재 문제 안 됨. 하지만 **설정이 기능하지 않는 상태**.

---

## 요약 매트릭스

| # | 항목 | 백테스트 위치 | 라이브 위치 | 판정 |
|---|------|-------------|-----------|------|
| 1 | 15:10 강제청산 | `backtester.py:377-412` | `main.py:384-401` / `engine_worker.py:838-863` | ❌ `main.py`는 진입가로 청산(PnL=0). engine_worker만 최신가 |
| 2 | 09:05 신호차단 | `base_strategy.py:24,59-62` | 동일 (BaseStrategy 공유) | ✅ |
| 3 | 일일 리셋 | `backtester.py:620` (`strategy.reset()`) | `main.py:384-401` / `engine_worker.py:838-863` | ❌ `active_strategies={}` 후 재등록 없음. 매일 재시작 필수 (암묵적) |
| 4 | 전일 OHLCV 주입 | `backtester.py:637-666` (1분봉 집계) | `main.py:589-614` / `engine_worker.py:357-386` (일봉 API) | ⚠️ 소스 다름, 갱신 주기 다름 (startup 1회 vs 매일) |
| 5 | 시장 필터 | `backtester.py:25-61, 573-578` | `engine_worker.py:229-237, 389-407, 681-687` | ❌ `main.py` 미구현. 갱신 주기/실패 정책도 차이 |
| 6 | 블랙리스트 | `backtester.py:581-599` (전체 집계) | `risk_manager.py:191-227` (종목별 집계) | ❌ 집계 단위 다름 (전체 vs ticker별) — 결과 상이 |
| 7 | 연속손실 휴식 | `backtester.py:602-614` (in-memory) | `risk_manager.py:151-189` (DB) | ✅ (의미론 동일) |
| 8 | 쿨다운 / 1트레이드 | `base_strategy.py:64-112` + `backtester.py:206, 257` | BaseStrategy 있으나 **on_entry/on_exit 미호출** | ❌ `_trade_count` 미갱신 → 쿨다운·max_trades 완전 무시 |

**합계: ✅ 2 / ⚠️ 1 / ❌ 5** — **baseline 일치율 25%**

---

## 라이브에만 존재하는 추가 로직 (명세 밖 찌꺼기 후보)

### A. `risk_manager.is_trading_halted()` (`risk_manager.py:133-146`)
- **백테스트에 없음** (Phase 1 명세 §6.2 `daily_max_loss` 미구현)
- 라이브: signal_consumer가 아닌 candle_consumer에서 체크 (`main.py:239`, `engine_worker.py:619`)
- daily_max_loss_pct (-1.5%) 도달 시 신호 차단
- **baseline 외 기능** → ADR 결정 필요 (유지 / 제거 / backtester에 추가)

### B. `risk_manager.position_scale` + `reduced_position_pct` (`risk_manager.py:29, 232-248`)
- 3일 연속 손실 시 포지션 50%로 축소
- **백테스트에 없음** (사이징 자체가 없음)
- 라이브에서 `max_qty *= position_scale` 적용
- **baseline 외 기능**

### C. `self._latest_prices` 추적 (engine_worker 전용)
- 모든 틱마다 최신가 저장 → force_close 청산가 결정용
- main.py에 **없음** → 15:10 청산 버그 원인
- **수정 방안**: main.py에 동일 dict 추가 또는 main.py 제거

### D. `cost > available_capital` 체크 (`engine_worker.py:712-715`)
- 자본 부족 시 매수 스킵
- main.py에 없고 백테스트에도 없음
- **baseline 외 기능** — 라이브 전용 리스크 가드

### E. `_daily_halt_notified` 1회성 알림 (engine_worker 전용, L99, 620-633, 860)
- 일일 손실 한도 최초 도달 시 텔레그램 알림
- 하루 1회 제한
- **baseline 외 기능** — 운영 편의

### F. `force_strategy` config 키 (`main.py:566, engine_worker.py:333`)
- 비어있으면 "momentum", 아니면 경고 후 무시
- 라이브/baseline 양쪽 모두 의미 없음 (지금은 항상 momentum 단일)
- **dead code 후보** — config.yaml 에서도 `force: "momentum"` 이라 실질 무효

### G. `StrategySelector` (`screener/strategy_selector.py`)
- 현재 항상 momentum 반환 (주석 "참고용 — 현재 select는 항상 momentum")
- 라이브에서 `main.py:172`, `engine_worker.py:226` 에서 인스턴스화만 하고 **select 호출 안 함**
- **dead code 확정**

### H. `screening_top_n` / `screener_top_n` (`config.yaml:40`)
- 값: 5
- 스크리닝 통과 중 상위 N개만 매매 대상이어야 하지만, **실제로는 universe 전체가 매매 대상**
- 스크리닝 결과는 score 업데이트 + 텔레그램 알림 용도만 (`main.py:359-377`)
- baseline에도 관련 로직 없음 (universe 전체 대상)
- **설계와 구현 불일치** — "스크리닝 top 5가 매매 대상"이라는 오해 유발

### I. PaperOrderManager / OrderManager 내부 수수료·슬리피지 처리
- backtester: 청산 시점에 `exit_price × (1 ± slippage)`, `fee = exit × (commission + tax)` 직접 계산
- 라이브: **주문 실행 시 수수료/슬리피지 계산 없음** — 실제 체결가가 키움 API 응답
- PaperOrderManager도 현재가 그대로 기록 (시뮬 슬리피지 적용 X)
- **baseline 불일치** — 라이브 PnL이 backtester PnL보다 **낙관적**으로 기록됨

---

## 불일치 수정 우선순위 (Phase 2 ADR 입력)

### 🔴 Critical (baseline 불일치 + 실거래 영향)

| # | 항목 | 영향 | 권고 |
|---|---|---|---|
| 1 | main.py 15:10 청산 = entry_price | PnL=0 기록, 실제 손익 미집계 | engine_worker.py 스타일로 변경 |
| 5 | main.py 시장 필터 미구현 | 약세장에서도 매수 진행 | MarketFilter 통합 |
| 6 | 블랙리스트 집계 단위 (전체 vs 종목별) | PF 2.91 측정값과 실거래 분포 상이 | ADR로 A/B 선택 + 한쪽 통일 |
| 8 | 쿨다운·max_trades 완전 무시 | 같은 종목 하루 10회 매매 가능 | `on_entry`/`on_exit` 훅 호출 추가 |

### 🟡 High (baseline 불일치 + 운영 영향)

| # | 항목 | 영향 | 권고 |
|---|---|---|---|
| 3 | 일일 리셋 후 전략 재등록 없음 | 매일 재시작 필수 (암묵적) | 재등록 로직 추가 또는 명문화 |
| 4 | 전일 OHLCV stale | Day 2 이후 낡은 전일고가 사용 | 매일 새벽 갱신 추가 |
| I | 라이브 수수료·슬리피지 미반영 | PnL 과대기록 | PaperOrderManager에 비용 모델 반영 |

### 🟢 Medium (baseline 외 기능)

| # | 항목 | 판단 필요사항 |
|---|---|---|
| A | `is_trading_halted` (daily_max_loss) | baseline에 추가할지, 라이브에서 제거할지 |
| B | `position_scale` (연속손실 축소) | 사이징 논의와 연계 |
| D | 자본 부족 체크 | 사이징 논의와 연계 |
| E | `_daily_halt_notified` | 운영 편의 기능 — 유지 권장 |

### ⚪ Cleanup (dead code)

| # | 항목 | 조치 |
|---|---|---|
| F | `force_strategy` | 제거 (config + 코드) |
| G | `StrategySelector` 미사용 | 제거 (select 항상 momentum) |
| H | `screening_top_n` 미사용 | 제거 또는 실제 매매 대상 제한 로직 구현 |

---

## 결론

**라이브 코드는 baseline과 실질적으로 다른 시스템**:

- main.py: 15:10 청산 버그 + 시장 필터 미구현으로 실운영 불가
- engine_worker.py: main.py 대비 완전도 높으나 여전히 4/8 불일치
- 공통: 쿨다운·max_trades 무시, 블랙리스트 집계 차이, 일일 리셋 후 전략 사라짐

**PF 2.91 baseline 보장은 현 라이브로 불가능**. Phase 2 재조립에서 최소 🔴 Critical 4건을 backtester에 맞춰 통일해야 baseline 재현 가능.

다음 결정 포인트:
1. main.py 를 엔트리로 유지할지, engine_worker.py 단일로 통합할지 (중복 + 불일치 원인)
2. 블랙리스트 집계 단위 A/B 선택
3. 쿨다운·on_entry/on_exit 훅 도입 방식
