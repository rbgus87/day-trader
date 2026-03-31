# STRATEGY_PROMPTS.md — 3전략 체제 구현용 CLI 프롬프트

> 각 프롬프트를 순서대로 Claude Code CLI에 복사하여 실행합니다.
> docs/STRATEGY_REDESIGN.md를 먼저 읽은 후 실행하세요.
> 각 프롬프트 실행 후 `pytest tests/ -v`로 기존 테스트 통과를 확인하세요.

---

## Prompt A — Momentum v2 (리테스트 + 동적 손절 + VWAP 필터)

```
CLAUDE.md와 docs/STRATEGY_REDESIGN.md 섹션 2를 읽어줘.

Momentum 전략을 v2로 업그레이드한다. 4종목 백테스트에서 전 종목 PF>1.0인 유일한 전략이지만, 거래 빈도가 너무 낮고(55일에 1~20건) 리테스트 없이 첫 돌파에 진입하여 위꼬리에 물리는 문제가 있다.

### 1. strategy/momentum_strategy.py 수정

3단계 상태 머신으로 리팩터링:

STATE_WAITING = "waiting"       # 돌파 대기
STATE_RETEST = "retest"         # 리테스트 대기
STATE_CONFIRMED = "confirmed"   # 재돌파 확인 → 신호 발생

generate_signal() 로직:
1. STATE_WAITING:
   - 현재가 > 전일 고점이면 → 돌파가(breakout_price) 기록, 돌파 시각 기록
   - STATE_RETEST로 전환
2. STATE_RETEST:
   - 돌파 후 30분 초과하면 → STATE_WAITING으로 리셋 (타임아웃)
   - 현재가가 전일 고점 × (1 ± retest_band_pct) 이내로 되돌림이면 → 리테스트 저점(retest_low) 기록
   - 리테스트 후 다시 돌파가 상회 + 최신 캔들이 양봉이면 → STATE_CONFIRMED
3. STATE_CONFIRMED:
   - 매수 Signal 발생
   - STATE_WAITING으로 리셋

### 2. 거래량 필터 변경

현재: 누적 거래량 >= 전일 총거래량 × 2.0
변경: 동시간대 비교가 이상적이나, 현재 DB에 시간대별 전일 거래량이 없으므로
      배율을 2.0 → 1.5로 완화하고, config에서 설정 가능하게 유지.
      향후 시간대별 비교는 TODO로 남겨둔다.

### 3. 동적 손절

get_stop_loss() 수정:
- 기존: entry_price × (1 + momentum_stop_loss_pct) → 고정 -0.8%
- 변경: max(retest_low × (1 - 0.003), entry_price × (1 + momentum_stop_loss_pct))
  → 리테스트 저점 -0.3%와 고정 -0.8% 중 높은 값 (더 타이트한 손절)
  → retest_low를 인스턴스 변수로 유지하여 get_stop_loss()에서 참조

### 4. VWAP 방향 필터

generate_signal()에서 조건 추가:
- candles에 "vwap" 컬럼이 있고 마지막 캔들의 close > vwap일 때만 신호 허용
- vwap 데이터가 없으면(None/0) 필터 통과 (안전 폴백)
- config에서 on/off 가능: momentum.vwap_filter: true

### 5. config/settings.py 수정

TradingConfig에 추가:
  momentum_retest_band_pct: float = 0.003     # 리테스트 밴드 ±0.3%
  momentum_retest_timeout_min: int = 30        # 리테스트 타임아웃 (분)
  momentum_vwap_filter: bool = True            # VWAP 방향 필터

from_yaml()에서 strategy.momentum 섹션의 해당 값 로딩 추가.

### 6. config.yaml 수정

strategy.momentum 섹션에 추가:
  retest_band_pct: 0.003
  retest_timeout_minutes: 30
  vwap_filter: true

volume_ratio: 2.0 → 1.5로 변경.

### 7. backtester.py _setup_strategy_day() 수정

Momentum v2는 리테스트 상태를 매일 리셋해야 함.
reset()에서 상태 머신 초기화 확인.

### 8. 테스트 수정/추가

tests/test_momentum_strategy.py:
- 기존 테스트: 첫 돌파 → 즉시 신호 기대 → 이제 리테스트 후 신호이므로 수정 필요
- 추가 테스트:
  1. 돌파→리테스트→재돌파 → 신호 발생 확인
  2. 돌파→30분 초과 → 타임아웃, 신호 없음
  3. 돌파→리테스트 없이 계속 상승 → 타임아웃, 신호 없음 (위꼬리 방지)
  4. VWAP 하회 시 → 신호 차단 확인
  5. 동적 손절: retest_low 기반 계산 확인

`pytest tests/ -v` 전체 통과 확인.

커밋: `feat: Momentum v2 — 리테스트 상태머신 + 동적 손절 + VWAP 필터`
```

---

## Prompt B — Pullback v2 (조건 완화 + 5분봉 + 종목 필터)

```
CLAUDE.md와 docs/STRATEGY_REDESIGN.md 섹션 3을 읽어줘.

Pullback 전략을 v2로 업그레이드한다. 바이오(PF 1.30), 조선(PF 1.29)에서 수익이지만 조건이 너무 엄격하여 거래 빈도가 극히 낮고, 저변동 종목에서는 손실이다.

### 1. strategy/pullback_strategy.py 수정

조건 완화:
- min_gain_pct: 0.04 → 0.025 (config에서 읽기, 기본값 변경)
- MA5 → MA10 (config: ma_short=10)
- MA20 정배열 → MA10 정배열 (config: ma_long=10, 빠른 확인)
- MA_TOUCH_BAND: 0.005 → 0.01 (config: ma_touch_band=0.01)

5분봉 전환:
- generate_signal()에서 candles가 5분봉이라고 가정하고 처리
- 호출자(main.py candle_consumer)에서 5분봉 캔들만 Pullback에 전달
- 또는 generate_signal() 내부에서 candles["tf"]를 체크하여 5분봉만 처리
  → 후자가 더 안전 (전략이 자체적으로 타임프레임 선택)

종목 적합성:
- generate_signal()에 min_atr_pct 조건 추가
- 당일 캔들의 (high-low)/close 평균이 min_atr_pct 미만이면 신호 차단
- 또는 스크리닝 단계에서 이미 계산된 atr_pct를 활용 (전략에 set_atr_pct() 추가)

### 2. config/settings.py 수정

TradingConfig에 추가/변경:
  pullback_min_gain_pct: float = 0.025    # 0.04→0.025
  pullback_ma_short: int = 10             # 신규 (기존 하드코딩 5)
  pullback_ma_long: int = 10              # 신규 (기존 하드코딩 20)
  pullback_ma_touch_band: float = 0.01    # 0.005→0.01
  pullback_min_atr_pct: float = 0.025     # 신규: 종목 변동성 하한

from_yaml()에서 strategy.pullback 섹션 로딩.

### 3. config.yaml 수정

strategy.pullback 섹션:
  min_gain_pct: 0.025
  stop_loss_pct: -0.018
  ma_short: 10
  ma_long: 10
  ma_touch_band: 0.01
  min_atr_pct: 0.025

### 4. 클래스 상수 → config 파라미터화

pullback_strategy.py의 하드코딩 상수 교체:
  MA5_WINDOW = 5   → self._ma_short (config에서)
  MA20_WINDOW = 20  → self._ma_long (config에서)
  MA_TOUCH_BAND = 0.005 → self._ma_touch_band (config에서)

### 5. 테스트 수정/추가

tests/test_pullback_strategy.py:
- 기존 테스트의 기대값 수정 (MA 주기 변경, 밴드 변경)
- 추가:
  1. 완화된 조건(+2.5%, MA10)에서 신호 발생 확인
  2. ATR 미달 종목 → 신호 차단 확인
  3. MA10 정배열 확인 로직 테스트

`pytest tests/ -v` 전체 통과 확인.

커밋: `feat: Pullback v2 — 조건 완화(MA10, +2.5%, 밴드 1%) + 5분봉 + ATR 필터`
```

---

## Prompt C — FlowStrategy 신규 구현

```
CLAUDE.md와 docs/STRATEGY_REDESIGN.md 섹션 4를 읽어줘.

수급추종 전략을 신규 구현한다. 체결강도 + 거래량 급증으로 기관/외국인 수급 쏠림을 포착하는 전략이다.

### 주의: 체결강도 데이터 한계

키움 WS 틱 데이터(FID 체계)에서 매수/매도 구분이 가능한지 확인이 필요하다.
현재 kiwoom_ws.py의 _parse_tick()은 price, volume, cum_volume, change만 파싱한다.

Phase 1에서는 체결강도 대신 **거래량 급증 기반 근사 버전**으로 구현한다:
- 5분 거래량 >= 20분 평균 × 2.5배
- 가격 상승 중 (현재가 > 5분전 종가)
- VWAP 방향 필터

Phase 2에서 체결강도(매수체결/총체결) 데이터를 확보한 후 정식 적용.

### 1. 신규 파일: strategy/flow_strategy.py

BaseStrategy를 상속하는 FlowStrategy 클래스:

```python
class FlowStrategy(BaseStrategy):
    """수급추종 전략 — 거래량 급증 + 가격 상승 + VWAP 필터."""

    def __init__(self, config: TradingConfig):
        self._config = config
        self._volume_history: list[int] = []  # 5분봉 거래량 히스토리
        self._min_strength = config.flow_min_strength_pct  # Phase 2용
        self._volume_surge_ratio = config.flow_volume_surge_ratio
        self._vwap_filter = config.flow_vwap_filter
        self.configure_multi_trade(
            max_trades=config.max_trades_per_day,
            cooldown_minutes=config.cooldown_minutes,
        )
        # 시간 제한
        self.BLOCK_UNTIL = time(9, 30)   # 09:30 이후
        self.MARKET_CLOSE = time(14, 30)  # 14:30 이전

    def on_candle_5m(self, candle: dict) -> None:
        """5분봉 완성 시 거래량 히스토리 업데이트."""
        self._volume_history.append(int(candle.get("volume", 0)))
        if len(self._volume_history) > 20:
            self._volume_history = self._volume_history[-20:]

    def generate_signal(self, candles, tick) -> Signal | None:
        if not self.can_trade():
            return None
        # 최소 4개 5분봉 필요 (20분 평균)
        if len(self._volume_history) < 4:
            return None
        # 거래량 급증 체크
        avg_vol = sum(self._volume_history[-4:]) / 4
        current_vol = self._volume_history[-1] if self._volume_history else 0
        if avg_vol <= 0 or current_vol < avg_vol * self._volume_surge_ratio:
            return None
        # 가격 상승 확인
        if candles is None or len(candles) < 2:
            return None
        if candles.iloc[-1]["close"] <= candles.iloc[-2]["close"]:
            return None
        # VWAP 필터
        if self._vwap_filter and "vwap" in candles.columns:
            vwap = candles.iloc[-1].get("vwap")
            if vwap and vwap > 0 and candles.iloc[-1]["close"] <= vwap:
                return None
        # 당일 시가 대비 상승
        if candles.iloc[-1]["close"] <= candles.iloc[0]["open"]:
            return None
        # 양봉 확인
        if candles.iloc[-1]["close"] <= candles.iloc[-1]["open"]:
            return None

        return Signal(
            ticker=tick["ticker"], side="buy", price=tick["price"],
            strategy="flow",
            reason=f"거래량 급증 {current_vol/avg_vol:.1f}배 + VWAP 상회",
        )
```

### 2. config/settings.py 추가

TradingConfig에:
  flow_min_strength_pct: float = 120.0   # Phase 2용 (현재 미사용)
  flow_volume_surge_ratio: float = 2.5
  flow_stop_loss_pct: float = -0.015
  flow_trailing_stop_pct: float = 0.015  # 넓은 trailing
  flow_vwap_filter: bool = True
  flow_signal_start: str = "09:30"
  flow_signal_end: str = "14:30"

from_yaml()에서 strategy.flow 섹션 로딩.

### 3. config.yaml에 flow 섹션 추가

strategy:
  flow:
    volume_surge_ratio: 2.5
    stop_loss_pct: -0.015
    trailing_stop_pct: 0.015
    vwap_filter: true
    signal_start: "09:30"
    signal_end: "14:30"

### 4. 파이프라인 연결

main.py와 gui/workers/engine_worker.py에서:
- FlowStrategy import 추가
- strategies dict에 "flow": FlowStrategy(config.trading) 추가
- candle_consumer에서 5분봉 캔들 수신 시 flow_strategy.on_candle_5m(candle) 호출
  (candle["tf"] == "5m"일 때)

### 5. 테스트 추가

tests/test_flow_strategy.py 신규:
1. 거래량 급증 (20분 평균의 3배) + 양봉 + VWAP 상회 → 신호 발생
2. 거래량 급증 but VWAP 하회 → 신호 차단
3. 거래량 평이 → 신호 없음
4. 09:25(시간 외) → 신호 차단
5. 히스토리 4개 미만 → 신호 없음

### 6. get_stop_loss, get_take_profit 구현

get_stop_loss: entry_price × (1 + flow_stop_loss_pct)
get_take_profit: (entry_price × (1 + tp1_pct), 0)  — trailing으로 관리

`pytest tests/ -v` 전체 통과 확인.

커밋: `feat: FlowStrategy 신규 — 거래량 급증 수급추종 (Phase 1)`
```

---

## Prompt D — StrategySelector 재설계 + ORB/VWAP 정리

```
CLAUDE.md와 docs/STRATEGY_REDESIGN.md 섹션 5, 6을 읽어줘.

### 1. StrategySelector 재설계

screener/strategy_selector.py 수정:

기존 우선순위: ORB > Momentum > VWAP > Pullback
변경 우선순위: Momentum > Flow > Pullback > None

select() 로직 변경:
1. 시장 데이터 수집 (기존 get_market_snapshot 활용)
2. 전략 선택:
   - 섹터 ETF 등락 >= 1.5% → Momentum (추세장)
   - 해당 없으면 → Flow (기본, 수급 감지 대기)
   - 폴백: 후보 종목의 ATR >= 2.5% → Pullback
   - 최종 폴백: "당일 매매 없음"

ORB 관련 코드(_check_orb, orb_gap_threshold) 제거.
VWAP 관련 코드(_check_vwap, vwap_range_threshold) 제거.
Flow 관련 코드(_check_flow) 추가 — 항상 True (Flow는 장중 수급 감지이므로 사전 조건 없음).

### 2. main.py 전략 인스턴스 정리

run_screening() 내 strategies dict 변경:
```python
from strategy.momentum_strategy import MomentumStrategy
from strategy.pullback_strategy import PullbackStrategy
from strategy.flow_strategy import FlowStrategy

strategies = {
    "momentum": MomentumStrategy(config.trading),
    "flow": FlowStrategy(config.trading),
    "pullback": PullbackStrategy(config.trading),
}
```

OrbStrategy, VwapStrategy import 제거.

### 3. ORB/VWAP 파일 정리

strategy/orb_strategy.py → 파일 상단에 DEPRECATED 주석 추가, import는 제거하지 않음 (백테스트 비교용)
strategy/vwap_strategy.py → 동일

### 4. config.yaml 정리

strategy.orb 섹션: 주석 처리 (#)
strategy.vwap 섹션: 주석 처리 (#)
strategy.selector 섹션:
  momentum_etf_threshold: 1.5  (유지)
  orb_gap_threshold → 삭제
  vwap_range_threshold → 삭제

### 5. settings.py 정리

ORB/VWAP 관련 필드는 유지 (하위 호환, 백테스트), 주석에 DEPRECATED 표기:
  # DEPRECATED: ORB 전략 폐기 (v2 전략 재편)
  orb_range_start: str = "09:05"
  ...

### 6. 테스트 수정

tests/test_strategy_selector.py:
- ORB/VWAP 선택 테스트 제거 또는 skip 처리
- Momentum/Flow/Pullback 선택 로직 테스트 추가
- "전략 없음" 폴백 테스트 유지

`pytest tests/ -v` 전체 통과 확인.

커밋: `refactor: 3전략 체제 전환 — ORB/VWAP 폐기, Selector 재설계`
```

---

## Prompt E — 백테스트 재실행 + before/after 비교

```
CLAUDE.md를 읽어줘.

Momentum v2와 Pullback v2 구현이 완료됐으므로, 동일 데이터로 before/after 백테스트를 실행하여 개선 효과를 검증한다.

### 실행

4종목에 대해 run_all_strategies를 실행한다.
단, FlowStrategy는 5분봉 거래량 히스토리가 필요하므로 기존 백테스터로는 정확한 테스트가 어렵다.
Momentum v2와 Pullback v2에 집중하여 결과를 확인한다.

실행 후 before/after 비교표를 출력한다:

```
=== Before/After 비교 ===
종목: 042700 (한미반도체), 55거래일
             | v1 Trades | v1 PF  | v2 Trades | v2 PF  | 변화
Momentum     |    20     | 1.24   |    ??     | ??     | ??
Pullback     |    41     | 0.86   |    ??     | ??     | ??
```

### 판정 기준
- Momentum v2: 거래 횟수 증가 + PF 유지/개선이면 성공
- Pullback v2: 거래 횟수 변화 + PF 개선이면 성공
- 두 전략 모두 PF < 1.0으로 악화되면 → 롤백 검토

결과를 콘솔에 출력하고, 판정 코멘트를 함께 제시해줘.

커밋 없음 (검증 단계).
```

---

## 수술 완료 후 체크리스트

```
[ ] pytest tests/ -v → 전체 통과
[ ] Momentum v2: 리테스트 상태머신 동작 확인 (테스트)
[ ] Pullback v2: 완화된 조건으로 신호 빈도 증가 확인 (테스트)
[ ] FlowStrategy: 거래량 급증 신호 발생 확인 (테스트)
[ ] StrategySelector: ORB/VWAP 제거, 3전략 선택 정상 동작
[ ] config.yaml: 3전략 파라미터 반영, ORB/VWAP 주석 처리
[ ] 백테스트 before/after 비교표 작성
[ ] CLAUDE.md 전략 섹션 업데이트
```
