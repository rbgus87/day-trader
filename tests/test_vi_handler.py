"""tests/test_vi_handler.py — VIHandler 단위 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest


def test_import_vi_handler():
    """모듈/클래스/enum이 import 가능."""
    from core.vi_handler import VIHandler, VIState
    assert VIState.NORMAL.value == "normal"
    assert VIState.STATIC_VI.value == "static_vi"
    assert VIState.SUSPECTED.value == "suspected"


def _fresh_handler(**overrides) -> "VIHandler":
    from core.vi_handler import VIHandler
    defaults = {
        "static_pct": 0.095,
        "assumed_duration_sec": 150,
        "suspected_duration_sec": 60,
    }
    defaults.update(overrides)
    return VIHandler(**defaults)


class TestUpdateFromTick:
    def test_above_threshold_activates_static_vi(self):
        """+9.5% 도달 → STATIC_VI 추정."""
        from core.vi_handler import VIState
        h = _fresh_handler()
        h.update_from_tick("000001", price=10950, prev_close=10000)  # +9.50%
        assert h.get_vi_state("000001") == VIState.STATIC_VI

    def test_below_threshold_stays_normal(self):
        """+9.4% 미만 → NORMAL 유지."""
        from core.vi_handler import VIState
        h = _fresh_handler()
        h.update_from_tick("000001", price=10940, prev_close=10000)  # +9.40%
        assert h.get_vi_state("000001") == VIState.NORMAL

    def test_limit_up_excluded(self):
        """상한가(+30%) 도달 종목 → STATIC_VI 미발동 (limit_up_exit 보호)."""
        from core.vi_handler import VIState
        h = _fresh_handler()
        h.update_from_tick("000001", price=13000, prev_close=10000)  # +30.0%
        assert h.get_vi_state("000001") == VIState.NORMAL

    def test_near_limit_up_excluded(self):
        """상한가의 99% 이상 종목 → STATIC_VI 미발동 (limit_up_exit 우선)."""
        from core.vi_handler import VIState
        h = _fresh_handler()
        h.update_from_tick("000001", price=12870, prev_close=10000)
        assert h.get_vi_state("000001") == VIState.NORMAL

    def test_negative_threshold_activates(self):
        """−9.5% 하락 → STATIC_VI (절댓값 기준)."""
        from core.vi_handler import VIState
        h = _fresh_handler()
        h.update_from_tick("000001", price=9050, prev_close=10000)  # −9.50%
        assert h.get_vi_state("000001") == VIState.STATIC_VI

    def test_zero_prev_close_no_crash(self):
        """prev_close=0이면 조용히 무시."""
        from core.vi_handler import VIState
        h = _fresh_handler()
        h.update_from_tick("000001", price=10000, prev_close=0)
        assert h.get_vi_state("000001") == VIState.NORMAL

    def test_custom_threshold(self):
        """static_pct 외부 주입 시 임계 적용."""
        from core.vi_handler import VIState
        h = _fresh_handler(static_pct=0.05)
        h.update_from_tick("000001", price=10510, prev_close=10000)  # +5.10%
        assert h.get_vi_state("000001") == VIState.STATIC_VI
