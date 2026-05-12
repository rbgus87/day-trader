"""tests/test_vi_handler.py — VIHandler 단위 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.vi_handler import VIHandler, VIState


def test_import_vi_handler():
    """모듈/클래스/enum이 import 가능."""
    assert VIState.NORMAL.value == "normal"
    assert VIState.STATIC_VI.value == "static_vi"
    assert VIState.SUSPECTED.value == "suspected"


def _fresh_handler(**overrides) -> VIHandler:
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
        h = _fresh_handler()
        h.update_from_tick("000001", price=10950, prev_close=10000)  # +9.50%
        assert h.get_vi_state("000001") == VIState.STATIC_VI

    def test_below_threshold_stays_normal(self):
        """+9.4% 미만 → NORMAL 유지."""
        h = _fresh_handler()
        h.update_from_tick("000001", price=10940, prev_close=10000)  # +9.40%
        assert h.get_vi_state("000001") == VIState.NORMAL

    def test_limit_up_excluded(self):
        """상한가(+30%) 도달 종목 → STATIC_VI 미발동 (limit_up_exit 보호)."""
        h = _fresh_handler()
        h.update_from_tick("000001", price=13000, prev_close=10000)  # +30.0%
        assert h.get_vi_state("000001") == VIState.NORMAL

    def test_near_limit_up_excluded(self):
        """상한가의 99% 이상 종목 → STATIC_VI 미발동 (limit_up_exit 우선)."""
        h = _fresh_handler()
        h.update_from_tick("000001", price=12870, prev_close=10000)
        assert h.get_vi_state("000001") == VIState.NORMAL

    def test_negative_threshold_activates(self):
        """−9.5% 하락 → STATIC_VI (절댓값 기준)."""
        h = _fresh_handler()
        h.update_from_tick("000001", price=9050, prev_close=10000)  # −9.50%
        assert h.get_vi_state("000001") == VIState.STATIC_VI

    def test_zero_prev_close_no_crash(self):
        """prev_close=0이면 조용히 무시."""
        h = _fresh_handler()
        h.update_from_tick("000001", price=10000, prev_close=0)
        assert h.get_vi_state("000001") == VIState.NORMAL

    def test_custom_threshold(self):
        """static_pct 외부 주입 시 임계 적용."""
        h = _fresh_handler(static_pct=0.05)
        h.update_from_tick("000001", price=10510, prev_close=10000)  # +5.10%
        assert h.get_vi_state("000001") == VIState.STATIC_VI


class TestExpiry:
    def test_static_vi_expires(self):
        """assumed_duration 경과 후 조회 → NORMAL 자동 복귀."""
        h = _fresh_handler(assumed_duration_sec=0)  # 즉시 만료
        h.update_from_tick("000001", price=10950, prev_close=10000)
        import time as _t
        _t.sleep(0.01)
        assert h.get_vi_state("000001") == VIState.NORMAL

    def test_static_vi_not_expired_yet(self):
        """assumed_duration 내 → STATIC_VI 유지."""
        h = _fresh_handler(assumed_duration_sec=60)
        h.update_from_tick("000001", price=10950, prev_close=10000)
        assert h.get_vi_state("000001") == VIState.STATIC_VI


class TestSuspected:
    def test_flag_suspected_activates(self):
        """flag_suspected → SUSPECTED 상태."""
        h = _fresh_handler()
        h.flag_suspected("000001", "rt_cd=9")
        assert h.get_vi_state("000001") == VIState.SUSPECTED

    def test_suspected_expires(self):
        """suspected_duration 경과 → NORMAL."""
        h = _fresh_handler(suspected_duration_sec=0)
        h.flag_suspected("000001", "rt_cd=9")
        import time as _t
        _t.sleep(0.01)
        assert h.get_vi_state("000001") == VIState.NORMAL

    def test_suspected_not_demoted_by_tick(self):
        """SUSPECTED 상태 종목은 update_from_tick의 STATIC_VI 추정으로 강등되지 않음."""
        h = _fresh_handler()
        h.flag_suspected("000001", "rt_cd=9")
        # +9.5% 가격 변동이 발생해도 SUSPECTED 유지 (확정 신호 보호)
        h.update_from_tick("000001", price=10950, prev_close=10000)
        assert h.get_vi_state("000001") == VIState.SUSPECTED


class TestQueries:
    def test_is_vi_active_matrix(self):
        h = _fresh_handler()
        # NORMAL: False
        assert h.is_vi_active("a") is False
        # STATIC_VI: True
        h.update_from_tick("b", price=10950, prev_close=10000)
        assert h.is_vi_active("b") is True
        # SUSPECTED: True
        h.flag_suspected("c", "test")
        assert h.is_vi_active("c") is True

    def test_should_use_best_limit_matrix(self):
        h = _fresh_handler()
        assert h.should_use_best_limit("a") is False
        h.update_from_tick("b", price=10950, prev_close=10000)
        assert h.should_use_best_limit("b") is True
        h.flag_suspected("c", "test")
        assert h.should_use_best_limit("c") is True


class TestStubs:
    def test_update_from_ws_0a_no_exception(self):
        """현재는 스텁 — 호출만으로 예외 없음."""
        h = _fresh_handler()
        h.update_from_ws_0a("000001", {"any": "payload"})
        # 상태 변동 없음 확인
        assert h.get_vi_state("000001") == VIState.NORMAL
