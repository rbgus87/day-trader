"""tests/test_shadow_tracker.py — ShadowTracker 단위 테스트."""
from __future__ import annotations

from datetime import datetime

import pytest

from core.shadow_tracker import ShadowPosition, ShadowTracker


# ── ShadowPosition 단위 ──────────────────────────────────────────────────────

class TestShadowPosition:
    def _pos(self, signal_price: float = 10_000, stop_loss_pct: float = 0.08) -> ShadowPosition:
        return ShadowPosition(
            ticker="000001",
            signal_price=signal_price,
            signal_time=datetime(2026, 1, 1, 9, 30),
            reason="market_filter",
            peak_price=signal_price,
            current_price=signal_price,
            stop_loss_pct=stop_loss_pct,
        )

    def test_update_raises_peak(self):
        pos = self._pos(10_000)
        pos.update(11_000)
        assert pos.peak_price == 11_000
        assert pos.current_price == 11_000

    def test_update_does_not_lower_peak(self):
        pos = self._pos(10_000)
        pos.update(11_000)
        pos.update(9_500)
        assert pos.peak_price == 11_000
        assert pos.current_price == 9_500

    def test_stop_loss_triggers(self):
        pos = self._pos(10_000, stop_loss_pct=0.08)
        pos.update(9_199)  # 10000 * 0.92 = 9200 — 9199 < 9200
        assert pos.stopped_out is True
        assert pos.current_price == pytest.approx(9_200.0)

    def test_stop_loss_boundary_not_triggered(self):
        pos = self._pos(10_000, stop_loss_pct=0.08)
        pos.update(9_201)  # 손절가(9200) 초과 → 발동 안 됨
        assert pos.stopped_out is False

    def test_update_ignored_after_stop_out(self):
        pos = self._pos(10_000)
        pos.update(9_000)  # stop out
        assert pos.stopped_out is True
        frozen_current = pos.current_price
        pos.update(8_000)  # 이후 갱신 무시
        assert pos.current_price == frozen_current

    def test_realistic_pnl_pct_profit(self):
        pos = self._pos(10_000)
        pos.update(11_000)
        assert pos.realistic_pnl_pct == pytest.approx(0.10)

    def test_realistic_pnl_pct_loss(self):
        pos = self._pos(10_000)
        pos.update(9_500)
        assert pos.realistic_pnl_pct == pytest.approx(-0.05)

    def test_realistic_pnl_pct_stopped_out(self):
        pos = self._pos(10_000, stop_loss_pct=0.08)
        pos.update(9_000)
        assert pos.realistic_pnl_pct == pytest.approx(-0.08)

    def test_peak_pnl_pct(self):
        pos = self._pos(10_000)
        pos.update(12_000)
        pos.update(10_500)
        assert pos.peak_pnl_pct == pytest.approx(0.20)

    def test_zero_signal_price_guard(self):
        pos = ShadowPosition(
            ticker="X", signal_price=0, signal_time=datetime.now(),
            reason="market_filter",
        )
        assert pos.realistic_pnl_pct == 0.0
        assert pos.peak_pnl_pct == 0.0


# ── ShadowTracker ────────────────────────────────────────────────────────────

class TestShadowTracker:
    def _tracker(self) -> ShadowTracker:
        return ShadowTracker(stop_loss_pct=0.08)

    def test_on_blocked_creates_position(self):
        t = self._tracker()
        t.on_blocked("005930", 70_000, datetime.now(), "market_filter")
        s = t.get_summary()
        assert s["total"] == 1
        assert s["positions"][0]["ticker"] == "005930"

    def test_update_prices_updates_position(self):
        t = self._tracker()
        t.on_blocked("005930", 70_000, datetime.now(), "market_filter")
        t.update_prices("005930", 75_000)
        s = t.get_summary()
        assert s["positions"][0]["current_price"] == 75_000

    def test_update_prices_no_position_is_noop(self):
        t = self._tracker()
        t.update_prices("999999", 10_000)  # 차단 이력 없음 → no-op
        assert t.get_summary()["total"] == 0

    def test_on_blocked_overwrites_same_ticker(self):
        t = self._tracker()
        t.on_blocked("005930", 70_000, datetime.now(), "market_filter")
        t.on_blocked("005930", 72_000, datetime.now(), "intraday_market_filter")
        s = t.get_summary()
        assert s["total"] == 1
        assert s["positions"][0]["signal_price"] == 72_000

    def test_close_all_moves_to_closed(self):
        t = self._tracker()
        t.on_blocked("005930", 70_000, datetime.now(), "market_filter")
        t.on_blocked("000660", 80_000, datetime.now(), "intraday_market_filter")
        t.close_all()
        s = t.get_summary()
        assert s["total"] == 2
        # 활성 포지션은 0이어야 함
        assert len(t._positions) == 0
        assert len(t._closed) == 2

    def test_get_summary_empty(self):
        t = self._tracker()
        s = t.get_summary()
        assert s["total"] == 0
        assert s["profit_count"] == 0
        assert s["loss_count"] == 0
        assert s["avg_profit_pct"] == 0.0
        assert s["avg_loss_pct"] == 0.0
        assert s["positions"] == []

    def test_get_summary_profit_loss_counts(self):
        t = self._tracker()
        now = datetime.now()
        t.on_blocked("A", 10_000, now, "market_filter")
        t.update_prices("A", 11_000)  # +10%
        t.on_blocked("B", 10_000, now, "market_filter")
        t.update_prices("B", 9_500)   # -5% (손절 미발동)
        s = t.get_summary()
        assert s["profit_count"] == 1
        assert s["loss_count"] == 1
        assert s["avg_profit_pct"] == pytest.approx(0.10)
        assert s["avg_loss_pct"] == pytest.approx(-0.05)

    def test_reset_clears_all(self):
        t = self._tracker()
        t.on_blocked("005930", 70_000, datetime.now(), "market_filter")
        t.close_all()
        t.reset()
        assert t.get_summary()["total"] == 0

    def test_format_report_empty(self):
        t = self._tracker()
        assert "[SHADOW] 시장 필터 차단 없음" in t.format_report()

    def test_format_report_with_data(self):
        t = self._tracker()
        t.on_blocked("005930", 70_000, datetime.now(), "market_filter")
        t.update_prices("005930", 75_000)
        report = t.format_report()
        assert "005930" in report
        assert "수익 기회 놓침" in report

    def test_format_report_stopped_out(self):
        t = self._tracker()
        t.on_blocked("005930", 10_000, datetime.now(), "market_filter")
        t.update_prices("005930", 9_000)  # stop out
        report = t.format_report()
        assert "(손절)" in report
        assert "차단 정당" in report
