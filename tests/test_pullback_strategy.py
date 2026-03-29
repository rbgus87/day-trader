"""tests/test_pullback_strategy.py — 눌림목 매매 전략 테스트."""

import pytest
import pandas as pd
from unittest.mock import patch

from strategy.pullback_strategy import PullbackStrategy
from config.settings import TradingConfig


OPEN_PRICE = 9900  # 당일 시가 (min_gain_pct=4% 기준: MA5 ~10340 → gain ~4.4%)


@pytest.fixture
def strategy():
    s = PullbackStrategy(TradingConfig())
    s.set_open_price(OPEN_PRICE)
    s.configure_multi_trade(max_trades=5, cooldown_minutes=0)
    return s


def _make_candles(n=25, *, close_values=None, ascending_ma20=True):
    if ascending_ma20:
        base_closes = [10300 + i * 2 for i in range(n)]
    else:
        base_closes = [10300 + (n - i) * 2 for i in range(n)]
    base_opens = [c - 10 for c in base_closes]
    if close_values is not None:
        for i, v in enumerate(close_values):
            base_closes[n - len(close_values) + i] = v
    return pd.DataFrame({
        "open": base_opens,
        "high": [c + 20 for c in base_closes],
        "low": [c - 20 for c in base_closes],
        "close": base_closes,
        "volume": [1000] * n,
    })


def _tick(price: float, ticker: str = "005930") -> dict:
    return {"ticker": ticker, "price": price, "time": "100000", "volume": 100}


def test_no_signal_small_gain(strategy):
    """시가 대비 3.9% 상승 — 최소 상승률(4%) 미달."""
    candles = _make_candles(ascending_ma20=True)
    price = int(OPEN_PRICE * 1.039)
    with patch.object(strategy, "is_tradable_time", return_value=True):
        assert strategy.generate_signal(candles, _tick(price)) is None


def test_signal_on_pullback(strategy):
    n = 25
    candles = _make_candles(n=n, ascending_ma20=True)
    ma5 = candles["close"].iloc[-5:].mean()
    candles.at[n - 2, "close"] = candles.at[n - 2, "open"] - 10
    candles.at[n - 1, "close"] = candles.at[n - 1, "open"] + 10
    with patch.object(strategy, "is_tradable_time", return_value=True):
        signal = strategy.generate_signal(candles, _tick(float(ma5)))
    assert signal is not None
    assert signal.side == "buy"
    assert signal.strategy == "pullback"


def test_no_signal_no_reversal(strategy):
    n = 25
    candles = _make_candles(n=n, ascending_ma20=True)
    ma5 = candles["close"].iloc[-5:].mean()
    candles.at[n - 2, "close"] = candles.at[n - 2, "open"] + 10
    candles.at[n - 1, "close"] = candles.at[n - 1, "open"] + 10
    with patch.object(strategy, "is_tradable_time", return_value=True):
        assert strategy.generate_signal(candles, _tick(float(ma5))) is None


def test_stop_loss(strategy):
    entry = 10000.0
    sl = strategy.get_stop_loss(entry)
    assert sl == pytest.approx(entry * (1 + TradingConfig().pullback_stop_loss_pct))
    assert sl == pytest.approx(9820.0)


def test_no_signal_while_in_position(strategy):
    n = 25
    candles = _make_candles(n=n, ascending_ma20=True)
    ma5 = candles["close"].iloc[-5:].mean()
    candles.at[n - 2, "close"] = candles.at[n - 2, "open"] - 10
    candles.at[n - 1, "close"] = candles.at[n - 1, "open"] + 10
    tick = _tick(float(ma5))
    with patch.object(strategy, "is_tradable_time", return_value=True):
        assert strategy.generate_signal(candles, tick) is not None
        strategy.on_entry()
        assert strategy.generate_signal(candles, tick) is None
        strategy.on_exit()
        assert strategy.generate_signal(candles, tick) is not None


def test_no_signal_ma20_descending(strategy):
    n = 25
    candles = _make_candles(n=n, ascending_ma20=False)
    ma5 = candles["close"].iloc[-5:].mean()
    candles.at[n - 2, "close"] = candles.at[n - 2, "open"] - 10
    candles.at[n - 1, "close"] = candles.at[n - 1, "open"] + 10
    with patch.object(strategy, "is_tradable_time", return_value=True):
        assert strategy.generate_signal(candles, _tick(float(ma5))) is None


def test_take_profit(strategy):
    entry = 10000.0
    tp1, tp2 = strategy.get_take_profit(entry)
    assert tp1 == pytest.approx(entry * (1 + TradingConfig().tp1_pct))
    assert tp2 == 0


def test_reset_allows_new_signal(strategy):
    n = 25
    candles = _make_candles(n=n, ascending_ma20=True)
    ma5 = candles["close"].iloc[-5:].mean()
    candles.at[n - 2, "close"] = candles.at[n - 2, "open"] - 10
    candles.at[n - 1, "close"] = candles.at[n - 1, "open"] + 10
    tick = _tick(float(ma5))
    with patch.object(strategy, "is_tradable_time", return_value=True):
        assert strategy.generate_signal(candles, tick) is not None
        strategy.reset()
        strategy.set_open_price(OPEN_PRICE)
        assert strategy.generate_signal(candles, tick) is not None
