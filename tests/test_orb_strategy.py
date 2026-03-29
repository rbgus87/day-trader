"""tests/test_orb_strategy.py — DEPRECATED: ORB 전략 폐기."""

import pytest

pytestmark = pytest.mark.skip("ORB strategy deprecated — 백테스트 PF<1.0")
import pandas as pd
from unittest.mock import patch

from strategy.orb_strategy import OrbStrategy
from config.settings import TradingConfig


@pytest.fixture
def orb():
    s = OrbStrategy(TradingConfig())
    s.configure_multi_trade(max_trades=5, cooldown_minutes=0)
    return s


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


def test_no_signal_while_in_position(orb):
    """포지션 보유 중에는 추가 신호가 발생하지 않는다."""
    orb._range_high = 70400
    orb._range_low = 69600
    candles = pd.DataFrame({
        "time": ["09:16"], "close": [70500], "high": [70600],
        "low": [70400], "volume": [5000],
    })
    tick = {"ticker": "005930", "price": 70500, "time": "091600", "volume": 500}
    with patch.object(orb, "is_tradable_time", return_value=True):
        sig1 = orb.generate_signal(candles, tick)
        assert sig1 is not None
        orb.on_entry()  # 포지션 진입
        sig2 = orb.generate_signal(candles, tick)
        assert sig2 is None  # 포지션 보유 중 차단
        orb.on_exit()  # 청산
        sig3 = orb.generate_signal(candles, tick)
        assert sig3 is not None  # 재진입 가능
