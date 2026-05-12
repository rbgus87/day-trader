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
    """close 키 dict deque (open/high/low는 close와 동일 단순화)."""
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
        rm._positions["000001"]["entry_time"] = datetime(2026, 5, 12, 10, 0)
        hist = _candles([1000.0] + [1001.0] * 9 + [992.0])
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
