# Time-Decayed Trailing + Momentum Fade Exit 설계

> 작성: 2026-05-12
> 상태: 승인됨
> 관련: ADR-010 (Pure trailing), ADR-017 (BE3), ADR-018 (limit_up_exit), VI Handler, Order Confirmation Pipeline

## 1. 배경

현 baseline (PF 4.36, 248건) 청산 분포:

| reason | 건수 | 비율 |
|--------|------|------|
| forced_close | 134 | 54.0% |
| breakeven_stop | 70 | 28.2% |
| stop_loss | 25 | 10.1% |
| limit_up_exit | 15 | 6.0% |
| trailing_stop | 4 | 1.6% |

`trailing_stop`이 4건뿐인 이유: Chandelier ATR×1.0 + min 2% / max 10% 트레일 폭이 인트라데이 변동 대비 넓어서 15:10 force_close 전 트리거되지 않음. 결과적으로 수익 포지션 중 다수가 forced_close로 끝남.

**개선 가설**: 장 후반으로 갈수록 trail 폭을 좁히고(time_decay), 모멘텀이 둔화된 수익 포지션을 조기 청산(momentum_fade)하면 forced_close 비율 감소 + 수익 선확정.

## 2. 목표 / 비목표

### 목표
- forced_close 비율 54% → 40% 이하
- PF 유지 (4.36 −5% 이내, 즉 4.14 이상)
- 총 PnL 감소 없음
- 백테스트와 실거래 동일 로직 (시각 주입 방식으로 결정성 보장)

### 비목표
- force_close_time 변경 (15:10 고정)
- limit_up_exit / breakeven_stop / VI Handler / OrderTracker 로직 변경
- PaperOrderManager 변경

## 3. 설계 원칙

1. **시각 주입 통일** — `update_trailing_stop(..., now: datetime | None = None)`. 호출자가 명시 전달 (backtest=candle ts, live=datetime.now). None 시 wall-clock 폴백.
2. **min_pct 시간연동 + hard floor** — `effective_min_pct = max(atr_trail_min_pct × decay, time_decay_min_pct_floor)` (사용자 결정).
3. **마지막 phase 연장** — 15:00 이후 시각(15:10까지)은 마지막 phase의 multiplier 그대로 사용.
4. **momentum_fade는 수익 포지션 + min_hold 후에만** — 손실 포지션은 stop_loss 경로가 처리, 진입 직후 부정확 ROC 판정 방어.
5. **청산 우선순위** — limit_up_exit → stop_loss(+trailing/breakeven) → **momentum_fade(신규)** → forced_close.

## 4. 컴포넌트

### 4.1 `config.yaml` (strategy.momentum)

```yaml
strategy:
  momentum:
    # 시간연동 트레일링 — 장 후반 trail 폭 축소
    time_decay_trailing_enabled: true
    time_decay_min_pct_floor: 0.01      # 절대 하한 1.0%
    time_decay_phases:
      - until: "12:00"
        multiplier: 1.0
      - until: "13:30"
        multiplier: 0.7
      - until: "14:30"
        multiplier: 0.5
      - until: "15:00"
        multiplier: 0.3
    # 15:00 이후 (15:10 force_close까지) 마지막 phase(0.3) 자동 연장

    # 모멘텀 둔화 청산 — 수익 포지션 + 보유 15분+ 에서만
    momentum_fade_exit_enabled: true
    momentum_fade_lookback: 10          # 최근 10분봉
    momentum_fade_threshold: -0.005     # ROC ≤ −0.5% 발동
    momentum_fade_min_hold_min: 15      # 진입 후 최소 15분 보유
    momentum_fade_min_profit: 0.01      # 현재 수익률 ≥ +1%
```

### 4.2 `config/settings.py`

```python
@dataclass(frozen=True)
class TimeDecayPhase:
    until: str           # "HH:MM"
    multiplier: float
```

`TradingConfig`에 추가 필드:
- `time_decay_trailing_enabled: bool = True`
- `time_decay_min_pct_floor: float = 0.01`
- `time_decay_phases: tuple[TimeDecayPhase, ...] = ()`
- `momentum_fade_exit_enabled: bool = True`
- `momentum_fade_lookback: int = 10`
- `momentum_fade_threshold: float = -0.005`
- `momentum_fade_min_hold_min: int = 15`
- `momentum_fade_min_profit: float = 0.01`

`AppConfig.from_yaml`은 `strategy.momentum.time_decay_phases` 리스트를 tuple of `TimeDecayPhase` 로 변환.

### 4.3 `risk/risk_manager.py`

#### `update_trailing_stop` 시그니처 확장

```python
def update_trailing_stop(
    self,
    ticker: str,
    current_price: float,
    atr_pct: float | None = None,
    now: datetime | None = None,         # 신규
) -> None:
```

본문 변경 — peak 갱신 시 `_get_time_decay_multiplier(now)` 호출:

```python
def _get_time_decay_multiplier(self, now: datetime | None) -> float:
    if not getattr(self._config, "time_decay_trailing_enabled", False):
        return 1.0
    if now is None:
        now = datetime.now()
    phases = getattr(self._config, "time_decay_phases", ())
    if not phases:
        return 1.0
    current_time = now.time()
    for phase in phases:
        # phase.until "HH:MM" → time 객체 비교
        until_h, until_m = phase.until.split(":")
        until = time(int(until_h), int(until_m))
        if current_time <= until:
            return phase.multiplier
    # 모든 phase 초과 → 마지막 phase 연장
    return phases[-1].multiplier
```

trailing 계산 시:
```python
decay = self._get_time_decay_multiplier(now)
effective_multiplier = self._config.atr_trail_multiplier * decay
effective_min_pct = max(
    self._config.atr_trail_min_pct * decay,
    getattr(self._config, "time_decay_min_pct_floor", 0.01),
)
new_stop = calculate_atr_trailing_stop(
    peak_price=current_price, atr_pct=atr_pct,
    multiplier=effective_multiplier,
    min_pct=effective_min_pct,
    max_pct=self._config.atr_trail_max_pct,
)
```

#### `check_momentum_fade` 신규

```python
def check_momentum_fade(
    self,
    ticker: str,
    current_price: float,
    candle_history,                 # deque of dict {ts, open, high, low, close, volume}
    now: datetime | None = None,
) -> bool:
    """모멘텀 둔화 청산 발동 여부.

    조건 (AND):
      1. enabled=true
      2. 보유시간 >= momentum_fade_min_hold_min
      3. 현재 수익률 >= momentum_fade_min_profit
      4. ROC(close[-1]/close[-N] - 1) <= momentum_fade_threshold
    """
    if not getattr(self._config, "momentum_fade_exit_enabled", False):
        return False
    pos = self._positions.get(ticker)
    if not pos:
        return False
    if now is None:
        now = datetime.now()
    # 보유시간 가드
    hold_min = (now - pos["entry_time"]).total_seconds() / 60
    if hold_min < self._config.momentum_fade_min_hold_min:
        return False
    # 수익률 가드 (손실 포지션 미적용)
    entry = pos.get("entry_price", 0)
    if entry <= 0:
        return False
    profit_pct = (current_price - entry) / entry
    if profit_pct < self._config.momentum_fade_min_profit:
        return False
    # ROC 계산
    lookback = self._config.momentum_fade_lookback
    if candle_history is None or len(candle_history) < lookback + 1:
        return False
    closes = [c.get("close", 0) for c in list(candle_history)[-lookback - 1:]]
    if closes[0] <= 0:
        return False
    roc = (closes[-1] / closes[0]) - 1
    return roc <= self._config.momentum_fade_threshold
```

### 4.4 `gui/workers/engine_worker.py:_tick_consumer`

청산 우선순위 (현 구조 유지 + momentum_fade 추가):

```
[A] limit_up_exit (변경 없음)
[B] check_stop_loss → True면 매도 (변경 없음, time_decay는 update_trailing_stop 내부에서 자동)
[C] (신규) check_momentum_fade → True면 매도 (exit_reason="momentum_fade")
```

`update_trailing_stop` 호출 위치를 찾아 `now=datetime.now()` 인자 추가:
```python
self._risk_manager.update_trailing_stop(
    ticker, price, atr_pct=intraday_atr,
    now=datetime.now(),
)
```

`check_stop_loss` 분기 후, breakeven 등 처리 이후의 위치에 momentum_fade 블록 삽입:

```python
                # 모멘텀 둔화 청산 (수익 포지션 + 진입 15분+ 후에만)
                hist = self._candle_history.get(ticker)
                if hist and self._risk_manager.check_momentum_fade(
                    ticker, price, hist, now=datetime.now(),
                ):
                    qty = pos["remaining_qty"]
                    entry = pos["entry_price"]
                    pnl = (price - entry) * qty
                    pnl_pct = ((price / entry) - 1) * 100 if entry > 0 else 0
                    strategy_name = pos.get("strategy", "") or "unknown"
                    prefer_best = self._vi_handler.should_use_best_limit(ticker)
                    result = await self._order_manager.execute_sell_stop(
                        ticker=ticker, qty=qty, price=int(price),
                        strategy=strategy_name, pnl=pnl, pnl_pct=pnl_pct,
                        exit_reason="momentum_fade",
                        prefer_best_limit=prefer_best,
                        on_rejection=lambda tk, rt: self._vi_handler.flag_suspected(tk, f"주문 거부 (rt_cd={rt})"),
                    )
                    if result is None:
                        continue
                    is_paper = self._mode == "paper"
                    if is_paper:
                        self._risk_manager.settle_sell(ticker, price, qty)
                        if pnl >= 0:
                            self._rt_wins += 1
                        else:
                            self._rt_losses += 1
                        logger.info(
                            f"momentum_fade 실행: {ticker} {qty}주 @ {price:,} "
                            f"PnL={pnl:+,.0f}"
                        )
                        strat_info = self._active_strategies.get(ticker)
                        if strat_info:
                            strat_info["strategy"].on_exit()
                        self.signals.trade_executed.emit({
                            "time": datetime.now().strftime("%H:%M:%S"),
                            "side": "sell", "ticker": ticker,
                            "price": int(price), "qty": qty,
                            "pnl": int(pnl), "reason": "momentum_fade",
                        })
                    else:
                        self._order_tracker.submit(
                            result["order_no"], ticker, "sell", qty,
                        )
                        logger.info(
                            f"[ORDER-TRACK] {result['order_no']} SUBMIT {ticker} sell {qty} "
                            f"(momentum_fade)"
                        )
                    continue
```

### 4.5 `backtest/backtester.py`

backtester는 자체 시뮬 루프에서 candle ts 기준 시각 사용:
- `update_trailing_stop(..., now=candle_ts)`로 명시 호출
- `check_momentum_fade(..., now=candle_ts)`도 동일 위치(stop_loss 체크 후, forced_close 전)
- candle_history는 backtester의 자체 1분봉 윈도우 그대로 전달

### 4.6 청산 우선순위 표 (최종)

| 우선순위 | reason | 트리거 | 변경 |
|---------|--------|--------|------|
| 1 | limit_up_exit | 상한가 도달 | 변경 없음 |
| 2 | breakeven_stop | peak +3% 후 stop 상향에 걸림 | 변경 없음 |
| 2 | trailing_stop | trail에 걸림 (time_decay 적용) | **trail 폭 축소** |
| 2 | stop_loss | 고정 −8% | 변경 없음 |
| 3 | **momentum_fade** | 수익 +1% + 보유 15분+ + ROC ≤ −0.5% | **신규** |
| 4 | forced_close | 15:10 미청산 | 변경 없음 |

## 5. 테스트

### 5.1 `tests/test_time_decay_trailing.py` (신규 단위)
1. phases 파싱: yaml list → TradingConfig.time_decay_phases (TimeDecayPhase tuple)
2. `_get_time_decay_multiplier(11:00)` → 1.0
3. `_get_time_decay_multiplier(13:00)` → 0.7
4. `_get_time_decay_multiplier(14:00)` → 0.5
5. `_get_time_decay_multiplier(14:45)` → 0.3
6. `_get_time_decay_multiplier(15:05)` → 0.3 (마지막 phase 연장)
7. `time_decay_trailing_enabled=false` → 시각 무관 1.0
8. `phases=()` → 1.0
9. update_trailing_stop @ 14:30 → effective_min_pct = max(2% × 0.5, 1.0%) = 1.0%
10. update_trailing_stop @ 11:00 → effective_min_pct = 2.0% (기존 동작)

### 5.2 `tests/test_momentum_fade.py` (신규 단위)
11. ROC 계산 (10분봉 close 비율)
12. min_hold 미충족 (진입 후 10분) → False
13. min_profit 미충족 (현 수익률 +0.5%) → False
14. 모든 조건 충족 → True
15. enabled=false → False
16. candle_history < lookback+1 → False
17. 손실 포지션 (현재가 < entry) → False

### 5.3 회귀
- 기존 `tests/test_risk_manager.py` 모두 통과 (now 파라미터 기본값 None → 기존 호출 영향 없음)
- 전체 pytest 통과

## 6. 검증 / 측정

### 백테스트 baseline 재측정
- 41종목 / 동일 기간 (2025-04-01 ~ 2026-04-10)
- 측정 명령: `python scripts/baseline_pf_limit_up.py`
- 비교 표:
  | 지표 | 현 baseline | 신규 목표 |
  |------|-------------|-----------|
  | PF | 4.36 | ≥ 4.14 (≥ 0.95×) |
  | 총 PnL | +288,654 | ≥ 288,654 |
  | forced_close 비율 | 54.0% | ≤ 40% |
  | momentum_fade + trailing_stop 비율 | 1.6% | ≥ 15% |
- 미달 시 파라미터 튜닝 (phases multiplier / momentum_fade threshold)

### 운영 안전
- selftest 7/7
- baseline 측정 후 CLAUDE.md 갱신

## 7. 위험 / 트레이드오프

- **time_decay 14:30+ phase에서 즉발 청산**: trail 1.0%는 노이즈에 즉발 가능. hard_floor 1.0% 보호 (사용자 결정).
- **momentum_fade false positive**: ROC 둔화가 추세 전환과 다를 수 있음 (재반등 놓침). min_profit ≥ 1% 가드로 일부 방어.
- **백테스트와 실거래 시각 동기**: 호출자(`now=`) 책임. backtester는 candle_ts, live는 datetime.now. 누락 시 wall-clock 폴백 — 백테스트에서 사용 금지 (잘못된 phase 적용).
- **forced_close 비율 감소 ≠ PF 개선**: forced_close 종목 중 손실인 것도 일부 있음 — 그것을 trailing/fade로 더 일찍 청산하면 손실 확정 빨라짐. PF 영향은 백테스트로만 검증 가능.

## 8. 변경 파일 목록

신규:
- `tests/test_time_decay_trailing.py`
- `tests/test_momentum_fade.py`

수정:
- `config.yaml` (strategy.momentum 9개 신규 키)
- `config/settings.py` (TimeDecayPhase + TradingConfig 8개 필드 + from_yaml 파싱)
- `risk/risk_manager.py` (update_trailing_stop now 인자 + _get_time_decay_multiplier + check_momentum_fade)
- `gui/workers/engine_worker.py` (update_trailing_stop 호출 + check_momentum_fade 분기)
- `backtest/backtester.py` (update_trailing_stop now=ts + check_momentum_fade)
- `CLAUDE.md` (baseline 갱신)

## 9. 명시적 금지

- force_close_time 변경 금지 (15:10 고정)
- limit_up_exit / breakeven_stop / VI Handler / OrderTracker 경로 변경 금지
- min_hold 가드 없는 momentum_fade 금지
- 손실 포지션 momentum_fade 적용 금지
- time_decay_phases 하드코딩 금지 (config 외부화)
- 백테스트에서 datetime.now() 사용 금지 (candle_ts 명시 전달)
