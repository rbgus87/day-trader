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
    defaults = dict(
        momentum_volume_ratio=2.0,
        tp1_pct=0.03,
        trailing_stop_pct=0.01,
        momentum_vwap_filter=True,
    )
    defaults.update(overrides)
    return TradingConfig(**defaults)


def make_candles(close: float, volume: int, rows: int = 3,
                 vwap: float | None = None) -> pd.DataFrame:
    data = {
        "open": [close] * rows,
        "high": [close] * rows,
        "low": [close] * rows,
        "close": [close] * rows,
        "volume": [volume // rows] * rows,
    }
    if vwap is not None:
        data["vwap"] = [vwap] * rows
    return pd.DataFrame(data)


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


# ---------------------------------------------------------------------------
# 1. 전일 고점 미달 → None
# ---------------------------------------------------------------------------

def test_no_signal_below_prev_high(strategy):
    candles = make_candles(close=9_800, volume=2_000_000)
    result = strategy.generate_signal(candles, make_tick("005930", 9_800))
    assert result is None


# ---------------------------------------------------------------------------
# 2. 돌파 + 거래량 충족 → 매수 신호
# ---------------------------------------------------------------------------

def test_signal_on_breakout(strategy):
    # 전일 고점 10_000, 현재가 10_100 (돌파)
    # 전일 거래량 1_000_000 × 1.5 = 1_500_000 → 2_100_000 충족
    candles = make_candles(close=10_100, volume=2_100_000)
    result = strategy.generate_signal(candles, make_tick("005930", 10_100))
    assert result is not None
    assert result.side == "buy"
    assert result.strategy == "momentum"
    assert result.price == 10_100


# ---------------------------------------------------------------------------
# 3. 돌파했지만 거래량 부족 → None
# ---------------------------------------------------------------------------

def test_no_signal_low_volume(strategy):
    # 1_000_000 × 1.5 = 1_500_000 필요 → 1_200_000 부족
    candles = make_candles(close=10_100, volume=1_200_000)
    result = strategy.generate_signal(candles, make_tick("005930", 10_100))
    assert result is None


# ---------------------------------------------------------------------------
# 4. 손절가 검증
# ---------------------------------------------------------------------------

def test_stop_loss(strategy):
    entry = 10_000
    sl = strategy.get_stop_loss(entry)
    expected = entry * (1 + strategy._config.momentum_stop_loss_pct)
    assert sl == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 5. 포지션 보유 중 신호 차단
# ---------------------------------------------------------------------------

def test_no_signal_while_in_position(strategy):
    candles = make_candles(close=10_100, volume=2_100_000)
    tick = make_tick("005930", 10_100)

    first = strategy.generate_signal(candles, tick)
    assert first is not None
    strategy.on_entry()

    second = strategy.generate_signal(candles, tick)
    assert second is None

    strategy.on_exit()
    third = strategy.generate_signal(candles, tick)
    assert third is not None


# ---------------------------------------------------------------------------
# 6. VWAP 하회 → 신호 차단
# ---------------------------------------------------------------------------

def test_vwap_filter_blocks_signal(strategy):
    # close=10_100 but vwap=10_200 → 현재가 < VWAP → 차단
    candles = make_candles(close=10_100, volume=2_100_000, vwap=10_200)
    result = strategy.generate_signal(candles, make_tick("005930", 10_100))
    assert result is None


# ---------------------------------------------------------------------------
# 7. VWAP 상회 → 신호 통과
# ---------------------------------------------------------------------------

def test_vwap_filter_allows_when_above(strategy):
    candles = make_candles(close=10_100, volume=2_100_000, vwap=10_000)
    result = strategy.generate_signal(candles, make_tick("005930", 10_100))
    assert result is not None


# ---------------------------------------------------------------------------
# 8. VWAP 필터 off → VWAP 하회해도 통과
# ---------------------------------------------------------------------------

def test_vwap_filter_off():
    cfg = make_config(momentum_vwap_filter=False)
    strat = MomentumStrategy(cfg)
    strat.set_prev_day_data(high=10_000, volume=1_000_000)
    strat.configure_multi_trade(max_trades=5, cooldown_minutes=0)
    strat.set_backtest_time(time(10, 0))

    candles = make_candles(close=10_100, volume=2_100_000, vwap=10_200)
    result = strat.generate_signal(candles, make_tick("005930", 10_100))
    assert result is not None


# ---------------------------------------------------------------------------
# 9. VWAP 데이터 없음 → 필터 통과 (안전 폴백)
# ---------------------------------------------------------------------------

def test_vwap_filter_no_data(strategy):
    candles = make_candles(close=10_100, volume=2_100_000)  # vwap 컬럼 없음
    result = strategy.generate_signal(candles, make_tick("005930", 10_100))
    assert result is not None


# ---------------------------------------------------------------------------
# 10. reset 테스트
# ---------------------------------------------------------------------------

def test_reset(strategy):
    strategy.on_entry()
    assert strategy._has_position is True
    strategy.reset()
    assert strategy._has_position is False
