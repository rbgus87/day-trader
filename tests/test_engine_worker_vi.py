"""tests/test_engine_worker_vi.py — engine_worker x VIHandler 통합 시나리오.

EngineWorker 전체 부팅을 피하기 위해 VIHandler 단독으로 통합 지점의 시나리오를
검증한다. 실제 통합은 engine_worker.py의 코드 수정으로 보장.
"""

from __future__ import annotations

from core.vi_handler import VIHandler


class TestTickConsumerIntegration:
    def test_update_from_tick_uses_prev_close_cache(self):
        """_prev_close[ticker] 값이 vi_handler에 전달되면 VI 추정 발동."""
        h = VIHandler(static_pct=0.095, assumed_duration_sec=60)
        prev_close_cache = {"000001": 10000.0}
        prev = prev_close_cache.get("000001")
        if prev:
            h.update_from_tick("000001", price=10960, prev_close=prev)
        assert h.is_vi_active("000001") is True

    def test_missing_prev_close_silent_skip(self):
        """prev_close 캐시 미스 시 vi_handler 호출 자체를 건너뜀."""
        h = VIHandler()
        prev_close_cache: dict[str, float] = {}
        prev = prev_close_cache.get("000001")
        if prev:
            h.update_from_tick("000001", price=10960, prev_close=prev)
        assert h.is_vi_active("000001") is False


class TestSignalConsumerIntegration:
    def test_buy_blocked_when_vi_active(self):
        """VI 활성 종목에 매수 신호 → vi_handler.is_vi_active() == True → 차단."""
        h = VIHandler()
        h.flag_suspected("000001", "test")
        assert h.is_vi_active("000001") is True

    def test_buy_proceeds_when_normal(self):
        h = VIHandler()
        assert h.is_vi_active("000001") is False


class TestForceCloseIntegration:
    def test_force_close_uses_prefer_best_limit_when_vi(self):
        """VI 의심 종목 forced_close 시 prefer_best_limit=True 전달."""
        h = VIHandler()
        h.flag_suspected("000001", "rt_cd=9")
        assert h.should_use_best_limit("000001") is True

    def test_force_close_market_when_normal(self):
        h = VIHandler()
        assert h.should_use_best_limit("000001") is False
