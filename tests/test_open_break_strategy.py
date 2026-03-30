"""tests/test_open_break_strategy.py — OpenBreakStrategy 단위 테스트."""

from datetime import time

import pandas as pd
import pytest

from config.settings import TradingConfig
from strategy.open_break_strategy import OpenBreakStrategy


def make_config(**overrides) -> TradingConfig:
    defaults = dict(
        open_break_pct=0.005,
        open_break_volume_ratio=0.3,
        open_break_stop_loss_pct=-0.005,
        open_break_start="09:15",
        tp1_pct=0.03,
    )
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
def strategy() -> OpenBreakStrategy:
    cfg = make_config()
    strat = OpenBreakStrategy(cfg)
    strat.set_prev_day_volume(1_000_000)
    strat.configure_multi_trade(max_trades=5, cooldown_minutes=0)
    strat.set_backtest_time(time(9, 30))
    return strat


def test_signal_on_breakout(strategy):
    """시가 +0.5% 돌파 + 거래량 → 신호."""
    # 시가 10000, close 10060 (> 10000*1.005=10050), vol 300k >= 1M * 0.3
    candles = make_candles(open_price=10_000, close=10_060, volume=300_000)
    signal = strategy.generate_signal(candles, make_tick(10_060))
    assert signal is not None
    assert signal.side == "buy"
    assert signal.strategy == "open_break"


def test_no_signal_before_start_time(strategy):
    """09:10 (시간 미달) → 신호 없음."""
    strategy.set_backtest_time(time(9, 10))
    candles = make_candles(open_price=10_000, close=10_060, volume=300_000)
    assert strategy.generate_signal(candles, make_tick(10_060)) is None


def test_no_signal_low_volume(strategy):
    """돌파했지만 거래량 미달 → 신호 없음."""
    # 필요: 1M * 0.3 = 300k, 제공: 200k
    candles = make_candles(open_price=10_000, close=10_060, volume=200_000)
    assert strategy.generate_signal(candles, make_tick(10_060)) is None


def test_no_signal_no_breakout(strategy):
    """시가 +0.3% (미달) → 신호 없음."""
    candles = make_candles(open_price=10_000, close=10_030, volume=300_000)
    assert strategy.generate_signal(candles, make_tick(10_030)) is None


def test_stop_loss(strategy):
    sl = strategy.get_stop_loss(10_000)
    assert sl == pytest.approx(10_000 * (1 + strategy._config.open_break_stop_loss_pct))
