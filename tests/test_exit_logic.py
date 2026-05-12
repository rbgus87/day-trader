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
        m = get_time_decay_multiplier(_at(11, 0), _default_phases(), enabled=True)
        assert m == 1.0

    def test_early_afternoon_phase(self):
        m = get_time_decay_multiplier(_at(13, 0), _default_phases(), enabled=True)
        assert m == 0.7

    def test_mid_afternoon_phase(self):
        m = get_time_decay_multiplier(_at(14, 0), _default_phases(), enabled=True)
        assert m == 0.5

    def test_late_afternoon_phase(self):
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
        m = get_time_decay_multiplier(_at(14, 0), _default_phases(), enabled=False)
        assert m == 1.0

    def test_empty_phases_returns_one(self):
        m = get_time_decay_multiplier(_at(14, 0), (), enabled=True)
        assert m == 1.0

    def test_invalid_until_raises(self):
        bad_phases = (TimeDecayPhase(until="13", multiplier=0.5),)
        with pytest.raises(ValueError):
            get_time_decay_multiplier(_at(14, 0), bad_phases, enabled=True)
