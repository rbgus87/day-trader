"""tests/test_exit_logic.py — exit_logic 순수 함수 단위 테스트."""

from __future__ import annotations

from datetime import datetime, time, timedelta

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

    def test_invalid_until_out_of_range_raises(self):
        """'25:00' 같은 범위 초과 값 → ValueError + until 값이 메시지에 포함."""
        bad_phases = (TimeDecayPhase(until="25:00", multiplier=0.5),)
        with pytest.raises(ValueError, match="25:00"):
            get_time_decay_multiplier(_at(14, 0), bad_phases, enabled=True)


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
        """수익+2%, 보유 20분, ROC -0.8% → True."""
        entry_time = _at(10, 0)
        now = _at(10, 20)
        closes = [1000.0] + [1001.0] * 9 + [992.0]
        result = compute_momentum_fade(
            entry_price=1000.0, current_price=1020.0,
            entry_time=entry_time, candle_closes=closes, now=now,
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
        closes = [1000.0] + [1001.0] * 9 + [997.0]
        result = compute_momentum_fade(
            entry_price=1000.0, current_price=1020.0,
            entry_time=entry_time, candle_closes=closes, now=now,
            **_fade_kwargs(),
        )
        assert result is False

    def test_disabled_returns_false(self):
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
