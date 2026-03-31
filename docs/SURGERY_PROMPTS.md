# SURGERY_PROMPTS.md — Claude Code CLI 실행용 프롬프트

> 각 프롬프트를 순서대로 Claude Code CLI에 복사하여 실행합니다.
> 반드시 CLAUDE.md를 먼저 읽은 후 실행하세요.
> 각 프롬프트 실행 후 `pytest tests/ -v`로 기존 테스트 통과를 확인하세요.

---

## Prompt 1 — config/parameter 일관성 + initial_capital 연결

```
CLAUDE.md와 docs/SURGERY_GUIDE.md를 읽어줘. FIX-2와 FIX-3을 함께 수정한다.

### FIX-2: config/parameter 일관성

config/settings.py의 TradingConfig 기본값을 config.yaml 최적화 결과와 통일해라.

변경할 기본값:
- tp1_pct: 0.02 → 0.03
- max_trades_per_day: 5 → 3
- cooldown_minutes: 10 → 15
- orb_volume_ratio: 1.0 → 0.0  (비활성화가 최적화 결과)
- pullback_min_gain_pct: 0.03 → 0.04
- pullback_stop_loss_pct: -0.015 → -0.018
- momentum_stop_loss_pct: -0.015 → -0.008

또한 from_yaml() 160행에서 orb_volume_ratio의 fallback을 1.0에서 0.0으로 변경.

### FIX-3: initial_capital 연결

1. TradingConfig에 `initial_capital: int = 1_000_000` 필드 추가
2. from_yaml()에서 `t.get("initial_capital", 1_000_000)` 로딩 추가
3. main.py에서 risk_manager 생성 후 `risk_manager.set_daily_capital(config.trading.initial_capital)` 호출 추가
4. gui/workers/engine_worker.py에서도 동일하게 추가
5. signal_consumer의 하드코딩 fallback `10_000_000`을 `config.trading.initial_capital`로 변경하고, capital <= 0일 때 경고 로그 추가

### 검증
- TradingConfig() 기본 생성과 AppConfig.from_yaml() 로딩 값이 일치하는지 테스트 추가 (tests/test_settings.py에)
- `pytest tests/ -v` 전체 통과 확인
- 기존 전략 테스트가 변경된 기본값으로 인해 실패하면 테스트의 기대값을 수정 (config 값이 아닌 테스트 기대값이 틀린 것)

커밋: `fix: [FIX-2,3] config 기본값 최적화 결과 통일 + initial_capital 연결`
```

---

## Prompt 2 — 실시간 포지션 모니터링 파이프라인

```
CLAUDE.md와 docs/SURGERY_GUIDE.md의 FIX-1을 읽어줘.

현재 파이프라인에서 포지션 보유 중일 때 틱 수신 시 손절/TP1/트레일링 스톱을 체크하는 로직이 없다. 이걸 추가해야 한다.

### 설계 방향

main.py의 tick_consumer를 수정하여 틱을 candle_builder에 전달하는 것과 동시에 포지션 모니터링도 수행한다.

tick_consumer 수정:
```python
async def tick_consumer():
    while True:
        tick = await tick_queue.get()
        # 1. 캔들 빌더에 전달 (기존)
        await candle_builder.on_tick(tick)
        # 2. 포지션 모니터링 (신규)
        ticker = tick["ticker"]
        price = tick["price"]
        pos = risk_manager.get_position(ticker)
        if pos is None or pos["remaining_qty"] <= 0:
            continue
        # 손절 체크
        if risk_manager.check_stop_loss(ticker, price):
            await order_manager.execute_sell_stop(ticker, pos["remaining_qty"])
            risk_manager.record_pnl(...)  # pnl 계산 필요
            risk_manager.remove_position(ticker)
            continue
        # TP1 체크
        if risk_manager.check_tp1(ticker, price):
            sell_qty = int(pos["remaining_qty"] * config.trading.tp1_sell_ratio)
            await order_manager.execute_sell_tp1(ticker, int(price), pos["remaining_qty"])
            risk_manager.mark_tp1_hit(ticker, sell_qty)
            continue
        # 트레일링 스톱 갱신
        risk_manager.update_trailing_stop(ticker, price)
```

### 주의사항
- PnL 계산: (exit_price - entry_price) * qty 로 계산하여 risk_manager.record_pnl()에 전달
- 손절/TP1 매도 후 trades 테이블에 기록 (FIX-4에서 추가될 DB 기록과 연계)
- 일일 손실 한도 체크: record_pnl 후 is_trading_halted() 자동 확인
- gui/workers/engine_worker.py의 _tick_consumer()에도 동일 로직 적용

### 테스트 추가
tests/test_pipeline.py에 통합 테스트 추가:
1. mock tick (stop_loss 가격) → execute_sell_stop 호출 확인
2. mock tick (tp1 가격) → execute_sell_tp1 호출 + mark_tp1_hit 확인
3. mock tick (고점 갱신) → trailing_stop 갱신 확인
4. 일일 손실 한도 도달 → is_trading_halted() True 확인

`pytest tests/ -v` 전체 통과 확인.

커밋: `fix: [FIX-1] 실시간 포지션 모니터링 추가 (손절/TP1/트레일링)`
```

---

## Prompt 3 — OrderManager DB 기록 + strategy 필드 수정

```
CLAUDE.md와 docs/SURGERY_GUIDE.md의 FIX-4, FIX-6을 읽어줘.

### FIX-4: OrderManager에 trades DB 기록 추가

core/order_manager.py 수정:
1. execute_buy() 성공 시 trades 테이블에 INSERT (side='buy')
2. _send_order() 성공 시 trades 테이블에 INSERT (매도 건)
3. 생성자에 `db: DbManager`가 이미 있으므로 활용

PaperOrderManager의 DB 기록 패턴을 참고하되, strategy 파라미터를 올바르게 전달받도록 한다.

### FIX-6: 양쪽 OrderManager의 strategy 필드 수정

PaperOrderManager:
- execute_buy(): strategy='paper' 하드코딩 → strategy 파라미터로 받기
- _simulate_order(): 동일하게 strategy 파라미터 추가

OrderManager:
- execute_buy(): strategy 파라미터 추가
- _send_order(): strategy 파라미터 추가

호출자 수정:
- main.py signal_consumer: order_manager.execute_buy(...) 호출 시 active_strategy의 전략명 전달
  예: strategy=signal.strategy (Signal dataclass에 이미 strategy 필드 있음)
- gui/workers/engine_worker.py: 동일
- force_close에서 execute_sell_force_close 호출 시 strategy는 DB에서 해당 포지션의 전략을 읽거나, risk_manager에 전략명 저장

### 하위 호환
strategy 파라미터에 기본값 `strategy: str = "unknown"` 설정하여 기존 호출이 깨지지 않도록.

### 테스트
- test_order_manager.py: execute_buy 후 mock db.execute 호출 검증
- test_paper_order_manager.py: strategy='paper' 대신 전달된 전략명 기록 확인
- `pytest tests/ -v` 전체 통과

커밋: `fix: [FIX-4,6] OrderManager DB 기록 추가 + strategy 필드 정상화`
```

---

## Prompt 4 — 백테스트 비용 모델 config.yaml 연동

```
CLAUDE.md와 docs/SURGERY_GUIDE.md의 FIX-5를 읽어줘.

### 문제
backtester.py의 하드코딩 비용 상수와 config.yaml backtest 섹션의 값이 불일치.
특히 슬리피지가 6배 차이 (0.005% vs 0.03%).

### 수정

1. config/settings.py에 BacktestConfig dataclass 추가:
```python
@dataclass(frozen=True)
class BacktestConfig:
    commission: float = 0.00015   # 매수/매도 각 0.015%
    tax: float = 0.0018           # 증권거래세 0.18%
    slippage: float = 0.0003      # 슬리피지 0.03%
    initial_capital: int = 1_000_000
```

2. AppConfig에 backtest: BacktestConfig 필드 추가

3. from_yaml()에서 config.yaml backtest 섹션 로딩:
```python
bt = cfg.get("backtest", {})
backtest = BacktestConfig(
    commission=bt.get("commission", 0.00015),
    tax=bt.get("tax", 0.0018),
    slippage=bt.get("slippage", 0.0003),
    initial_capital=bt.get("initial_capital", 1_000_000),
)
```

4. backtester.py 수정:
- 모듈 레벨 상수 위에 "# DEPRECATED: BacktestConfig 사용 권장" 주석
- 생성자에서 commission/tax/slippage를 None 받으면 BacktestConfig에서 로드
- 기존 호출자(run_all_strategies.py, optimizer.py 등)가 깨지지 않도록 하위 호환 유지

### 검증
- 수정 전/후 동일 데이터로 KPI 비교 (슬리피지 6배 차이이므로 결과 달라야 정상)
- `pytest tests/test_backtester.py -v` 통과

커밋: `fix: [FIX-5] 백테스트 비용 모델 config.yaml 연동`
```

---

## Prompt 5 — 백테스터 분할매도 + selector 임계값

```
CLAUDE.md와 docs/SURGERY_GUIDE.md의 FIX-7, FIX-8을 읽어줘.

### FIX-7: 백테스터 TP1 분할매도 시뮬레이션

backtester.py의 run_backtest() 수정:

현재 position dict에 추가 필드:
- tp1_hit: bool (TP1 히트 여부)
- original_qty: 1.0 (비율 기반)
- remaining_ratio: 1.0

청산 조건 수정:
```
현재: TP1 도달 → 전량 청산, exit_reason="tp1"
변경:
  TP1 도달 → 50% 청산, trade 기록 (exit_reason="tp1")
         → 손절선을 진입가로 이동 (position["stop_loss"] = entry_price)
         → tp1_hit = True, remaining_ratio = 0.5
  이후: 트레일링 스톱 계산 (고점 대비 -trailing_stop_pct%)
       → trailing 또는 forced_close로 나머지 50% 청산
```

PnL 계산:
- TP1 분할 매도: (tp1_price - entry_price) * 0.5 - 비용
- 나머지 청산: (exit_price - entry_price) * 0.5 - 비용
- 하나의 진입에 대해 최대 2건의 trade 기록 발생

### FIX-8: StrategySelector 임계값 통일

strategy_selector.py의 DEFAULT 상수를 config.yaml 값으로 변경:
- DEFAULT_ORB_GAP_THRESHOLD: 0.5 → 0.8
- DEFAULT_MOMENTUM_ETF_THRESHOLD: 1.5 → 2.0
- DEFAULT_VWAP_RANGE_THRESHOLD: 0.5 → 0.8

### 검증
- FIX-7: 백테스트에서 TP1 히트 시 2건 trade 기록 확인
- FIX-7: 전량 청산 대비 분할매도 결과 비교 (수익이 달라야 정상)
- FIX-8: test_strategy_selector.py의 임계값 기대값 수정
- `pytest tests/ -v` 전체 통과

커밋: `fix: [FIX-7,8] 백테스터 분할매도 시뮬레이션 + selector 임계값 통일`
```

---

## Prompt 6 — MEDIUM 이슈 일괄 수정

```
CLAUDE.md와 docs/SURGERY_GUIDE.md의 FIX-9~13을 읽어줘. 한번에 수정한다.

### FIX-9: CandleBuilder ts 포맷에 날짜 포함

data/candle_builder.py 38행:
현재: `"ts": f"{time_str[:2]}:{time_str[2:4]}:00"`
변경: `"ts": f"{datetime.now().strftime('%Y-%m-%d')}T{time_str[:2]}:{time_str[2:4]}:00"`

datetime import 추가 필요. 백테스트 모드에서는 외부에서 날짜를 주입할 수 있도록 set_date() 메서드 추가 고려.

### FIX-10: market_calendar 2027년 공휴일 추가

utils/market_calendar.py에 KR_HOLIDAYS_2027 추가.
향후 유지보수를 위해 두 dict를 합치는 `KR_HOLIDAYS = KR_HOLIDAYS_2026 | KR_HOLIDAYS_2027` 패턴.
또는 holidays 패키지 도입 검토 (requirements.txt에 추가 필요).

### FIX-11: force_close private dict 접근 제거

risk/risk_manager.py에 추가:
```python
def get_open_positions(self) -> dict[str, dict]:
    """보유 중인 포지션 목록 반환 (읽기 전용 복사본)."""
    return {k: {**v} for k, v in self._positions.items() if v.get("remaining_qty", 0) > 0}
```

main.py force_close()와 gui/workers/engine_worker.py에서 `risk_manager._positions` → `risk_manager.get_open_positions()` 교체.

### FIX-12: DB 백업 메커니즘

main.py 스케줄러에 추가:
```python
async def backup_db():
    import shutil
    from datetime import datetime
    backup_name = f"daytrader_backup_{datetime.now():%Y%m%d}.db"
    shutil.copy2(config.db_path, f"backups/{backup_name}")
    # 7일 이상 된 백업 삭제
    ...

scheduler.add_job(backup_db, "cron", hour=15, minute=35)
```

backups/ 디렉토리 자동 생성 + .gitignore에 추가.

### FIX-13: 수급 데이터 미구현 경고

screener/candidate_collector.py _collect_single() 메서드에:
최초 1회 경고 로그 추가 (매 종목마다가 아닌 수집 시작 시 1회):
```python
logger.warning("수급 데이터(기관/외국인 순매수) 미구현 — 스크리닝 수급 필터 비활성 상태")
```

### 검증
- FIX-9: candle_builder 테스트에서 ts가 ISO8601 형식인지 확인
- FIX-10: is_trading_day(date(2027, 1, 1)) → False 확인
- FIX-11: force_close가 public 메서드 사용하는지 확인
- `pytest tests/ -v` 전체 통과

커밋: `fix: [FIX-9~13] CandleBuilder 날짜 포함, market_calendar 확장, DB 백업, 기타`
```

---

## 수술 완료 후 체크리스트

```
[ ] pytest tests/ -v → 전체 통과
[ ] config.yaml 값과 settings.py 기본값 일치 확인
[ ] paper_mode=true로 시스템 기동 → 스크리닝 → 전략 선택 정상 동작
[ ] 틱 수신 시 손절 체크 로그 출력 확인
[ ] trades 테이블에 strategy 필드가 실제 전략명으로 기록되는지 확인
[ ] risk_manager.available_capital이 config 값(1,000,000)과 일치
[ ] 백테스트 실행 시 슬리피지 0.03% 적용 확인
[ ] CLAUDE.md 버그 목록에서 수정된 항목 체크 처리
```
