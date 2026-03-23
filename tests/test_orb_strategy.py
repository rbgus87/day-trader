"""tests/test_orb_strategy.py"""

import pytest
import pandas as pd
from unittest.mock import patch

from strategy.orb_strategy import OrbStrategy
from config.settings import TradingConfig


@pytest.fixture
def orb():
    return OrbStrategy(TradingConfig())


def test_no_signal_during_range_building(orb):
    candles = pd.DataFrame({
        "time": ["09:05", "09:06", "09:07"],
        "open": [70000, 70200, 69800],
        "high": [70300, 70400, 70100],
        "low": [69900, 69700, 69600],
        "close": [70200, 69800, 70100],
        "volume": [1000, 1200, 800],
    })
    tick = {"ticker": "005930", "price": 70100, "time": "091000", "volume": 100}
    with patch.object(orb, "is_tradable_time", return_value=True):
        orb._range_high = None
        signal = orb.generate_signal(candles, tick)
        assert signal is None


def test_signal_on_breakout(orb):
    orb._range_high = 70400
    orb._range_low = 69600
    orb._prev_day_volume = 10000

    candles = pd.DataFrame({
        "time": ["09:15", "09:16"],
        "close": [70300, 70500],
        "high": [70400, 70600],
        "low": [70200, 70400],
        "volume": [8000, 9000],
    })
    tick = {"ticker": "005930", "price": 70500, "time": "091600", "volume": 500}

    with patch.object(orb, "is_tradable_time", return_value=True):
        signal = orb.generate_signal(candles, tick)
        assert signal is not None
        assert signal.side == "buy"


def test_stop_loss(orb):
    sl = orb.get_stop_loss(70000)
    assert sl == 70000 * (1 + TradingConfig().orb_stop_loss_pct)


def test_take_profit(orb):
    tp1, tp2 = orb.get_take_profit(70000)
    assert tp1 == 70000 * (1 + TradingConfig().tp1_pct)
    assert tp2 == 0


def test_signal_fires_only_once(orb):
    orb._range_high = 70400
    orb._range_low = 69600
    candles = pd.DataFrame({
        "time": ["09:16"], "close": [70500], "high": [70600],
        "low": [70400], "volume": [5000],
    })
    tick = {"ticker": "005930", "price": 70500, "time": "091600", "volume": 500}
    with patch.object(orb, "is_tradable_time", return_value=True):
        sig1 = orb.generate_signal(candles, tick)
        sig2 = orb.generate_signal(candles, tick)
        assert sig1 is not None
        assert sig2 is None
