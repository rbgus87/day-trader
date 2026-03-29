"""tests/test_flow_strategy.py — FlowStrategy 단위 테스트."""

from datetime import time

import pandas as pd
import pytest

from config.settings import TradingConfig
from strategy.flow_strategy import FlowStrategy


def make_config(**overrides) -> TradingConfig:
    defaults = dict(flow_volume_surge_ratio=2.5, flow_vwap_filter=True)
    defaults.update(overrides)
    return TradingConfig(**defaults)


def make_candles(closes: list[float], opens: list[float] | None = None,
                 vwap: float | None = None) -> pd.DataFrame:
    n = len(closes)
    if opens is None:
        opens = [c - 10 for c in closes]
    data = {
        "open": opens,
        "high": [c + 10 for c in closes],
        "low": [c - 10 for c in closes],
        "close": closes,
        "volume": [1000] * n,
    }
    if vwap is not None:
        data["vwap"] = [vwap] * n
    return pd.DataFrame(data)


@pytest.fixture
def strategy() -> FlowStrategy:
    strat = FlowStrategy(make_config())
    strat.configure_multi_trade(max_trades=5, cooldown_minutes=0)
    strat.set_backtest_time(time(10, 0))
    return strat


def _feed_volume_history(strat: FlowStrategy, volumes: list[int]):
    """5분봉 거래량 히스토리를 주입."""
    for v in volumes:
        strat.on_candle_5m({"volume": v})


# 1. 거래량 급증 + 양봉 + VWAP 상회 → 신호 발생
def test_signal_on_volume_surge(strategy):
    # 4개 히스토리: avg=1000, 현재=3000 (3배 > 2.5배)
    _feed_volume_history(strategy, [1000, 1000, 1000, 5000])
    # 상승 중 양봉, 시가 대비 상승
    candles = make_candles(
        closes=[100, 105, 110],
        opens=[99, 100, 105],
        vwap=100,
    )
    tick = {"ticker": "005930", "price": 110}
    result = strategy.generate_signal(candles, tick)
    assert result is not None
    assert result.side == "buy"
    assert result.strategy == "flow"


# 2. 거래량 급증 but VWAP 하회 → 신호 차단
def test_vwap_blocks_signal(strategy):
    _feed_volume_history(strategy, [1000, 1000, 1000, 5000])
    candles = make_candles(
        closes=[100, 105, 110],
        opens=[99, 100, 105],
        vwap=120,  # VWAP 120 > close 110
    )
    tick = {"ticker": "005930", "price": 110}
    result = strategy.generate_signal(candles, tick)
    assert result is None


# 3. 거래량 평이 → 신호 없음
def test_no_signal_normal_volume(strategy):
    _feed_volume_history(strategy, [1000, 1000, 1000, 1000])
    candles = make_candles(closes=[100, 105, 110], opens=[99, 100, 105], vwap=100)
    tick = {"ticker": "005930", "price": 110}
    result = strategy.generate_signal(candles, tick)
    assert result is None


# 4. 09:25 (시간 외) → 신호 차단
def test_blocked_before_signal_start(strategy):
    strategy.set_backtest_time(time(9, 25))
    _feed_volume_history(strategy, [1000, 1000, 1000, 5000])
    candles = make_candles(closes=[100, 105, 110], opens=[99, 100, 105], vwap=100)
    tick = {"ticker": "005930", "price": 110}
    result = strategy.generate_signal(candles, tick)
    assert result is None


# 5. 히스토리 4개 미만 → 신호 없음
def test_no_signal_insufficient_history(strategy):
    _feed_volume_history(strategy, [1000, 1000])  # 2개만
    candles = make_candles(closes=[100, 105, 110], opens=[99, 100, 105])
    tick = {"ticker": "005930", "price": 110}
    result = strategy.generate_signal(candles, tick)
    assert result is None


# 6. 하락 중 → 신호 없음
def test_no_signal_price_declining(strategy):
    _feed_volume_history(strategy, [1000, 1000, 1000, 5000])
    candles = make_candles(closes=[110, 105, 100], opens=[111, 106, 101])
    tick = {"ticker": "005930", "price": 100}
    result = strategy.generate_signal(candles, tick)
    assert result is None


# 7. 손절가 / 익절가
def test_stop_loss_and_take_profit(strategy):
    sl = strategy.get_stop_loss(10_000)
    assert sl == pytest.approx(10_000 * (1 + strategy._config.flow_stop_loss_pct))

    tp1, tp2 = strategy.get_take_profit(10_000)
    assert tp1 == pytest.approx(10_000 * (1 + strategy._config.tp1_pct))
    assert tp2 == 0


# 8. reset은 거래량 히스토리 초기화
def test_reset_clears_history(strategy):
    _feed_volume_history(strategy, [1000, 2000, 3000, 4000])
    assert len(strategy._volume_history) == 4
    strategy.reset()
    assert len(strategy._volume_history) == 0
