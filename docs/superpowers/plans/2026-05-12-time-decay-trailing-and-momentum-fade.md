# Time-Decayed Trailing + Momentum Fade Exit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** forced_close 비율 54%→40% 이하로 감소. 장 후반으로 갈수록 trail 폭을 좁히는 time_decay 트레일링과, 모멘텀 둔화 시 수익 포지션을 조기 청산하는 momentum_fade 로직을 추가. 백테스트와 라이브 동일 로직.

**Architecture:** 공통 순수 함수를 `core/exit_logic.py`로 추출 (`get_time_decay_multiplier`, `compute_momentum_fade`). risk_manager (live)와 backtester가 동일 함수를 호출하여 로직 일관성 보장. 시각은 호출자가 명시 주입 (live: `datetime.now()`, backtest: candle ts). engine_worker는 1개 호출 변경 + 1개 분기 추가 (최소 침습).

**Tech Stack:** Python 3.14, dataclasses, datetime, pytest, loguru. `core/indicators.calculate_atr_trailing_stop` 기존 활용.

**Spec:** `docs/superpowers/specs/2026-05-12-time-decay-trailing-and-momentum-fade-design.md`

---

## File Structure

신규:
- `core/exit_logic.py` — 순수 함수 모듈 (`get_time_decay_multiplier`, `compute_momentum_fade`, dataclass `TimeDecayPhase`)
- `tests/test_exit_logic.py` — 단위 테스트
- `tests/test_time_decay_trailing.py` — risk_manager.update_trailing_stop 통합 테스트
- `tests/test_momentum_fade.py` — risk_manager.check_momentum_fade 통합 테스트

수정:
- `config.yaml` (strategy.momentum 9개 신규 키)
- `config/settings.py` (TimeDecayPhase import + TradingConfig 8개 필드 + from_yaml 파싱)
- `risk/risk_manager.py` (update_trailing_stop now 인자 + check_momentum_fade 신규)
- `gui/workers/engine_worker.py` (update_trailing_stop 호출에 now 추가 + check_momentum_fade 분기)
- `backtest/backtester.py` (inline 트레일링에 time_decay 적용 + momentum_fade 체크)
- `tests/test_settings.py` (회귀 1건)
- `CLAUDE.md` (Task 7 baseline 갱신)

`TimeDecayPhase` dataclass는 `core/exit_logic.py`에 정의하고 `config/settings.py`에서 import (양방향 의존 방지: settings는 exit_logic을 사용, exit_logic은 settings를 모름).

---

## Task 1: Pure Functions + Tests (core/exit_logic.py)

**Files:**
- Create: `core/exit_logic.py`
- Create: `tests/test_exit_logic.py`

### 1.1 빈 모듈 + import 가능

- [ ] **Step 1: 실패 테스트 작성**

Create `tests/test_exit_logic.py`:
```python
"""tests/test_exit_logic.py — exit_logic 순수 함수 단위 테스트."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from collections import deque

import pytest

from core.exit_logic import (
    TimeDecayPhase,
    get_time_decay_multiplier,
    compute_momentum_fade,
)


def test_import():
    """모듈/심볼 import."""
    assert TimeDecayPhase.__name__ == "TimeDecayPhase"
    assert callable(get_time_decay_multiplier)
    assert callable(compute_momentum_fade)
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_exit_logic.py::test_import -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 모듈 작성**

Create `core/exit_logic.py`:
```python
"""core/exit_logic.py — 청산 로직 순수 함수.

risk_manager(live)와 backtester가 동일하게 호출하여 로직 일관성 보장.
모든 시각은 호출자가 명시 주입 (live: datetime.now(), backtest: candle ts).

스펙: docs/superpowers/specs/2026-05-12-time-decay-trailing-and-momentum-fade-design.md
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta


@dataclass(frozen=True)
class TimeDecayPhase:
    """시간연동 트레일링 phase. config.yaml의 time_decay_phases 리스트 요소."""
    until: str           # "HH:MM" 형식
    multiplier: float


def _parse_until(until: str) -> time:
    """'HH:MM' → datetime.time. 잘못된 형식이면 ValueError."""
    parts = until.split(":")
    if len(parts) != 2:
        raise ValueError(f"잘못된 until 형식 (HH:MM 기대): {until!r}")
    return time(int(parts[0]), int(parts[1]))


def get_time_decay_multiplier(
    now: datetime,
    phases: Sequence[TimeDecayPhase],
    enabled: bool,
) -> float:
    """현재 시각에 해당하는 time_decay multiplier 반환.

    - enabled=False 또는 phases가 비면 1.0
    - now.time() ≤ phase.until 인 첫 phase의 multiplier
    - 모든 phase 초과(15:00 이후) → 마지막 phase 연장
    """
    if not enabled or not phases:
        return 1.0
    current_time = now.time()
    for phase in phases:
        until = _parse_until(phase.until)
        if current_time <= until:
            return phase.multiplier
    return phases[-1].multiplier


def compute_momentum_fade(
    entry_price: float,
    current_price: float,
    entry_time: datetime,
    candle_closes: Sequence[float],
    now: datetime,
    lookback: int,
    threshold: float,
    min_hold_min: int,
    min_profit: float,
    enabled: bool,
) -> bool:
    """모멘텀 둔화 청산 발동 여부 (순수 함수).

    조건 (AND):
      1. enabled
      2. (now - entry_time) ≥ min_hold_min
      3. (current_price - entry_price) / entry_price ≥ min_profit
      4. len(candle_closes) ≥ lookback + 1
      5. (closes[-1] / closes[-lookback-1] - 1) ≤ threshold
    """
    if not enabled:
        return False
    if entry_price <= 0 or current_price <= 0:
        return False
    # 보유시간 가드
    hold_sec = (now - entry_time).total_seconds()
    if hold_sec < min_hold_min * 60:
        return False
    # 수익률 가드 (손실 포지션 미적용)
    profit_pct = (current_price - entry_price) / entry_price
    if profit_pct < min_profit:
        return False
    # candle 부족
    if len(candle_closes) < lookback + 1:
        return False
    base_close = candle_closes[-lookback - 1]
    if base_close <= 0:
        return False
    roc = (candle_closes[-1] / base_close) - 1
    return roc <= threshold
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_exit_logic.py::test_import -v`
Expected: PASS

- [ ] **Step 5: 커밋**
```bash
git add core/exit_logic.py tests/test_exit_logic.py
git commit -m "feat: core/exit_logic.py 순수 함수 스켈레톤"
```

### 1.2 get_time_decay_multiplier 단위 테스트

- [ ] **Step 1: 실패 테스트 작성**

Append to `tests/test_exit_logic.py`:
```python
def _default_phases() -> tuple[TimeDecayPhase, ...]:
    """spec §4.1 기본 phases."""
    return (
        TimeDecayPhase(until="12:00", multiplier=1.0),
        TimeDecayPhase(until="13:30", multiplier=0.7),
        TimeDecayPhase(until="14:30", multiplier=0.5),
        TimeDecayPhase(until="15:00", multiplier=0.3),
    )


def _at(hh: int, mm: int) -> datetime:
    return datetime(2026, 5, 12, hh, mm, 0)


class TestTimeDecayMultiplier:
    def test_morning_phase_multiplier(self):
        """11:00 → 1.0 (첫 phase: until 12:00)."""
        m = get_time_decay_multiplier(_at(11, 0), _default_phases(), enabled=True)
        assert m == 1.0

    def test_early_afternoon_phase(self):
        """13:00 → 0.7 (두 번째 phase: until 13:30)."""
        m = get_time_decay_multiplier(_at(13, 0), _default_phases(), enabled=True)
        assert m == 0.7

    def test_mid_afternoon_phase(self):
        """14:00 → 0.5 (세 번째 phase: until 14:30)."""
        m = get_time_decay_multiplier(_at(14, 0), _default_phases(), enabled=True)
        assert m == 0.5

    def test_late_afternoon_phase(self):
        """14:45 → 0.3 (마지막 phase: until 15:00)."""
        m = get_time_decay_multiplier(_at(14, 45), _default_phases(), enabled=True)
        assert m == 0.3

    def test_after_last_phase_extends(self):
        """15:05 → 0.3 (마지막 phase 연장)."""
        m = get_time_decay_multiplier(_at(15, 5), _default_phases(), enabled=True)
        assert m == 0.3

    def test_boundary_exact_match(self):
        """13:30 정각 → 0.7 (≤ until 비교)."""
        m = get_time_decay_multiplier(_at(13, 30), _default_phases(), enabled=True)
        assert m == 0.7

    def test_disabled_returns_one(self):
        """enabled=False → 1.0 (시각 무관)."""
        m = get_time_decay_multiplier(_at(14, 0), _default_phases(), enabled=False)
        assert m == 1.0

    def test_empty_phases_returns_one(self):
        """phases=() → 1.0."""
        m = get_time_decay_multiplier(_at(14, 0), (), enabled=True)
        assert m == 1.0

    def test_invalid_until_raises(self):
        """잘못된 'HH:MM' 형식 → ValueError."""
        bad_phases = (TimeDecayPhase(until="13", multiplier=0.5),)
        with pytest.raises(ValueError):
            get_time_decay_multiplier(_at(14, 0), bad_phases, enabled=True)
```

- [ ] **Step 2: 통과 확인**

Run: `python -m pytest tests/test_exit_logic.py::TestTimeDecayMultiplier -v`
Expected: 9 passed

- [ ] **Step 3: 커밋**
```bash
git add tests/test_exit_logic.py
git commit -m "test: get_time_decay_multiplier 단위 테스트 9건"
```

### 1.3 compute_momentum_fade 단위 테스트

- [ ] **Step 1: 실패 테스트 작성**

Append to `tests/test_exit_logic.py`:
```python
def _fade_kwargs(**overrides):
    """기본 momentum_fade 파라미터 (spec §4.1)."""
    base = {
        "lookback": 10,
        "threshold": -0.005,
        "min_hold_min": 15,
        "min_profit": 0.01,
        "enabled": True,
    }
    base.update(overrides)
    return base


class TestMomentumFade:
    def test_all_conditions_satisfied(self):
        """모든 조건 충족 → True.

        보유 20분, 수익 +2%, ROC −0.8% (closes[0]=1000, closes[-1]=992).
        """
        entry_time = _at(10, 0)
        now = _at(10, 20)
        closes = [1000.0] + [1001.0] * 9 + [992.0]  # 11 closes, ROC=(992/1000)-1=-0.8%
        result = compute_momentum_fade(
            entry_price=1000.0,
            current_price=1020.0,  # +2%
            entry_time=entry_time,
            candle_closes=closes,
            now=now,
            **_fade_kwargs(),
        )
        assert result is True

    def test_min_hold_not_met(self):
        """보유 10분 (< 15분 min_hold) → False."""
        entry_time = _at(10, 0)
        now = _at(10, 10)
        closes = [1000.0] + [1001.0] * 9 + [992.0]
        result = compute_momentum_fade(
            entry_price=1000.0, current_price=1020.0,
            entry_time=entry_time, candle_closes=closes, now=now,
            **_fade_kwargs(),
        )
        assert result is False

    def test_min_profit_not_met(self):
        """수익 +0.5% (< 1% min_profit) → False."""
        entry_time = _at(10, 0)
        now = _at(10, 20)
        closes = [1000.0] + [1001.0] * 9 + [992.0]
        result = compute_momentum_fade(
            entry_price=1000.0, current_price=1005.0,
            entry_time=entry_time, candle_closes=closes, now=now,
            **_fade_kwargs(),
        )
        assert result is False

    def test_loss_position_not_applied(self):
        """손실 포지션 (-1%) → False."""
        entry_time = _at(10, 0)
        now = _at(10, 20)
        closes = [1000.0] + [1001.0] * 9 + [990.0]
        result = compute_momentum_fade(
            entry_price=1000.0, current_price=990.0,
            entry_time=entry_time, candle_closes=closes, now=now,
            **_fade_kwargs(),
        )
        assert result is False

    def test_roc_above_threshold(self):
        """ROC −0.3% (> threshold −0.5%) → False."""
        entry_time = _at(10, 0)
        now = _at(10, 20)
        closes = [1000.0] + [1001.0] * 9 + [997.0]  # ROC = -0.3%
        result = compute_momentum_fade(
            entry_price=1000.0, current_price=1020.0,
            entry_time=entry_time, candle_closes=closes, now=now,
            **_fade_kwargs(),
        )
        assert result is False

    def test_disabled_returns_false(self):
        """enabled=False → False."""
        entry_time = _at(10, 0)
        now = _at(10, 20)
        closes = [1000.0] + [1001.0] * 9 + [992.0]
        result = compute_momentum_fade(
            entry_price=1000.0, current_price=1020.0,
            entry_time=entry_time, candle_closes=closes, now=now,
            **_fade_kwargs(enabled=False),
        )
        assert result is False

    def test_insufficient_candles(self):
        """candle 부족 (lookback=10이나 closes 5개) → False."""
        entry_time = _at(10, 0)
        now = _at(10, 20)
        closes = [1000.0, 1001.0, 1002.0, 1001.0, 992.0]
        result = compute_momentum_fade(
            entry_price=1000.0, current_price=1020.0,
            entry_time=entry_time, candle_closes=closes, now=now,
            **_fade_kwargs(),
        )
        assert result is False

    def test_zero_entry_price(self):
        """entry_price=0 → False (방어)."""
        entry_time = _at(10, 0)
        now = _at(10, 20)
        closes = [1000.0] * 11
        result = compute_momentum_fade(
            entry_price=0.0, current_price=1020.0,
            entry_time=entry_time, candle_closes=closes, now=now,
            **_fade_kwargs(),
        )
        assert result is False
```

- [ ] **Step 2: 통과 확인**

Run: `python -m pytest tests/test_exit_logic.py -v`
Expected: 18 passed total (1 import + 9 time_decay + 8 momentum_fade)

- [ ] **Step 3: 커밋**
```bash
git add tests/test_exit_logic.py
git commit -m "test: compute_momentum_fade 단위 테스트 8건"
```

---

## Task 2: Config 외부화 (TradingConfig + yaml + from_yaml)

**Files:**
- Modify: `config.yaml` (strategy.momentum 9개 키)
- Modify: `config/settings.py` (TradingConfig 8개 필드 + from_yaml)
- Modify: `tests/test_settings.py` (회귀 1건)

### 2.1 TradingConfig 필드 + 테스트

- [ ] **Step 1: 실패 테스트 작성**

In `tests/test_settings.py`, insert new test before `test_market_calendar_2027`:
```python
def test_trading_config_time_decay_and_fade_defaults():
    """time_decay + momentum_fade 기본값."""
    from config.settings import TradingConfig
    tc = TradingConfig()
    # time_decay
    assert tc.time_decay_trailing_enabled is True
    assert tc.time_decay_min_pct_floor == 0.01
    assert isinstance(tc.time_decay_phases, tuple)
    # 기본 phases가 비어있어도 동작 — yaml로 주입되는 게 정상
    # momentum_fade
    assert tc.momentum_fade_exit_enabled is True
    assert tc.momentum_fade_lookback == 10
    assert tc.momentum_fade_threshold == -0.005
    assert tc.momentum_fade_min_hold_min == 15
    assert tc.momentum_fade_min_profit == 0.01
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_settings.py::test_trading_config_time_decay_and_fade_defaults -v`
Expected: FAIL — AttributeError

- [ ] **Step 3: TradingConfig 수정**

In `config/settings.py`, add import at the top (after existing imports):
```python
from core.exit_logic import TimeDecayPhase
```

In `TradingConfig`, locate the last field (after `order_timeout_consecutive_threshold` per Order Confirmation Pipeline). Add at the end of the dataclass body:
```python

    # 시간연동 트레일링 — 장 후반 trail 폭 축소
    # phases는 config.yaml strategy.momentum.time_decay_phases에서 주입
    time_decay_trailing_enabled: bool = True
    time_decay_min_pct_floor: float = 0.01     # 절대 하한 1.0%
    time_decay_phases: tuple[TimeDecayPhase, ...] = ()

    # 모멘텀 둔화 청산 — 수익 포지션 + 보유 15분+ 에서만
    momentum_fade_exit_enabled: bool = True
    momentum_fade_lookback: int = 10
    momentum_fade_threshold: float = -0.005
    momentum_fade_min_hold_min: int = 15
    momentum_fade_min_profit: float = 0.01
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_settings.py -v`
Expected: 전체 통과

### 2.2 config.yaml + from_yaml 와이어링

- [ ] **Step 1: config.yaml 키 추가**

In `config.yaml`, locate `strategy.momentum` section. Find the last momentum field (likely `limit_up_stop_floor_pct: 0.99`). Insert immediately after:
```yaml

    # 시간연동 트레일링 — 장 후반 trail 폭 축소
    # 마지막 phase(15:00)는 force_close(15:10)까지 연장 적용
    time_decay_trailing_enabled: true
    time_decay_min_pct_floor: 0.01      # 절대 하한 1.0% (인트라데이 노이즈 방어)
    time_decay_phases:
      - until: "12:00"
        multiplier: 1.0
      - until: "13:30"
        multiplier: 0.7
      - until: "14:30"
        multiplier: 0.5
      - until: "15:00"
        multiplier: 0.3

    # 모멘텀 둔화 청산 — 수익 포지션 + 보유 15분+ + ROC ≤ -0.5%
    momentum_fade_exit_enabled: true
    momentum_fade_lookback: 10           # 최근 10분봉
    momentum_fade_threshold: -0.005      # ROC 임계 (-0.5%)
    momentum_fade_min_hold_min: 15       # 진입 후 최소 15분 보유
    momentum_fade_min_profit: 0.01       # 현재 수익률 +1% 이상
```

- [ ] **Step 2: AppConfig.from_yaml 와이어링**

In `config/settings.py:AppConfig.from_yaml`, locate the section where `strategy.momentum` keys are extracted (search for `mom = strategy.get("momentum", {})` or similar — read existing pattern first).

Run: `grep -n 'mom\.get\|momentum_volume_ratio' config/settings.py`

Find the `TradingConfig(...)` constructor call and add extraction for new keys. The pattern likely uses `mom.get(...)`:
```python
            # time_decay (phases는 list → TimeDecayPhase tuple 변환)
            time_decay_trailing_enabled=mom.get("time_decay_trailing_enabled", True),
            time_decay_min_pct_floor=mom.get("time_decay_min_pct_floor", 0.01),
            time_decay_phases=tuple(
                TimeDecayPhase(until=p["until"], multiplier=float(p["multiplier"]))
                for p in mom.get("time_decay_phases", [])
            ),
            # momentum_fade
            momentum_fade_exit_enabled=mom.get("momentum_fade_exit_enabled", True),
            momentum_fade_lookback=mom.get("momentum_fade_lookback", 10),
            momentum_fade_threshold=mom.get("momentum_fade_threshold", -0.005),
            momentum_fade_min_hold_min=mom.get("momentum_fade_min_hold_min", 15),
            momentum_fade_min_profit=mom.get("momentum_fade_min_profit", 0.01),
```

위치는 기존 momentum_* 추출 옆. 변수명 `mom`이 다르면 실제 명칭 사용.

- [ ] **Step 3: yaml 로딩 확인**

Run: `python -c "from config.settings import AppConfig; c = AppConfig.from_yaml(); ph = c.trading.time_decay_phases; print(len(ph), ph[0].until, ph[0].multiplier, ph[-1].until, ph[-1].multiplier)"`
Expected: `4 12:00 1.0 15:00 0.3`

Run: `python -c "from config.settings import AppConfig; c = AppConfig.from_yaml(); t = c.trading; print(t.time_decay_trailing_enabled, t.momentum_fade_exit_enabled, t.momentum_fade_threshold)"`
Expected: `True True -0.005`

- [ ] **Step 4: 전체 settings 테스트 회귀**

Run: `python -m pytest tests/test_settings.py -v`
Expected: 모두 통과

- [ ] **Step 5: 커밋**
```bash
git add config.yaml config/settings.py tests/test_settings.py
git commit -m "feat: config에 time_decay_phases + momentum_fade 파라미터 추가"
```

---

## Task 3: risk_manager — update_trailing_stop + check_momentum_fade

**Files:**
- Modify: `risk/risk_manager.py`
- Create: `tests/test_time_decay_trailing.py`
- Create: `tests/test_momentum_fade.py`

### 3.1 update_trailing_stop now 파라미터 + decay 적용

- [ ] **Step 1: 실패 테스트 작성**

Create `tests/test_time_decay_trailing.py`:
```python
"""tests/test_time_decay_trailing.py — risk_manager.update_trailing_stop time_decay 통합."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from config.settings import TradingConfig
from core.exit_logic import TimeDecayPhase
from data.db_manager import DbManager
from risk.risk_manager import RiskManager


def _rm(tmp_path, **config_overrides) -> RiskManager:
    """trading_config의 일부를 오버라이드한 RiskManager.

    TradingConfig는 frozen이라 dataclasses.replace 사용.
    """
    import dataclasses
    base = TradingConfig()
    cfg = dataclasses.replace(base, **config_overrides) if config_overrides else base
    db = DbManager(str(tmp_path / "t.db"))
    asyncio.run(db.init())
    return RiskManager(trading_config=cfg, db=db, notifier=AsyncMock())


def _phases() -> tuple[TimeDecayPhase, ...]:
    return (
        TimeDecayPhase(until="12:00", multiplier=1.0),
        TimeDecayPhase(until="13:30", multiplier=0.7),
        TimeDecayPhase(until="14:30", multiplier=0.5),
        TimeDecayPhase(until="15:00", multiplier=0.3),
    )


class TestTimeDecayInTrailing:
    def test_morning_uses_full_multiplier(self, tmp_path):
        """11:00 (decay=1.0) → trail 폭은 ATR×1.0, min 2% 적용."""
        rm = _rm(
            tmp_path,
            time_decay_trailing_enabled=True,
            time_decay_phases=_phases(),
            atr_trail_enabled=True,
            atr_trail_multiplier=1.0,
            atr_trail_min_pct=0.02,
            atr_trail_max_pct=0.10,
            atr_tp_enabled=False,
        )
        rm.register_position(
            ticker="000001", entry_price=10000, qty=10, stop_loss=9200,
            status="confirmed",
        )
        # peak 갱신
        rm.update_trailing_stop(
            "000001", current_price=10500, atr_pct=0.06,
            now=datetime(2026, 5, 12, 11, 0),
        )
        pos = rm.get_position("000001")
        # 11:00 → decay=1.0. trail = ATR 6% × 1.0 = 6% → min/max 클램프 (6% 그대로)
        # new_stop = 10500 × (1 - 0.06) = 9870
        assert pos["stop_loss"] == pytest.approx(9870, abs=1.0)

    def test_late_afternoon_narrows_trail(self, tmp_path):
        """14:45 (decay=0.3) → trail = ATR 6% × 0.3 = 1.8%, min_pct = max(0.6%, 1.0%) = 1.0%.

        new_stop = 10500 × (1 - 0.018) = 10311
        """
        rm = _rm(
            tmp_path,
            time_decay_trailing_enabled=True,
            time_decay_phases=_phases(),
            time_decay_min_pct_floor=0.01,
            atr_trail_enabled=True,
            atr_trail_multiplier=1.0,
            atr_trail_min_pct=0.02,
            atr_trail_max_pct=0.10,
            atr_tp_enabled=False,
        )
        rm.register_position(
            ticker="000001", entry_price=10000, qty=10, stop_loss=9200,
            status="confirmed",
        )
        rm.update_trailing_stop(
            "000001", current_price=10500, atr_pct=0.06,
            now=datetime(2026, 5, 12, 14, 45),
        )
        pos = rm.get_position("000001")
        # decay=0.3 → effective_min = max(0.02×0.3, 0.01) = max(0.006, 0.01) = 0.01
        # trail_pct = clamp(0.06 × 0.3, 0.01, 0.10) = clamp(0.018, 0.01, 0.10) = 0.018
        # new_stop = 10500 × (1 - 0.018) = 10311
        assert pos["stop_loss"] == pytest.approx(10311, abs=1.0)

    def test_hard_floor_kicks_in(self, tmp_path):
        """ATR=2% × decay 0.3 = 0.6% → hard floor 1.0% 적용."""
        rm = _rm(
            tmp_path,
            time_decay_trailing_enabled=True,
            time_decay_phases=_phases(),
            time_decay_min_pct_floor=0.01,
            atr_trail_enabled=True,
            atr_trail_multiplier=1.0,
            atr_trail_min_pct=0.02,
            atr_trail_max_pct=0.10,
            atr_tp_enabled=False,
        )
        rm.register_position(
            ticker="000001", entry_price=10000, qty=10, stop_loss=9200,
            status="confirmed",
        )
        rm.update_trailing_stop(
            "000001", current_price=10500, atr_pct=0.02,
            now=datetime(2026, 5, 12, 14, 45),
        )
        pos = rm.get_position("000001")
        # decay=0.3 → effective_min = max(0.02×0.3, 0.01) = 0.01
        # raw_trail = 0.02 × 0.3 = 0.006 → clamped to 0.01 (hard floor)
        # new_stop = 10500 × (1 - 0.01) = 10395
        assert pos["stop_loss"] == pytest.approx(10395, abs=1.0)

    def test_disabled_preserves_legacy_behavior(self, tmp_path):
        """time_decay_trailing_enabled=False → 14:45에도 multiplier=1.0 동작."""
        rm = _rm(
            tmp_path,
            time_decay_trailing_enabled=False,
            atr_trail_enabled=True,
            atr_trail_multiplier=1.0,
            atr_trail_min_pct=0.02,
            atr_trail_max_pct=0.10,
            atr_tp_enabled=False,
        )
        rm.register_position(
            ticker="000001", entry_price=10000, qty=10, stop_loss=9200,
            status="confirmed",
        )
        rm.update_trailing_stop(
            "000001", current_price=10500, atr_pct=0.06,
            now=datetime(2026, 5, 12, 14, 45),
        )
        pos = rm.get_position("000001")
        # decay 무시 → 기존 동작: trail = 6%, new_stop = 9870
        assert pos["stop_loss"] == pytest.approx(9870, abs=1.0)

    def test_now_none_uses_wall_clock(self, tmp_path):
        """now=None → datetime.now() 사용 (live 안전 기본).

        본 테스트는 호출 자체가 예외 없이 되는지만 확인.
        """
        rm = _rm(
            tmp_path,
            time_decay_trailing_enabled=False,  # 결정성 위해 disabled
            atr_trail_enabled=True,
            atr_trail_multiplier=1.0,
            atr_trail_min_pct=0.02,
            atr_trail_max_pct=0.10,
            atr_tp_enabled=False,
        )
        rm.register_position(
            ticker="000001", entry_price=10000, qty=10, stop_loss=9200,
            status="confirmed",
        )
        # now 미전달 — 예외 없이 호출 가능해야 함
        rm.update_trailing_stop("000001", current_price=10500, atr_pct=0.06)
        assert rm.get_position("000001")["stop_loss"] >= 9200
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_time_decay_trailing.py -v`
Expected: FAIL — `update_trailing_stop() got unexpected keyword argument 'now'` 또는 동등 오류

- [ ] **Step 3: update_trailing_stop 수정**

In `risk/risk_manager.py`:

(a) Add import (with existing imports):
```python
from core.exit_logic import get_time_decay_multiplier
```

(b) Modify `update_trailing_stop` signature — add `now` keyword:
```python
    def update_trailing_stop(
        self,
        ticker: str,
        current_price: float,
        atr_pct: float | None = None,
        now: datetime | None = None,
    ) -> None:
```

(c) Inside the `if current_price > pos["highest_price"]:` block, BEFORE calling `calculate_atr_trailing_stop`, compute the decay multiplier and effective bounds:
```python
        # time_decay multiplier (1.0 if disabled or empty phases)
        decay = get_time_decay_multiplier(
            now if now is not None else datetime.now(),
            getattr(self._config, "time_decay_phases", ()),
            getattr(self._config, "time_decay_trailing_enabled", False),
        )
        effective_multiplier = self._config.atr_trail_multiplier * decay
        floor = getattr(self._config, "time_decay_min_pct_floor", 0.01)
        effective_min_pct = max(self._config.atr_trail_min_pct * decay, floor)
```

(d) Replace the existing `calculate_atr_trailing_stop(...)` call to use effective values:
```python
                    new_stop = calculate_atr_trailing_stop(
                        peak_price=current_price,
                        atr_pct=atr_pct,
                        multiplier=effective_multiplier,
                        min_pct=effective_min_pct,
                        max_pct=self._config.atr_trail_max_pct,
                    )
```

(e) In the fallback branch (`if new_stop is None:`), also apply effective_min_pct:
```python
                # ATR 미가용 폴백 — effective_min_pct로 lower bound (time_decay 일관성)
                max_pct = getattr(self._config, "atr_trail_max_pct", 0.10)
                trail_pct = max(effective_min_pct, min(max_pct, pos["trailing_pct"]))
                new_stop = current_price * (1 - trail_pct)
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_time_decay_trailing.py -v`
Expected: 5 passed

Run: `python -m pytest tests/test_risk_manager.py -v`
Expected: 기존 모두 통과 (now 파라미터 기본값 None → 기존 호출 영향 없음)

- [ ] **Step 5: 커밋**
```bash
git add risk/risk_manager.py tests/test_time_decay_trailing.py
git commit -m "feat: update_trailing_stop에 time_decay 적용 + now 인자

- now=None이면 datetime.now() 폴백 (live 안전 기본).
- decay = get_time_decay_multiplier(now, phases, enabled).
- effective_multiplier = atr_trail_multiplier × decay.
- effective_min_pct = max(atr_trail_min_pct × decay, time_decay_min_pct_floor).
- ATR 미가용 폴백 분기에도 effective_min_pct 일관 적용."
```

### 3.2 check_momentum_fade

- [ ] **Step 1: 실패 테스트 작성**

Create `tests/test_momentum_fade.py`:
```python
"""tests/test_momentum_fade.py — risk_manager.check_momentum_fade 통합."""

from __future__ import annotations

import asyncio
import dataclasses
from collections import deque
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from config.settings import TradingConfig
from data.db_manager import DbManager
from risk.risk_manager import RiskManager


def _rm(tmp_path, **overrides) -> RiskManager:
    base = TradingConfig()
    cfg = dataclasses.replace(base, **overrides) if overrides else base
    db = DbManager(str(tmp_path / "t.db"))
    asyncio.run(db.init())
    return RiskManager(trading_config=cfg, db=db, notifier=AsyncMock())


def _candles(closes: list[float]) -> deque:
    """close 리스트로 1분봉 deque 생성 (open/high/low는 close와 동일 단순화)."""
    return deque([{"close": c, "open": c, "high": c, "low": c} for c in closes])


class TestCheckMomentumFade:
    def test_all_conditions_satisfied(self, tmp_path):
        """수익+2%, 보유 20분, ROC -0.8% → True."""
        rm = _rm(
            tmp_path,
            momentum_fade_exit_enabled=True,
            momentum_fade_lookback=10,
            momentum_fade_threshold=-0.005,
            momentum_fade_min_hold_min=15,
            momentum_fade_min_profit=0.01,
        )
        rm.register_position(
            ticker="000001", entry_price=1000, qty=10, stop_loss=920,
            status="confirmed",
        )
        # entry_time을 20분 전으로 조정
        rm._positions["000001"]["entry_time"] = datetime(2026, 5, 12, 10, 0)
        hist = _candles([1000.0] + [1001.0] * 9 + [992.0])  # ROC=-0.8%
        result = rm.check_momentum_fade(
            "000001", current_price=1020,
            candle_history=hist,
            now=datetime(2026, 5, 12, 10, 20),
        )
        assert result is True

    def test_min_hold_not_met(self, tmp_path):
        """보유 10분 (< 15분) → False."""
        rm = _rm(tmp_path)
        rm.register_position(
            ticker="000001", entry_price=1000, qty=10, stop_loss=920,
            status="confirmed",
        )
        rm._positions["000001"]["entry_time"] = datetime(2026, 5, 12, 10, 0)
        hist = _candles([1000.0] + [1001.0] * 9 + [992.0])
        result = rm.check_momentum_fade(
            "000001", current_price=1020, candle_history=hist,
            now=datetime(2026, 5, 12, 10, 10),
        )
        assert result is False

    def test_loss_position_returns_false(self, tmp_path):
        """현재가 < entry → False (손실 미적용)."""
        rm = _rm(tmp_path)
        rm.register_position(
            ticker="000001", entry_price=1000, qty=10, stop_loss=920,
            status="confirmed",
        )
        rm._positions["000001"]["entry_time"] = datetime(2026, 5, 12, 10, 0)
        hist = _candles([1000.0] + [1001.0] * 9 + [992.0])
        result = rm.check_momentum_fade(
            "000001", current_price=990, candle_history=hist,
            now=datetime(2026, 5, 12, 10, 20),
        )
        assert result is False

    def test_disabled_returns_false(self, tmp_path):
        """enabled=False → False."""
        rm = _rm(tmp_path, momentum_fade_exit_enabled=False)
        rm.register_position(
            ticker="000001", entry_price=1000, qty=10, stop_loss=920,
            status="confirmed",
        )
        rm._positions["000001"]["entry_time"] = datetime(2026, 5, 12, 10, 0)
        hist = _candles([1000.0] + [1001.0] * 9 + [992.0])
        result = rm.check_momentum_fade(
            "000001", current_price=1020, candle_history=hist,
            now=datetime(2026, 5, 12, 10, 20),
        )
        assert result is False

    def test_unknown_ticker_returns_false(self, tmp_path):
        """알 수 없는 ticker → False."""
        rm = _rm(tmp_path)
        hist = _candles([1000.0] + [1001.0] * 9 + [992.0])
        result = rm.check_momentum_fade(
            "UNKNOWN", current_price=1020, candle_history=hist,
            now=datetime(2026, 5, 12, 10, 20),
        )
        assert result is False

    def test_insufficient_candles(self, tmp_path):
        """candle 5개 (< lookback+1=11) → False."""
        rm = _rm(tmp_path)
        rm.register_position(
            ticker="000001", entry_price=1000, qty=10, stop_loss=920,
            status="confirmed",
        )
        rm._positions["000001"]["entry_time"] = datetime(2026, 5, 12, 10, 0)
        hist = _candles([1000.0, 1005.0, 1010.0, 1008.0, 992.0])
        result = rm.check_momentum_fade(
            "000001", current_price=1020, candle_history=hist,
            now=datetime(2026, 5, 12, 10, 20),
        )
        assert result is False
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_momentum_fade.py -v`
Expected: FAIL — `check_momentum_fade` 미존재

- [ ] **Step 3: check_momentum_fade 신규**

In `risk/risk_manager.py`, add import:
```python
from core.exit_logic import compute_momentum_fade, get_time_decay_multiplier
```
(`get_time_decay_multiplier`는 이미 Task 3.1에서 추가됨)

Add method after `update_trailing_stop` (before any DB-related methods at bottom):
```python
    def check_momentum_fade(
        self,
        ticker: str,
        current_price: float,
        candle_history,
        now: datetime | None = None,
    ) -> bool:
        """모멘텀 둔화 청산 발동 여부.

        candle_history는 close 키를 가진 dict 객체의 deque/list.
        조건은 core.exit_logic.compute_momentum_fade 위임.
        """
        if not getattr(self._config, "momentum_fade_exit_enabled", False):
            return False
        pos = self._positions.get(ticker)
        if not pos:
            return False
        if now is None:
            now = datetime.now()
        entry_time = pos.get("entry_time")
        if entry_time is None:
            return False
        # candle_history → close 리스트 추출
        if candle_history is None:
            return False
        closes = [c.get("close", 0) for c in candle_history]
        return compute_momentum_fade(
            entry_price=pos.get("entry_price", 0),
            current_price=current_price,
            entry_time=entry_time,
            candle_closes=closes,
            now=now,
            lookback=self._config.momentum_fade_lookback,
            threshold=self._config.momentum_fade_threshold,
            min_hold_min=self._config.momentum_fade_min_hold_min,
            min_profit=self._config.momentum_fade_min_profit,
            enabled=True,  # 위에서 이미 enabled 체크
        )
```

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_momentum_fade.py -v`
Expected: 6 passed

Run: `python -m pytest tests/test_risk_manager.py -v`
Expected: 모두 통과 (기존 인터페이스 보존)

- [ ] **Step 5: 커밋**
```bash
git add risk/risk_manager.py tests/test_momentum_fade.py
git commit -m "feat: risk_manager.check_momentum_fade 추가

수익 포지션 + 보유 min_hold_min 이상 + ROC ≤ threshold → True.
손실 포지션, 미보유, candle 부족, disabled는 False.
compute_momentum_fade 순수 함수에 위임."
```

---

## Task 4: engine_worker 통합

**Files:**
- Modify: `gui/workers/engine_worker.py`

### 4.1 update_trailing_stop 호출에 now 추가

- [ ] **Step 1: 호출 위치 확인**

Run: `grep -n "update_trailing_stop" gui/workers/engine_worker.py`

기대: 1개 이상 호출. 각 호출에 `now=datetime.now()` 인자 추가.

- [ ] **Step 2: 호출 수정**

각 `self._risk_manager.update_trailing_stop(...)` 호출에 `now=datetime.now()` 추가. 예:

Before:
```python
self._risk_manager.update_trailing_stop(ticker, price, atr_pct=intra_atr)
```

After:
```python
self._risk_manager.update_trailing_stop(
    ticker, price, atr_pct=intra_atr, now=datetime.now(),
)
```

(`datetime` import가 이미 있는지 확인. 없으면 추가.)

- [ ] **Step 3: 정적 검증**

Run: `python -m py_compile gui/workers/engine_worker.py`
Run: `python -m pytest tests/ -x --tb=short 2>&1 | tail -3`
Expected: 모든 테스트 통과

- [ ] **Step 4: 커밋**
```bash
git add gui/workers/engine_worker.py
git commit -m "feat: _tick_consumer의 update_trailing_stop에 now=datetime.now() 명시"
```

### 4.2 _tick_consumer에 momentum_fade 분기 추가

- [ ] **Step 1: 삽입 위치 파악**

`_tick_consumer`에서 `check_stop_loss` 분기가 끝나고 `continue` 한 직후, 그리고 `check_tp1` 또는 다른 청산 체크 전에 momentum_fade 블록을 삽입한다.

Read 30 lines around `check_stop_loss` to find the right anchor:
Run: `grep -n "check_stop_loss\|check_tp1" gui/workers/engine_worker.py`

- [ ] **Step 2: momentum_fade 분기 삽입**

`check_stop_loss` 분기의 마지막 `continue` 다음에 다음 블록 삽입:
```python
                # 모멘텀 둔화 청산 (수익 포지션 + 보유 min_hold_min+ + ROC ≤ threshold)
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

- [ ] **Step 3: 정적 검증 + 회귀**

Run: `python -m py_compile gui/workers/engine_worker.py`
Run: `grep -n "momentum_fade" gui/workers/engine_worker.py` — expect ≥3 hits
Run: `python -m pytest tests/ -x --tb=short 2>&1 | tail -3`
Expected: 모두 통과

- [ ] **Step 4: 커밋**
```bash
git add gui/workers/engine_worker.py
git commit -m "feat: _tick_consumer에 momentum_fade 분기 추가

check_stop_loss 다음, paper_mode/real_mode 분기는 stop_loss 패턴 일치.
VI Handler prefer_best_limit + on_rejection 그대로 적용."
```

---

## Task 5: backtester 통합

**Files:**
- Modify: `backtest/backtester.py`

### 5.1 backtester trailing 로직에 time_decay 적용

backtester는 risk_manager.update_trailing_stop를 호출하지 않고 inline 트레일링 로직 (line 328-368)을 가짐. 동일한 time_decay 로직을 inline 적용.

- [ ] **Step 1: 현재 trailing 로직 위치 확인**

Run: `grep -n "calculate_atr_trailing_stop" backtest/backtester.py`

Expected: 1+ hit around line 354.

- [ ] **Step 2: trailing 호출 수정**

Find the section (read 40 lines around line 328-368). The current call passes `multiplier=self._config.atr_trail_multiplier` etc. directly. Modify to compute time_decay first using the candle's timestamp.

(a) Add import at top of `backtest/backtester.py`:
```python
from core.exit_logic import get_time_decay_multiplier, compute_momentum_fade
```

(b) Inside the trailing block (where `position["highest_price"]` is updated and `calculate_atr_trailing_stop` is called), BEFORE that call, compute decay:
```python
                        # time_decay multiplier (candle ts 기준 — backtest 결정성)
                        candle_ts = row["ts"] if hasattr(row, "__getitem__") else row.ts
                        if not isinstance(candle_ts, datetime):
                            candle_ts = pd.to_datetime(candle_ts)
                        decay = get_time_decay_multiplier(
                            candle_ts,
                            getattr(self._config, "time_decay_phases", ()),
                            getattr(self._config, "time_decay_trailing_enabled", False),
                        )
                        effective_multiplier = self._config.atr_trail_multiplier * decay
                        floor = getattr(self._config, "time_decay_min_pct_floor", 0.01)
                        effective_min_pct = max(self._config.atr_trail_min_pct * decay, floor)
```

(c) Replace the existing `calculate_atr_trailing_stop` call to use effective values:
```python
                                    new_stop = calculate_atr_trailing_stop(
                                        peak_price=position["highest_price"],
                                        atr_pct=atr_pct_val,  # 기존 변수명 그대로
                                        multiplier=effective_multiplier,
                                        min_pct=effective_min_pct,
                                        max_pct=self._config.atr_trail_max_pct,
                                    )
```

(`atr_pct_val`은 backtester의 기존 변수명 — 실제 명칭 확인 후 그대로 사용.)

(d) 폴백 분기 (atr 미가용)도 일관 적용:
```python
                            trailing_pct = effective_min_pct  # 또는 max(effective_min_pct, self._config.trailing_stop_pct)
                            new_stop = position["highest_price"] * (1 - trailing_pct)
```

(원래 `trailing_pct = self._config.trailing_stop_pct` 로직이 있다면 그 결과를 `max(effective_min_pct, trailing_pct, ...)`로 wrap.)

실제 코드를 읽어 정확한 fallback 패턴 적용.

- [ ] **Step 3: 검증 — backtester import 정상**

Run: `python -m py_compile backtest/backtester.py`

Run: `python -m pytest tests/test_backtester.py -v`
Expected: 기존 모든 테스트 통과 (행동 변경은 baseline 측정에서 확인)

- [ ] **Step 4: 커밋**
```bash
git add backtest/backtester.py
git commit -m "feat: backtester trailing에 time_decay 적용 (candle ts 기준)"
```

### 5.2 backtester에 momentum_fade 청산 추가

- [ ] **Step 1: 삽입 위치 파악**

backtester는 매 캔들마다 `if low <= position["stop_loss"]:` 분기로 stop_loss 처리. 그 후 forced_close 체크 전에 momentum_fade를 삽입.

Run: `grep -n 'low <= position\["stop_loss"\]\|forced_close' backtest/backtester.py`

- [ ] **Step 2: momentum_fade 분기 삽입**

`stop_loss` 분기의 `continue` 다음, forced_close 체크 전에 다음 블록:

```python
                # 모멘텀 둔화 청산 (수익 포지션 + 보유 min_hold_min+ + ROC ≤ threshold)
                if position is not None:
                    # candle close 리스트 (현 시점까지)
                    fade_window = self._config.momentum_fade_lookback + 1
                    if i >= fade_window:
                        recent_closes = candles["close"].iloc[i - fade_window + 1: i + 1].tolist()
                    else:
                        recent_closes = []
                    if compute_momentum_fade(
                        entry_price=position["entry_price"],
                        current_price=float(row["close"]),
                        entry_time=position["entry_time"],
                        candle_closes=recent_closes,
                        now=candle_ts,  # 위에서 추출한 candle_ts
                        lookback=self._config.momentum_fade_lookback,
                        threshold=self._config.momentum_fade_threshold,
                        min_hold_min=self._config.momentum_fade_min_hold_min,
                        min_profit=self._config.momentum_fade_min_profit,
                        enabled=getattr(self._config, "momentum_fade_exit_enabled", False),
                    ):
                        exit_price = float(row["close"])
                        exit_reason = "momentum_fade"
                        # 기존 trade 기록 패턴 그대로 (close + reset position)
                        # ... 동일한 trade close/reset 로직 사용 ...
```

**중요**: backtester의 기존 trade close 패턴(stop_loss / limit_up_exit 분기)을 그대로 따라야 함. trade record append, position reset 등. 실제 코드를 읽고 정확한 패턴 적용.

- [ ] **Step 3: 백테스트 sanity 실행**

Run: `python -m pytest tests/test_backtester.py -v`
Expected: 기존 모두 통과

Run smoke test:
`python scripts/baseline_pf_limit_up.py 2>&1 | tail -20` from `D:\project\day-trader`
Expected: 백테스트 정상 실행 — exit_reason 분포에 `momentum_fade` 등장 가능 (값은 Task 6에서 평가)

- [ ] **Step 4: 커밋**
```bash
git add backtest/backtester.py
git commit -m "feat: backtester에 momentum_fade 청산 추가 (candle ts 결정성)"
```

---

## Task 6: 최종 검증 + baseline 측정 + CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

### 6.1 전체 테스트 회귀

- [ ] **Step 1: pytest 전체**

Run: `python -m pytest tests/ -v --tb=short 2>&1 | tail -10` from `D:\project\day-trader`
Expected: 모두 통과. ~328+ tests (이전 309 + 신규 ~19: 9 time_decay + 8 momentum_fade + 5 trailing 통합 + 6 fade 통합 + 1 settings 등).

만약 실패가 있으면 STOP and report.

- [ ] **Step 2: selftest**

Run: `python selftest.py`
Expected: 7/7

### 6.2 baseline backtest 재측정

- [ ] **Step 1: baseline 측정**

Run: `python scripts/baseline_pf_limit_up.py 2>&1 | tail -25` from `D:\project\day-trader`

기록할 값:
- PF
- 총 거래 건수
- 총 PnL
- 청산 분포: forced_close / breakeven_stop / stop_loss / limit_up_exit / trailing_stop / **momentum_fade** (신규)

- [ ] **Step 2: 성공 기준 검증**

성공 기준:
- forced_close 비율 ≤ 40%
- PF ≥ 4.14 (4.36 × 0.95)
- 총 PnL ≥ 288,654 (이전 baseline)
- trailing_stop + momentum_fade 비율 ≥ 15%

미달 시:
- 파라미터 튜닝 권장 (phases multiplier 조정 / momentum_fade threshold 조정)
- 결과를 보고하고 사용자에게 추가 액션 요청

- [ ] **Step 3: 회귀 확인 — 다른 청산 경로 변경 없음**

Run: `grep -B2 -A8 'exit_reason="limit_up_exit"' gui/workers/engine_worker.py | grep -c "prefer_best_limit"`
Expected: `0` (ADR-018 가드)

Run: `grep -n "_limit_up_exit_pending.discard" gui/workers/engine_worker.py`
Expected: 2 hits (Order Confirmation Pipeline 보존)

Run: `grep -n "VIHandler\|update_from_tick\|is_vi_active" gui/workers/engine_worker.py`
Expected: 4+ hits (VI Handler 보존)

### 6.3 CLAUDE.md 갱신

- [ ] **Step 1: CLAUDE.md baseline 섹션 갱신**

In `CLAUDE.md`, find the `백테스트 결과 (baseline, ...)` section. Update the date and figures based on Task 6.2 measurement.

Replace the existing baseline block with new measurement. Keep the previous baseline lines in `이전 baseline` for history:
```markdown
## 백테스트 결과 (baseline, 2026-05-12 time_decay + momentum_fade 반영)

- **Profit Factor X.XX** (1주 가중, 41종목, Pure trailing + time_decay + momentum_fade + ...)
- 연 거래 건수 N건
- 총 PnL +N
- 거래당 PnL +N
- 청산 분포: forced_close X (X%) / breakeven_stop X / trailing_stop X / **momentum_fade X (신규)** / stop_loss X / limit_up_exit X
- **이전 baseline**
  - time_decay 이전 (2026-05-12): PF 4.36 / 248건 / forced_close 134 (54%) / trailing_stop 4 (1.6%) / momentum_fade 0
  - 거래세 0.15% 시: PF 4.56 / 248건 / +297,059
  - ADR-017 (BE3): PF 4.28 / 254건
  - ...
```

(실제 수치는 Task 6.2 측정값으로 채움.)

- [ ] **Step 2: Phase 완결 라인 추가**

In `재조립 진행 상태 — 전 Phase 완결` section, after the Order Confirmation Pipeline line, add:
```markdown
- [x] Time-Decayed Trailing + Momentum Fade Exit (2026-05-12) — forced_close 비율 X% (이전 54%), 신규 청산 경로 momentum_fade X건. PF X.XX.
```

- [ ] **Step 3: 최종 수정 날짜 갱신**

Top of `CLAUDE.md`, update the `**최종 수정**` line to today's date and reason.

### 6.4 최종 커밋

- [ ] **Step 1: CLAUDE.md 커밋**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md baseline 갱신 — time_decay + momentum_fade 적용

forced_close 54% → X% 감소. PF X.XX. 신규 momentum_fade 청산 경로 X건.
trailing_stop X건 (이전 4건)."
```

---

## Spec-Plan Coverage

| Spec 섹션 | 구현 Task |
|-----------|-----------|
| §3.1 time_decay multiplier + min_pct 동시 축소 + hard_floor | Task 1.2 + Task 3.1 |
| §3.2 시각 주입 통일 (now param) | Task 3.1 (signature), Task 4.1 (engine), Task 5.1 (backtester) |
| §3.3 마지막 phase 연장 | Task 1.2 (test_after_last_phase_extends) |
| §3.4 momentum_fade 수익 포지션 + min_hold | Task 1.3 + Task 3.2 |
| §3.5 청산 우선순위 (limit_up → stop → fade → forced) | Task 4.2 (engine), Task 5.2 (backtester) |
| §4.1 config.yaml 9개 신규 키 | Task 2.2 |
| §4.2 TimeDecayPhase + TradingConfig 8개 필드 + from_yaml | Task 2.1 + Task 2.2 |
| §4.3 update_trailing_stop now 인자 + decay | Task 3.1 |
| §4.3 check_momentum_fade | Task 3.2 |
| §4.4 engine_worker 통합 (update_trailing_stop + momentum_fade 분기) | Task 4.1 + Task 4.2 |
| §4.5 backtester 통합 (inline time_decay + momentum_fade) | Task 5.1 + Task 5.2 |
| §5.1 time_decay 단위 테스트 9건 | Task 1.2 |
| §5.2 momentum_fade 단위 테스트 8건 (compute) + 6건 (risk_manager 통합) | Task 1.3 + Task 3.2 |
| §6 baseline 재측정 + CLAUDE.md | Task 6 |
| §7 위험 / 트레이드오프 | Task 6.2 (검증), Task 1.2 + Task 3.1 (hard_floor) |
| §9 명시적 금지 | Task 6.2 회귀 검증 (limit_up_exit, VI, OrderTracker 보존 grep) |

모든 spec 섹션이 task로 매핑됨.
