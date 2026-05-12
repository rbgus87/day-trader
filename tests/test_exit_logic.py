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
