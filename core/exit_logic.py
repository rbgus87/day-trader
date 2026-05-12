"""core/exit_logic.py — 청산 로직 순수 함수.

risk_manager(live)와 backtester가 동일하게 호출하여 로직 일관성 보장.
모든 시각은 호출자가 명시 주입 (live: datetime.now(), backtest: candle ts).

스펙: docs/superpowers/specs/2026-05-12-time-decay-trailing-and-momentum-fade-design.md
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time


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
    try:
        return time(int(parts[0]), int(parts[1]))
    except ValueError as exc:
        raise ValueError(f"잘못된 until 값 {until!r}: {exc}") from exc


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
    hold_sec = (now - entry_time).total_seconds()
    if hold_sec < min_hold_min * 60:
        return False
    profit_pct = (current_price - entry_price) / entry_price
    if profit_pct < min_profit:
        return False
    if len(candle_closes) < lookback + 1:
        return False
    base_close = candle_closes[-lookback - 1]
    if base_close <= 0:
        return False
    roc = (candle_closes[-1] / base_close) - 1
    return roc <= threshold
