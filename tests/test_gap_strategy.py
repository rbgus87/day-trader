"""tests/test_gap_strategy.py — GapStrategy 단위 테스트."""

from datetime import time

import pandas as pd
import pytest

from config.settings import TradingConfig
from strategy.gap_strategy import GapStrategy


def make_config(**overrides) -> TradingConfig:
    defaults = dict(gap_min_gap_pct=0.015, gap_stop_loss_pct=-0.01, tp1_pct=0.03)
    defaults.update(overrides)
    return TradingConfig(**defaults)


def make_candles(open_price: float, close: float, volume: int, rows: int = 3) -> pd.DataFrame:
    return pd.DataFrame({
        "open": [open_price] * rows,
        "high": [max(open_price, close) + 10] * rows,
        "low": [min(open_price, close) - 10] * rows,
        "close": [close] * rows,
        "volume": [volume // rows] * rows,
    })


def make_tick(price: float, ticker: str = "005930") -> dict:
    return {"ticker": ticker, "price": price}


@pytest.fixture
def strategy() -> GapStrategy:
    cfg = make_config()
    strat = GapStrategy(cfg)
    strat.set_prev_close(10_000)
    strat.configure_multi_trade(max_trades=5, cooldown_minutes=0)
    strat.set_backtest_time(time(10, 0))
    return strat


def test_signal_on_gap_up_bullish(strategy):
    """갭 1.5%+ 양봉 → 신호 발생."""
    # 시가 10200 (갭 2%), 양봉 (close > open)
    candles = make_candles(open_price=10_200, close=10_250, volume=3_000)
    signal = strategy.generate_signal(candles, make_tick(10_250))
    assert signal is not None
    assert signal.side == "buy"
    assert signal.strategy == "gap"


def test_no_signal_small_gap(strategy):
    """갭 0.5% (미달) → 신호 없음."""
    # 시가 10050 (갭 0.5% < 1.5%)
    candles = make_candles(open_price=10_050, close=10_060, volume=3_000)
    assert strategy.generate_signal(candles, make_tick(10_060)) is None


def test_no_signal_bearish_candle(strategy):
    """갭 있지만 음봉 → 신호 없음."""
    # 시가 10200 (갭 2%), 음봉 (close < open)
    candles = make_candles(open_price=10_200, close=10_180, volume=3_000)
    assert strategy.generate_signal(candles, make_tick(10_180)) is None


def test_signal_only_once_per_day(strategy):
    """당일 첫 1회만 신호."""
    candles = make_candles(open_price=10_200, close=10_250, volume=3_000)
    tick = make_tick(10_250)
    assert strategy.generate_signal(candles, tick) is not None
    strategy.on_entry()
    strategy.on_exit()
    # 두 번째 시도: signaled_today가 True
    assert strategy.generate_signal(candles, tick) is None


def test_stop_loss(strategy):
    sl = strategy.get_stop_loss(10_000)
    assert sl == pytest.approx(10_000 * (1 + strategy._config.gap_stop_loss_pct))


def test_reset_clears_signal_flag(strategy):
    candles = make_candles(open_price=10_200, close=10_250, volume=3_000)
    tick = make_tick(10_250)
    strategy.generate_signal(candles, tick)
    strategy.reset()
    strategy.set_prev_close(10_000)
    strategy.set_backtest_time(time(10, 0))
    assert strategy.generate_signal(candles, tick) is not None
