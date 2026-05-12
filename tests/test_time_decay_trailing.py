"""tests/test_time_decay_trailing.py — risk_manager.update_trailing_stop time_decay 통합."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from config.settings import TradingConfig
from core.exit_logic import TimeDecayPhase
from data.db_manager import DbManager
from risk.risk_manager import RiskManager


def _rm(tmp_path, **config_overrides) -> RiskManager:
    """trading_config의 일부를 오버라이드한 RiskManager."""
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
        """11:00 (decay=1.0) → trail 폭은 ATR×1.0, min 2% 적용.

        trail_pct = clamp(ATR 6% × 1.0, 2%, 10%) = 6%
        new_stop = 10500 × (1 - 0.06) = 9870
        """
        rm = _rm(
            tmp_path,
            time_decay_trailing_enabled=True,
            time_decay_phases=_phases(),
            atr_trail_enabled=True,
            atr_trail_multiplier=1.0,
            atr_trail_min_pct=0.02,
            atr_trail_max_pct=0.10,
            atr_tp_enabled=False,
            breakeven_enabled=False,
        )
        rm.register_position(
            ticker="000001", entry_price=10000, qty=10, stop_loss=9200,
            status="confirmed",
        )
        rm.update_trailing_stop(
            "000001", current_price=10500, atr_pct=0.06,
            now=datetime(2026, 5, 12, 11, 0),
        )
        pos = rm.get_position("000001")
        assert pos["stop_loss"] == pytest.approx(9870, abs=1.0)

    def test_late_afternoon_narrows_trail(self, tmp_path):
        """14:45 (decay=0.3) — ATR 6%.

        effective_min = max(0.02×0.3, 0.01) = max(0.006, 0.01) = 0.01
        trail_pct = clamp(0.06 × 0.3, 0.01, 0.10) = clamp(0.018, 0.01, 0.10) = 0.018
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
            breakeven_enabled=False,
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
        assert pos["stop_loss"] == pytest.approx(10311, abs=1.0)

    def test_hard_floor_kicks_in(self, tmp_path):
        """ATR=2%, decay=0.3 → raw_trail = 0.006 → hard floor 1.0% 적용.

        new_stop = 10500 × (1 - 0.01) = 10395
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
            breakeven_enabled=False,
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
            breakeven_enabled=False,
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
        assert pos["stop_loss"] == pytest.approx(9870, abs=1.0)

    def test_now_none_uses_wall_clock(self, tmp_path):
        """now=None → datetime.now() 사용 (예외 없이 호출 가능)."""
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
        # now 미전달 — 예외 없이 호출 가능해야 함
        rm.update_trailing_stop("000001", current_price=10500, atr_pct=0.06)
        assert rm.get_position("000001")["stop_loss"] >= 9200
