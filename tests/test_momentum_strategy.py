"""tests/test_momentum_strategy.py — MomentumStrategy 단위 테스트."""

from datetime import time

import pandas as pd
import pytest

from config.settings import TradingConfig
from strategy.momentum_strategy import MomentumStrategy


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def make_config(**overrides) -> TradingConfig:
    defaults = dict(momentum_volume_ratio=2.0, tp1_pct=0.03, trailing_stop_pct=0.01, adx_enabled=False, rvol_enabled=False, vwap_enabled=False, min_breakout_pct=0.0)
    defaults.update(overrides)
    return TradingConfig(**defaults)


def make_candles(close: float, volume: int, rows: int = 3) -> pd.DataFrame:
    return pd.DataFrame({
        "open": [close] * rows,
        "high": [close] * rows,
        "low": [close] * rows,
        "close": [close] * rows,
        "volume": [volume // rows] * rows,
    })


def make_tick(ticker: str, price: float) -> dict:
    return {"ticker": ticker, "price": price}


@pytest.fixture
def strategy() -> MomentumStrategy:
    cfg = make_config()
    strat = MomentumStrategy(cfg)
    strat.set_prev_day_data(high=10_000, volume=1_000_000)
    strat.configure_multi_trade(max_trades=5, cooldown_minutes=0)
    strat.set_backtest_time(time(10, 0))
    return strat


def test_no_signal_below_prev_high(strategy):
    result = strategy.generate_signal(make_candles(9_800, 2_100_000), make_tick("005930", 9_800))
    assert result is None


def test_signal_on_breakout(strategy):
    result = strategy.generate_signal(make_candles(10_100, 2_100_000), make_tick("005930", 10_100))
    assert result is not None
    assert result.side == "buy"
    assert result.strategy == "momentum"


def test_no_signal_low_volume(strategy):
    result = strategy.generate_signal(make_candles(10_100, 1_500_000), make_tick("005930", 10_100))
    assert result is None


def test_stop_loss(strategy):
    sl = strategy.get_stop_loss(10_000)
    assert sl == pytest.approx(10_000 * (1 + strategy._config.momentum_stop_loss_pct))


def test_no_signal_while_in_position(strategy):
    candles = make_candles(10_100, 2_100_000)
    tick = make_tick("005930", 10_100)
    assert strategy.generate_signal(candles, tick) is not None
    strategy.on_entry()
    assert strategy.generate_signal(candles, tick) is None
    strategy.on_exit()
    assert strategy.generate_signal(candles, tick) is not None


def test_reset(strategy):
    strategy.on_entry()
    assert strategy._has_position is True
    strategy.reset()
    assert strategy._has_position is False
