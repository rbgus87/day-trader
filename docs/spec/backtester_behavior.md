# backtester 동작 명세

> **이 문서가 라이브 재조립의 유일한 설계 진실(source of truth).**
> 라이브 코드는 이 명세를 구현해야 하며, 명세에 없는 동작은 라이브에도 없어야 함.
>
> **조사 시점**: 2026-04-15, 커밋 `1914077` 기준
> **조사 대상**: `backtest/backtester.py`, `strategy/momentum_strategy.py`, `strategy/base_strategy.py`, `scripts/backtest_single.py`, `core/indicators.py`, `config/settings.py`, `config.yaml`
>
> 각 항목의 파일:라인 인용은 위 시점 기준이며 코드 수정 시 재조사 필요.

---

## 1. 실행 구조

### 1.1 진입점 — `scripts/backtest_single.py`

1. `AppConfig.from_yaml()`으로 config.yaml + .env 로드 (`backtest_single.py:56`)
2. `config.yaml`의 `backtest` 섹션 별도 재로드 → `BacktestConfig` 생성 (commission/tax/slippage, `backtest_single.py:59-66`)
3. `config/universe.yaml` 로드, `ticker → market` 매핑 생성 (`backtest_single.py:68-70`)
4. 각 ticker별 분봉 캔들을 DB에서 전량 로드 → `pickle.dumps` 캐시 (`backtest_single.py:92-99`)
5. `build_market_strong_by_date(db_path, ma_length)` 호출로 날짜별 코스피/코스닥 강세 맵 생성 (`backtest_single.py:102-104`)
6. `ProcessPoolExecutor(max_workers = max(2, cpu-1))` 로 ticker 단위 병렬 실행 (`backtest_single.py:106-117`)
7. 각 워커 = `simulate_one()` = 단일 ticker `Backtester.run_multi_day_cached` 실행 (`backtest_single.py:32-52`)

### 1.2 `run_multi_day` vs `run_multi_day_cached`

**공통**: 날짜별로 그룹핑 → 매일 `strategy.reset()` → `run_backtest(day_df, strategy)` 실행.

| 항목 | `run_multi_day` (L483) | `run_multi_day_cached` (L536) |
|---|---|---|
| 캔들 소스 | DB에서 `load_candles` 호출 | 사전 로드된 DataFrame 사용 |
| 시장 필터 | **없음** | 있음 (L573-578) |
| 블랙리스트 | **없음** | 있음 (L581-599) |
| 연속손실 휴식 | **없음** | 있음 (L602-614) |
| 운영 경로 | 레거시/단순 | **`backtest_single.py` 실제 사용** |

**라이브 재조립은 `run_multi_day_cached` 로직을 구현해야 함.**

### 1.3 시뮬레이션 루프 구조

- **최상위**: 날짜 단위 (`groupby("date")` at `backtester.py:568`)
- **내부**: 분봉 단위 (`for idx, row in candles.iterrows()` at `backtester.py:167`)
- **전략 리셋**: 매일 `strategy.reset()` 호출 → 일일 단위 거래 카운터/쿨다운 초기화 (`backtester.py:620`)
- **`prev_day_df`**: 전일 캔들 DataFrame을 다음 날 `_setup_strategy_day()`에 전달 (`backtester.py:617, 627, 648-651`)

### 1.4 캔들 데이터 소스

- **테이블**: `intraday_candles`
- **필터**: `tf='1m'` (`backtester.py:118`)
- **컬럼**: `ts, open, high, low, close, volume, vwap` (`backtester.py:116`)
- **정렬**: `ORDER BY ts ASC` (`backtester.py:119`)
- **timeframe**: **1분봉 단일**. 5분봉은 1분봉 5개 묶어 strategy에 부가 전달(`backtester.py:187-200`, FlowStrategy 등 전용 — 현 운영 전략인 Momentum은 미사용)

---

## 2. 진입 평가

### 2.1 진입 평가 흐름

매 분봉마다 (`backtester.py:167-226`):
1. `strategy.set_backtest_time(ts.time())` — 현재 시뮬 시각 주입 (L170-171)
2. `tick = {ticker, price=close, time, volume}` 구성 (L173-178)
3. `candles_so_far = candles.iloc[: idx+1]` — 현재까지 누적 (L180-184)
4. **포지션 없을 때만** `strategy.generate_signal(candles_so_far, tick)` 호출 (L203-204)
5. 결과가 `Signal(side="buy")` 이면 `strategy.on_entry()` + 포지션 생성

### 2.2 MomentumStrategy.generate_signal 필터 (우선순위 순서)

`strategy/momentum_strategy.py:56-120` 순서:

| 순번 | 필터 | 조건 | config 키 | 위치 |
|---|---|---|---|---|
| 1 | `can_trade()` | 포지션 없음 + `trade_count < max_trades` + 쿨다운 경과 + `BLOCK_UNTIL(09:05) ≤ now ≤ MARKET_CLOSE(15:20)` | `trading.max_trades_per_day`, `trading.cooldown_minutes`, `BaseStrategy.BLOCK_UNTIL`, `MARKET_CLOSE` (hardcoded) | `base_strategy.py:64-75`, `momentum_strategy.py:58` |
| 2 | `_check_buy_time_limit()` | `now >= buy_time_end` 이면 차단 | `strategy.momentum.buy_time_limit_enabled`, `strategy.momentum.buy_time_end` | `momentum_strategy.py:39-54, 61-63` |
| 3 | 전일 고가 존재 | `_prev_day_high > 0` | `_setup_strategy_day`에서 주입 | `momentum_strategy.py:67-68` |
| 4 | 가격 돌파 | `tick["price"] > _prev_day_high` | — | `momentum_strategy.py:71-72` |
| 5 | 캔들 존재 | `candles is not None and not empty` | — | `momentum_strategy.py:75-76` |
| 6 | 누적 거래량 | `sum(volume 오늘) >= prev_day_volume × momentum_volume_ratio` | `strategy.momentum.volume_ratio` | `momentum_strategy.py:78-81` |
| 7 | 마지막 종가 | `candles.iloc[-1]["close"] > _prev_day_high` | — | `momentum_strategy.py:84-86` |
| 8 | ADX | `adx_enabled` AND `ADX_14 >= adx_min` (캔들 `adx_length + 20` 이상 필요) | `strategy.momentum.adx_enabled`, `adx_length`, `adx_min` | `momentum_strategy.py:89-90, 122-142` |
| 9 | RVol | `rvol_enabled` AND `(recent_vol / (avg_vol × window)) >= rvol_min` | `strategy.momentum.rvol_enabled`, `rvol_window`, `rvol_min` | `momentum_strategy.py:93-94, 144-158` |
| 10 | VWAP | `vwap_enabled` AND `current_price >= VWAP × (1 + vwap_min_above)` | `strategy.momentum.vwap_enabled`, `vwap_min_above` | `momentum_strategy.py:97-98, 160-175` |

전부 통과 시 `Signal(ticker, side="buy", price=current_price, strategy="momentum")` 반환.

### 2.3 진입 시 기록 데이터

`backtester.py:216-222`:
```python
position = {
    "entry_ts": row["ts"],
    "entry_price": entry_price,           # close × (1 + slippage)
    "net_entry": net_entry,                # entry_price + entry_price × commission
    "stop_loss": stop_loss,                # strategy.get_stop_loss(entry_price)
    "tp1_price": tp1,                      # strategy.get_take_profit(entry_price)[0]
}
```

### 2.4 max_positions / 1트레이드 제한

- **`max_positions`**: **backtester 미구현** — 단일 ticker × 단일 포지션만 시뮬. `backtest_single.py`가 ticker를 ProcessPool로 병렬 실행하지만 각 워커는 독립적이며 전역 포지션 한도 없음.
- **종목당 max_trades_per_day**: `BaseStrategy._trade_count >= _max_trades` 체크 (`base_strategy.py:68-72`). `config.yaml`의 `trading.max_trades_per_day=2` 적용.
- **쿨다운**: `_last_exit_time`부터 `cooldown_minutes` 경과 전 재진입 차단 (`base_strategy.py:99-112`). `config.yaml=120분`(사실상 재진입 금지).

---

## 3. 포지션 관리

### 3.1 포지션 dict 필드 (dataclass 아님, dict)

`backtester.py`에서 추가/변경되는 키:

| 키 | 타입 | 설정 시점 | 용도 |
|---|---|---|---|
| `entry_ts` | timestamp | 진입 (L217) | 거래 기록용 |
| `entry_price` | float | 진입 (L218) | 슬리피지 반영된 진입가 |
| `net_entry` | float | 진입 (L219) | entry_price + 수수료 |
| `stop_loss` | float | 진입 (L220), TP1 시 본전 이동 (L284), 트레일링 시 상향 (L350) | 손절가 |
| `tp1_price` | float | 진입 (L221) | 1차 익절 목표가 |
| `tp1_hit` | bool | TP1 히트 시 True (L282) | TP1 체결 여부 |
| `remaining_ratio` | float | TP1 후 `1.0 - tp1_sell_ratio` (L283) | 잔여 비율 |
| `highest_price` | float | TP1 시 현재 high (L285), 이후 매 캔들 high 갱신 (L310-311) | Chandelier 트레일링용 |

### 3.2 보유 중 매 캔들 평가 (L228-412)

변수 추출: `low, high, close = row[...]` (L230-232).
평가 순서는 **4절 청산 조건** 참조.

### 3.3 트레일링 스톱 갱신 로직 (Chandelier)

`backtester.py:308-350` (TP1 히트 후에만 동작):

```python
if high > position["highest_price"]:
    position["highest_price"] = high
    new_stop = None
    if atr_trail_enabled:
        atr_pct = get_latest_atr(db_path, ticker, as_of=current_date)
        if atr_pct is not None:
            trail_pct = atr_pct * atr_trail_multiplier
            trail_pct = clamp(trail_pct, atr_trail_min_pct, atr_trail_max_pct)
            new_stop = highest_price * (1 - trail_pct)
    if new_stop is None:  # 폴백
        new_stop = highest_price * (1 - trailing_stop_pct)
    position["stop_loss"] = max(position["stop_loss"], new_stop)  # 상향만
```

- `calculate_atr_trailing_stop` (`core/indicators.py:131-152`): `peak × (1 − clamp(atr_pct × mult, min, max))`
- **최고가 갱신 없으면 트레일 계산 자체 안 함** (L310 조건)
- **stop_loss 단조증가 보장** (L350의 `max`)

### 3.4 TP1 처리

`backtester.py:260-305`:
- TP1 도달 시 `tp1_sell_ratio` (50%) 매도 기록 (exit_reason=`"tp1_hit"`)
- `position`은 유지되며 3가지 필드 변경:
  - `tp1_hit = True`
  - `remaining_ratio = 1 - tp1_sell_ratio = 0.5`
  - `stop_loss = entry_price` (본전 이동 — ATR이 아닌 원래 진입가!)
  - `highest_price = high`
- **마지막 캔들에서 TP1 동시 발생 시 즉시 `forced_close`로 나머지 청산** (L287-305)

---

## 4. 청산 조건

### 평가 순서

`backtester.py:230-412` if/elif 체인 순서대로:

1. **stop_loss** (L234-257) — 항상 최우선
2. **tp1_hit** (L260-305) — `not tp1_hit and tp1_price > 0 and high >= tp1_price`
3. **trailing_stop 또는 forced_close (TP1 히트 상태)** (L308-393)
4. **forced_close (TP1 미히트 상태)** (L396-412) — 마지막 캔들만

### 4.1 stop_loss (L234-257)

- **조건**: `low <= position["stop_loss"]`
- **exit_price (전 슬리피지)**: `position["stop_loss"]` (손절가 그대로, L236)
- **exit_price_slipped**: `exit_price × (1 - slippage)` (L238)
- **net_exit**: `exit_price_slipped × (1 − (exit_fee + tax))` (L239-240)
- **pnl**: `(net_exit - net_entry) × remaining_ratio` (L241; `remaining_ratio` 기본 1.0)
- **pnl_pct**: `(net_exit - net_entry) / net_entry` (L242)
- **exit_reason**: `"stop_loss"`

### 4.2 tp1_hit (L260-286)

- **조건**: `not position.get("tp1_hit") and position["tp1_price"] and high >= position["tp1_price"]`
- **exit_price (전 슬리피지)**: `position["tp1_price"]` (TP1 목표가 그대로, L261)
- **exit_price_slipped**: `tp1_price × (1 - slippage)` (L262)
- **net_tp1**: `tp1_slipped × (1 − (exit_fee + tax))` (L263-264)
- **tp1_ratio**: `config.tp1_sell_ratio` (0.5)
- **pnl**: `(net_tp1 - net_entry) × tp1_ratio` (L266)
- **pnl_pct**: `(net_tp1 - net_entry) / net_entry` (L267; **50% 부분 매도지만 비율은 순수익률 그대로**)
- **exit_reason**: `"tp1_hit"`
- **부수효과**: 포지션 유지, `tp1_hit=True`, `remaining_ratio=0.5`, `stop_loss=entry_price`, `highest_price=high` 설정

### 4.3 TP1+마지막 캔들 동시 발생 시 forced_close (L287-305)

- **조건**: TP1 히트 바로 직후 `idx == len(candles) - 1`
- **exit_price**: `close × (1 - slippage)` (L290; 캔들 종가)
- **pnl**: `(net_fc - net_entry) × remaining_ratio` (L293)
- **exit_reason**: `"forced_close"` (TP1 후 잔여분)

### 4.4 trailing_stop (L308-375)

- **전제**: `position.get("tp1_hit")` 이 True (L308)
- 먼저 트레일링 갱신 (§3.3), 그 다음 평가:
- **조건**: `low <= position["stop_loss"]` (갱신된 stop)
- **exit_price (전 슬리피지)**: `position["stop_loss"]` (L355)
- **exit_price_slipped**: `stop × (1 - slippage)`
- **pnl**: `(net_exit - net_entry) × remaining_ratio` (L359; 0.5)
- **exit_reason**: `"trailing_stop"`

### 4.5 forced_close — TP1 히트 상태 마지막 캔들 (L377-393)

- **조건**: TP1 히트 상태 + `idx == len(candles) - 1` + 트레일링 스톱 미히트
- **exit_price**: `close × (1 - slippage)` (L378)
- **pnl**: `(net_exit - net_entry) × remaining_ratio` (0.5)
- **exit_reason**: `"forced_close"`

### 4.6 forced_close — TP1 미히트 상태 마지막 캔들 (L396-412)

- **조건**: 포지션 보유 중 + TP1 미히트 + 손절 미히트 + `idx == len(candles) - 1`
- **exit_price**: `close × (1 - slippage)` (L397)
- **net_exit**: `exit_price_slipped × (1 − (exit_fee + tax))`
- **pnl**: `net_exit - net_entry` (L400; **전량, `remaining_ratio` 미적용**)
- **pnl_pct**: `pnl / net_entry`
- **exit_reason**: `"forced_close"`

### 4.7 exit_reason 값 전수

`"stop_loss"`, `"tp1_hit"`, `"trailing_stop"`, `"forced_close"` — 4종만 존재. 라이브에도 이 4종만 있어야 함 (1회성 `"rebuild_stop"`은 Phase 0 정리용 예외).

---

## 5. 사이징

### 5.1 현재 사이징 모델

**"1주 단위 비율 시뮬"** — 자본금/주식수 개념 자체가 없음.

증거: `backtester.py` 전체에서 `qty`, `shares`, `capital`, `position_size`, `buy_amount`, `entry_capital`, `trade_size` 키워드 **0건**.

### 5.2 PnL 계산 수식 (코드 그대로)

진입 시 (L209-211):
```python
entry_price = close × (1 + slippage)
entry_fee   = entry_price × commission
net_entry   = entry_price + entry_fee
```

청산 시 (L238-242, 262-267 등):
```python
exit_price_slipped = exit_price × (1 − slippage)
exit_fee           = exit_price_slipped × (commission + tax)
net_exit           = exit_price_slipped − exit_fee
pnl                = (net_exit − net_entry) × remaining_ratio
pnl_pct            = (net_exit − net_entry) / net_entry
```

**예외**: §4.6 "TP1 미히트 상태 forced_close"는 `pnl = net_exit − net_entry` (remaining 미적용, L400).

### 5.3 remaining_ratio 의미와 변화

- **기본값**: 1.0 (position 생성 시 키 없음 → `.get(..., 1.0)` 로 기본값 적용, L237)
- **TP1 히트 시**: `1.0 − tp1_sell_ratio = 0.5` (L283)
- **이후 청산**: trailing_stop / forced_close 모두 0.5 적용 (L352, L381)
- **예외**: §4.6 마지막 캔들 forced_close는 키 미존재 상태라 기본 1.0이나 **코드가 remaining 미사용** (L400)

### 5.4 수수료·세금 반영

- **매수**: `commission` (진입 수수료)
- **매도**: `commission + tax` (청산 수수료 + 증권거래세)
- **슬리피지**: 매수 시 가격 상향, 매도 시 하향 (비대칭)
- 값 소스: `BacktestConfig` (L82-86). `scripts/backtest_single.py`에서 `config.yaml`의 `backtest` 섹션으로 덮어씀.
- **참고**: `backtester.py:17-20` 에 `ENTRY_FEE_RATE=0.00015`, `SLIPPAGE_RATE=0.00005` 모듈 상수 존재하나 **DEPRECATED 표기**되어 실제 사용 안 됨. config.yaml 값(`slippage: 0.0003`)이 6배 큼 — **혼동 주의**.

---

## 6. 리스크 관리

### 6.1 `run_multi_day_cached` 에 구현된 방어 (일자 skip 로직)

세 방어 모두 "해당 일자 전체 매매 건너뛰기" 방식.

| 방어 | 구현 | 위치 | 로직 |
|---|---|---|---|
| **시장 필터** | ✅ | L557, 571-578 | `_ticker_market in ("kospi","kosdaq")` + 해당 시장 그날 약세 → skip. strong 값 없으면 허용(보수적) |
| **블랙리스트** | ✅ | L558-560, 581-599 | 최근 `blacklist_lookback_days`일 내 청산 거래 중 `pnl < 0` 건수가 `blacklist_loss_threshold` 이상 → skip |
| **연속손실 휴식** | ✅ | L562-563, 602-614 | 직전 날짜부터 역순으로 daily_pnl < 0 누적, `consecutive_loss_threshold` 이상 → skip |

### 6.2 backtester 미구현 기능 (라이브 재조립 시 결정 필요)

| 기능 | config 키 | backtester 상태 |
|---|---|---|
| `daily_max_loss` | `trading.daily_max_loss_enabled`, `daily_max_loss_pct` | **미구현**. config에만 존재, backtester가 읽지 않음 |
| `max_positions` | `trading.max_positions` | **미구현** (단일 ticker × 단일 포지션) |
| `reduced_position_pct` | `trading.reduced_position_pct` | **미구현** (사이징 자체가 없음) |
| `consecutive_loss_days` (포지션 축소) | `trading.consecutive_loss_days` | **미구현** (별도의 `consecutive_loss_rest`만 있음) |
| 자본금 | `trading.initial_capital` | **미구현** |
| `blacklist_days` (블랙 유지 기간) | `trading.blacklist_days` | **미구현** — backtester는 "최근 N일 내 손실 K회" 판정만 하며 블랙리스트 상태를 별도 유지하지 않음 |

### 6.3 거래 가능 시간

하드코딩 (`base_strategy.py:24-25`):
- `BLOCK_UNTIL = time(9, 5)`
- `MARKET_CLOSE = time(15, 20)`

config의 `signal_block_until`, `force_close_time`은 **strategy가 읽지 않음** — 하드코딩 값이 우선. 라이브 재조립 시 config 키로 주입되도록 수정 필요.

---

## 7. 시장 필터

### 7.1 로직 (`backtester.py:25-61`, `build_market_strong_by_date`)

1. `index_candles` 테이블에서 `index_code='001'` (KOSPI), `'101'` (KOSDAQ) 조회
2. 컬럼: `dt, close`
3. 각 날짜 `i`에 대해 직전 `ma_length` (기본 5) 일의 close 평균 = MA
4. `strong = (close > MA)`
5. 결과: `{"20260410": {"kospi": True, "kosdaq": False}, ...}`

### 7.2 적용 방식 (`backtester.py:573-578`)

```python
if market_filter_enabled and ticker_market in ("kospi", "kosdaq"):
    strong = market_strong_by_date.get(date_key)
    if strong is not None and not strong.get(ticker_market, True):
        skip_day = True  # 해당 일자 전체 매매 차단
```

- 종목 단위가 아닌 **일자 단위 전체 skip**
- `ticker_market` 은 `scripts/backtest_single.py`에서 `universe.yaml`의 `market: "kospi"/"kosdaq"` 필드로 주입 (`backtest_single.py:70, 111`)
- 데이터 없는 날짜(`strong is None`)는 허용 — 보수적 기본값

---

## 8. 데이터 처리

### 8.1 캔들 생성/집계

- **원본**: `intraday_candles` 테이블의 1분봉 (프리컴퓨티드)
- **5분봉**: 1분봉 5개씩 묶어 `on_candle_5m` 호출 (`backtester.py:187-200`). Momentum은 미사용.

### 8.2 ATR 계산

- `core/indicators.py:13-35` `calculate_atr()` → `pandas_ta.atr(high, low, close, length=14)`
- **일봉 기준** 계산 (backtester 시뮬 중엔 사전 계산된 DB 값 조회)
- **실전 사용은 `get_latest_atr(db_path, ticker, as_of_date)`**: `ticker_atr` 테이블에서 `dt <= as_of_date` 중 최신 1건 조회 → `atr_pct`를 `/100` 해서 비율(0.034 = 3.4%) 반환
- 생성 배치: `scripts/calculate_atr.py` (현재 이 스크립트가 `ticker_atr`를 갱신)

### 8.3 ADX 계산

- `momentum_strategy.py:122-142` `_check_adx()`
- `pandas_ta.adx(high, low, close, length=adx_length)`
- 최소 캔들 수: `adx_length + 20` (기본 14 + 20 = 34). 미달 시 False.
- 사용 컬럼: `ADX_{length}` (pandas_ta 규격)

### 8.4 전일 고가·거래량 결정

`backtester._setup_strategy_day`:
- `prev_day_df["high"].max()` → `prev_day_high` (`backtester.py:649`)
- `prev_day_df["volume"].sum()` → `prev_day_volume` (`backtester.py:650`)
- 첫 날은 `prev_day_df=None` 이므로 `prev_day_high = 0` 유지 → Momentum 필터 3번으로 당일 매매 불가 (`momentum_strategy.py:67-68`)

### 8.5 분봉→일봉 변환

**backtester에 분봉→일봉 변환 없음.** 일봉이 필요한 ATR은 별도 배치(`scripts/calculate_atr.py`)가 사전 계산해 DB에 저장.

---

## 9. 결과 산출

### 9.1 KPI 계산 (`backtester.py:429-477`, `calculate_kpi`)

| 지표 | 수식 |
|---|---|
| `total_trades` | `len(trades)` |
| `wins` | `sum(1 for p in pnls if p > 0)` |
| `win_rate` | `wins / total_trades` |
| `gross_profit` | `sum(p for p in pnls if p > 0)` |
| `gross_loss` | `abs(sum(p for p in pnls if p < 0))` |
| `profit_factor` | `gross_profit / gross_loss` (gross_loss=0이면 `inf`) |
| `total_pnl` | `sum(pnls)` |
| `max_drawdown` | peak-to-trough on cumulative PnL (`_calc_max_drawdown`, L667-685) |
| `sharpe_ratio` | `(mean/std) × sqrt(252)` — 거래별 (`_calc_sharpe`, L687-706) |

### 9.2 MDD 계산 (L667-685)

```python
cumulative = 0
peak = 0
max_dd = 0
for pnl in pnls:
    cumulative += pnl
    peak = max(peak, cumulative)
    max_dd = max(max_dd, peak - cumulative)
```

### 9.3 Sharpe (L687-706)

```python
mean = sum(pnls) / n
variance = sum((r - mean) ** 2 for r in pnls) / (n - 1)
std = sqrt(variance)
sharpe = (mean / std) × sqrt(252)
```

- **거래 단위**, **연간화 계수 252**
- 가격 수익률이 아닌 **원 단위 PnL** 기준 계산 — 비표준적. 사이징이 1주 단위이므로 금액 스케일이 종목별로 달라짐.

### 9.4 `backtest_single.py` 집계 (`backtest_single.py:119-156`)

워커들의 KPI를 모아서 재집계:
- `total_trades = sum(k["total_trades"])`
- `gross_profit / gross_loss` 을 trade 단위로 재계산
- `pf_above_1 = sum(1 for k if k.profit_factor > 1.0)` — 종목별 PF>1 개수
- `per_trade = total_pnl / total_trades`
- `exit_counter = Counter(t["exit_reason"])` — 청산 사유 분포

---

## 10. 파라미터 전체 목록 (config.yaml → backtester 사용)

### 10.1 `trading` 섹션

| 키 | 기본값 | config.yaml 값 | 사용 위치 | 의미 |
|---|---|---|---|---|
| `initial_capital` | 1_000_000 | 1_000_000 | — | **backtester 미사용** (사이징 없음) |
| `daily_max_loss_pct` | −0.02 | −0.015 | — | **backtester 미사용** |
| `consecutive_loss_days` | 3 | 3 | — | **backtester 미사용** (포지션 축소용) |
| `reduced_position_pct` | 0.5 | 0.5 | — | **backtester 미사용** |
| `daily_max_loss_enabled` | True | true | — | **backtester 미사용** |
| `blacklist_enabled` | True | true | `backtester.py:558` | 블랙리스트 방어 활성 |
| `blacklist_lookback_days` | 5 | 5 | `backtester.py:559, 583` | 최근 N일 손실 집계 |
| `blacklist_loss_threshold` | 3 | 3 | `backtester.py:560, 598` | 블랙 발동 손실 횟수 |
| `blacklist_days` | 7 | 7 | — | **backtester 미사용** |
| `consecutive_loss_rest_enabled` | True | true | `backtester.py:562` | 연속손실 휴식 활성 |
| `consecutive_loss_threshold` | 3 | 3 | `backtester.py:563, 613` | 연속손실 일수 |
| `consecutive_loss_rest_days` | 1 | 1 | — | **backtester 미사용** (코드는 항상 1일 휴식) |
| `tp1_pct` | 0.03 | 0.16 | `momentum_strategy.py:213` | ATR TP1 폴백값 |
| `tp1_sell_ratio` | 0.5 | 0.5 | `backtester.py:265` | TP1 시 매도 비율 |
| `trailing_stop_pct` | 0.01 | 0.005 | `backtester.py:347` | ATR 트레일 실패 시 폴백 |
| `entry_1st_ratio` | 0.55 | 0.55 | — | **backtester 미사용** (분할매수 없음) |
| `max_trades_per_day` | 1 | 2 | `momentum_strategy.py:26`, `base_strategy.py:68` | 종목당 일일 진입 한도 |
| `max_positions` | 3 | 3 | — | **backtester 미사용** |
| `screening_top_n` | 5 | 5 | — | **backtester 미사용** |
| `cooldown_minutes` | 999 | 120 | `momentum_strategy.py:27`, `base_strategy.py:99-112` | 재진입 쿨다운 |
| `signal_block_until` | "09:05" | "09:05" | — | **backtester 미사용** (BaseStrategy 하드코딩) |
| `force_close_time` | "15:10" | "15:10" | — | **backtester 미사용** (마지막 캔들=당일 마지막 1분봉) |
| `market_filter_enabled` | True | true | `backtester.py:557` | 시장 필터 활성 |
| `market_ma_length` | 5 | 5 | `backtest_single.py:103` | MA 기간 |

### 10.2 `strategy.momentum` 섹션

| 키 | 기본값 | config.yaml 값 | 사용 위치 | 의미 |
|---|---|---|---|---|
| `volume_ratio` | 2.0 | 2.0 | `momentum_strategy.py:79` | 거래량 배수 |
| `stop_loss_pct` | −0.008 | −0.030 | `momentum_strategy.py:183` | ATR 손절 폴백값 |
| `adx_enabled` | True | true | `momentum_strategy.py:89` | ADX 필터 활성 |
| `adx_length` | 14 | 14 | `momentum_strategy.py:124, 130` | ADX 기간 |
| `adx_min` | 25.0 | 20 | `momentum_strategy.py:139` | ADX 임계값 |
| `rvol_enabled` | True | false | `momentum_strategy.py:93` | RVol 필터 활성 |
| `rvol_window` | 5 | 5 | `momentum_strategy.py:146` | RVol 윈도우 |
| `rvol_min` | 3.0 | 3.0 | `momentum_strategy.py:155` | RVol 임계값 |
| `vwap_enabled` | True | false | `momentum_strategy.py:97` | VWAP 필터 활성 |
| `vwap_min_above` | 0.0 | 0.0 | `momentum_strategy.py:171` | VWAP 위 최소 % |
| `buy_time_limit_enabled` | True | true | `momentum_strategy.py:45` | 매수 종료 시각 활성 |
| `buy_time_end` | "11:30" | "12:00" | `momentum_strategy.py:49` | 매수 종료 시각 |
| `atr_stop_enabled` | True | true | `momentum_strategy.py:184` | ATR 손절 활성 |
| `atr_stop_multiplier` | 1.5 | 1.5 | `momentum_strategy.py:199` | ATR × N |
| `atr_stop_min_pct` | 0.015 | 0.015 | `momentum_strategy.py:200` | 하한 |
| `atr_stop_max_pct` | 0.080 | 0.080 | `momentum_strategy.py:201` | 상한 |
| `atr_tp_enabled` | True | true | `momentum_strategy.py:214` | ATR TP1 활성 |
| `atr_tp_multiplier` | 3.0 | 3.0 | `momentum_strategy.py:227` | ATR × N |
| `atr_tp_min_pct` | 0.03 | 0.03 | `momentum_strategy.py:228` | 하한 |
| `atr_tp_max_pct` | 0.25 | 0.25 | `momentum_strategy.py:229` | 상한 |
| `atr_trail_enabled` | True | true | `backtester.py:314` | Chandelier 트레일 활성 |
| `atr_trail_multiplier` | 2.5 | 2.5 | `backtester.py:339` | ATR × N |
| `atr_trail_min_pct` | 0.02 | 0.02 | `backtester.py:340` | 하한 |
| `atr_trail_max_pct` | 0.10 | 0.10 | `backtester.py:341` | 상한 |

### 10.3 `backtest` 섹션

| 키 | 기본값 | config.yaml 값 | 사용 위치 |
|---|---|---|---|
| `commission` | 0.00015 | 0.00015 | `backtester.py:83-84` |
| `tax` | 0.0018 | 0.0018 | `backtester.py:85` |
| `slippage` | 0.0003 | 0.0003 | `backtester.py:86` |
| `initial_capital` | 1_000_000 | 1_000_000 | **backtester 미사용** |

### 10.4 backtester가 읽지 않는 `strategy.selector`

`config.yaml:95-97` `selector.momentum_etf_threshold`: backtester는 항상 MomentumStrategy 단일이므로 미사용.

---

## 11. 청산 분포 baseline (2025-04-01 ~ 2026-04-10 기준)

`python scripts/backtest_single.py --start 2025-04-01 --end 2026-04-10` 실행 결과:

| 지표 | 값 |
|---|---|
| Universe | 60종목 |
| 거래수 | **185** |
| PF | **2.91** |
| 총 PnL | **+288,812 (1주 기준)** |
| 거래당 PnL | +1,561 |
| PF>1 종목 | 33/60 |

### 청산 분포

| reason | 건수 | 비율 |
|---|---|---|
| `forced_close` | **168** | **90.8%** |
| `stop_loss` | 14 | 7.6% |
| `tp1_hit` | 3 | 1.6% |
| `trailing_stop` | 0 | 0% |

### 이 분포가 의미하는 것

- **시스템의 alpha 소스는 "진입 선별"이며 "청산 기술"이 아님** — 185건 중 15:10 강제청산이 90.8%를 차지. 손절(7.6%)과 익절(1.6%)로 포지션이 정리되는 경우는 9.2%뿐.
- **TP1 / trailing은 사실상 비활성 상태 (발동률 1.6%)** — `atr_tp_multiplier=3.0` + `atr_tp_max_pct=25%` 가 단타 일중으로는 너무 멀음. 트레일링은 TP1 히트 후에만 작동하므로 자연스레 0건.
- **라이브 재조립 시 이 분포가 재현되어야 baseline 일치 확인 가능** — 라이브에서 `forced_close` 비율이 크게 낮아지면 (예: time_stop 추가, TP1 축소) **baseline과 다른 시스템**이 됨. 의도된 변경이 아니면 롤백해야 함.

---

## 12. 불명확 / 코드 모순

### 12.1 DEPRECATED 상수와 실제 값 불일치

`backtester.py:17-20`:
```python
SLIPPAGE_RATE: float = 0.00005    # DEPRECATED
```
vs `config.yaml:118`:
```yaml
slippage: 0.0003
```

**실제로는 `BacktestConfig`를 통해 config 값(0.0003)이 6배로 적용됨**. 모듈 상수는 사용되지 않지만 제거되지 않아 코드 리딩 혼동 유발. 재조립 시 삭제 권장.

### 12.2 `consecutive_loss_rest_days`

config에 있으나 backtester 로직은 "`threshold` 이상 → 1일 휴식 후 재시도" — **휴식 일수를 config에서 읽지 않음** (L602-614). `rest_days=1` 이 하드코딩된 상태.

### 12.3 `_last_exit_time` 쿨다운 기준 날짜

`base_strategy.py:86-91`:
```python
self._last_exit_time = datetime.combine(
    datetime.now().date(), self._backtest_time,
)
```
- **백테스트 모드에서 `datetime.now().date()` (실행 시점 오늘 날짜) + `_backtest_time` 조합**
- 매일 `strategy.reset()` 호출되어 `_last_exit_time = None` 리셋되므로 일별 경계에선 문제 없지만, **같은 날 내에서 쿨다운 비교 시 날짜가 일관되게 "오늘"이라 OK**.
- 단, 이 구현은 "백테스트 전체 기간 중 오늘 날짜"로 고정이므로 멀티데이 쿨다운 비교 시 의미 없음. 현재는 일일 리셋되므로 눈에 띄는 버그는 아님.

### 12.4 TP1 `pnl_pct` 계산 (50% 매도인데 전체 비율)

`backtester.py:267`:
```python
pnl_pct = (net_tp1 - net_entry) / net_entry   # 0.5 곱하지 않음
```
- 실제 PnL은 절반인데 `pnl_pct`는 풀 비율. 통계/지표 해석 시 주의 필요.
- 리포트(`calculate_kpi`)는 `pnl`만 사용하므로 KPI에는 영향 없음.

### 12.5 `backtester.py` 자체의 `_has_5m` 처리

`backtester.py:164, 187-200`에 FlowStrategy 용 5분봉 빌더가 남아있음. 현재 MomentumStrategy는 `on_candle_5m` 미보유이므로 `_has_5m=False` → 코드 실행 안 됨. Phase 1 정리에서 flow 전략 제거됐으므로 **dead code**. 재조립 시 제거 가능.

### 12.6 tp2 값

`MomentumStrategy.get_take_profit` 은 `(tp1, 0)` 반환 (`momentum_strategy.py:215, 231, 234`). backtester는 `tp2` 를 unpack하지만 사용 안 함 (`backtester.py:214`). 최근 커밋 `47d98ea`이 backtester tp2 분기는 제거. **strategy 시그니처의 tuple 두 번째 값 = 항상 0 = 무의미**. 재조립 시 시그니처 단순화(`-> float`) 가능.

---

## 13. backtester에 없지만 라이브에 필요할 것으로 보이는 기능 (Phase 2 입력)

### 13.1 반드시 구현해야 할 것 (백테스트와 **다르면 baseline 불일치**)

| 항목 | 백테스트 동작 | 라이브 구현 필요사항 |
|---|---|---|
| **15:10 강제 청산** | 마지막 캔들(당일 분봉 마지막)에서 종가 청산 | `force_close_time=15:10`에 시계 기반 청산 트리거 (`trading.force_close_time`) |
| **09:05 신호 차단** | `BaseStrategy.BLOCK_UNTIL` 하드코딩 | config `signal_block_until` 존중 (현재는 하드코딩 — 라이브도 같아야 함) |
| **일일 리셋** | 매일 `strategy.reset()` | 자정/장전 스케줄러로 동일 리셋 |
| **전일 고가/거래량 주입** | `_setup_strategy_day(prev_day_df)` | 장전에 전일 OHLCV 조회하여 strategy에 주입 |
| **시장 필터** | `index_candles` + MA5 | 동일 — `build_market_strong_by_date`를 날짜별 조회로 |
| **블랙리스트** | trades 테이블 대체 in-memory 계산 | `trades` DB 조회로 동일 로직 |
| **연속손실 휴식** | `daily_pnl_by_date` in-memory | `daily_pnl` DB 조회로 동일 로직 |
| **쿨다운** | `_last_exit_time` in-memory | 동일 (리스타트 시 초기화 허용) |

### 13.2 라이브 전용 결정이 필요한 항목 (backtester 무지)

| 항목 | 이슈 | 결정 필요사항 |
|---|---|---|
| **사이징** | backtester = 1주 단위 비율 | ADR-002: 1주 단위 결정됨. 자본금 기반 수량은 phase 추가 대상 |
| **`max_positions`** | backtester 무관 (티커별 독립) | 라이브는 동시 포지션 한도 필요. 어떻게 할지 결정 (ADR 필요) |
| **분할 매수 (55%+45%)** | backtester 없음 | 라이브에서 할 거면 backtester에도 넣어야 baseline 일치 — 제거 권장 |
| **`daily_max_loss` 일일 손실 한도** | backtester 무관 | 라이브에서 강제 청산? 설정만? — 결정 필요 |
| **주문 거부 / 부분 체결** | backtester = 완전 체결 가정 | 라이브 키움 API 응답 처리 필요 |
| **Slippage 모델** | backtester = 고정 비율 | 라이브는 실제 체결가. "얼마나 차이나도 허용" 기준 |
| **종목 스크리닝 (08:30)** | backtester = universe.yaml 전체 | 라이브는 screener 결과를 쓸지, universe 전체를 감시할지. 현재 코드는 universe 전체 감시 + 스크리닝은 score 업데이트용. 이 구조 유지할지 결정 |
| **WebSocket / 체결통보** | backtester 무관 | 키움 WS 재연결, 체결 확인, 실패 시 재시도 |
| **DB 기록 (`trades`, `daily_pnl`)** | backtester = in-memory만 | 모든 거래 DB 기록, 블랙리스트/휴식이 DB 조회하도록 |
| **Telegram 알림** | backtester 무관 | 진입/청산/리스크 이벤트 알림 |
| **리스크 해제 조건** | backtester = 일자 전환 시 자동 해제 | 라이브도 동일. 수동 리셋 옵션 필요? |

### 13.3 재조립 시 신중 결정 항목

| 항목 | 왜 신중해야 하는가 |
|---|---|
| `time_stop` 재도입 | 현재 baseline의 `forced_close 90.8%` 중 상당수가 time_stop으로 재분류될 수 있음. 이 경우 **PF 자체가 바뀜** — baseline 불일치 |
| TP1 / 트레일링 축소 | 현재 1.6% 발동률을 억지로 올리면 오히려 성과 저하 가능. 백테스트에서 먼저 검증 후 적용 |
| 자본 기반 사이징 | 종목별 가격 차이로 PnL 스케일이 달라져 PF 재계산 필요. 백테스트 엔진도 동시 수정해야 비교 가능 |

---

## 부록 A: 일별 실행 흐름 요약 (의사코드)

```
for date in candles.groupby('date'):
    day_df = candles_of(date)

    # 1. 방어 체크 (skip 여부 결정)
    if market_filter_enabled and weak_market(date, ticker_market):
        skip, prev_day = day_df; continue
    if blacklist_enabled and recent_loss_count(date, trades) >= threshold:
        skip, prev_day = day_df; continue
    if rest_enabled and consecutive_loss_days(date, daily_pnl) >= threshold:
        skip, prev_day = day_df; continue

    # 2. 일일 초기화
    strategy.reset()
    strategy.set_prev_day_high(prev_day_df.high.max())
    strategy.set_prev_day_volume(prev_day_df.volume.sum())

    # 3. 분봉 루프
    position = None
    for idx, candle in day_df:
        strategy.set_backtest_time(candle.time)

        if position is None:
            signal = strategy.generate_signal(candles_so_far, tick=candle)
            if signal and signal.side == "buy":
                strategy.on_entry()
                position = open_position(candle.close)
        else:
            if candle.low <= position.stop_loss:
                close_position(position, "stop_loss", at=position.stop_loss)
            elif not position.tp1_hit and candle.high >= position.tp1_price:
                partial_close(position, "tp1_hit", ratio=tp1_sell_ratio)
                position.tp1_hit = True
                position.stop_loss = position.entry_price  # 본전
                if last_candle(idx):
                    close_position(position, "forced_close", at=candle.close)
            elif position.tp1_hit:
                update_trailing_stop(position, candle.high)
                if candle.low <= position.stop_loss:
                    close_position(position, "trailing_stop", at=position.stop_loss)
                elif last_candle(idx):
                    close_position(position, "forced_close", at=candle.close)
            elif last_candle(idx):
                close_position(position, "forced_close", at=candle.close)

    # 4. 일자 PnL 기록
    daily_pnl[date] = sum(t.pnl for t in today_trades)
    prev_day_df = day_df
```

---

## 부록 B: 키 파일·라인 빠른 참조

| 기능 | 파일:라인 |
|---|---|
| 진입 평가 루프 | `backtest/backtester.py:167-226` |
| 손절 청산 | `backtest/backtester.py:234-257` |
| TP1 청산 + 상태 변경 | `backtest/backtester.py:260-305` |
| 트레일링 스톱 갱신 | `backtest/backtester.py:308-350` |
| 트레일링 청산 | `backtest/backtester.py:354-375` |
| 마지막 캔들 강제청산 | `backtest/backtester.py:377-412` |
| 시장 필터 | `backtest/backtester.py:25-61, 573-578` |
| 블랙리스트 | `backtest/backtester.py:581-599` |
| 연속손실 휴식 | `backtest/backtester.py:602-614` |
| KPI 계산 | `backtest/backtester.py:429-477` |
| Momentum 진입 필터 | `strategy/momentum_strategy.py:56-120` |
| ATR 손절/TP1 | `core/indicators.py:89-128` |
| Chandelier 트레일 | `core/indicators.py:131-152` |
| BaseStrategy 거래 가능 시간 | `strategy/base_strategy.py:24-75` |
| 백테스트 진입점 | `scripts/backtest_single.py:32-117` |
