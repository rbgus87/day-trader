"""tests/test_momentum_strategy.py — MomentumStrategy 단위 테스트."""

from datetime import datetime, time
from unittest.mock import patch

import pandas as pd
import pytest

from config.settings import TradingConfig
from strategy.momentum_strategy import MomentumStrategy


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def make_config(**overrides) -> TradingConfig:
    """기본 TradingConfig 생성 (환경변수 없이)."""
    defaults = dict(
        momentum_volume_ratio=2.0,
        tp1_pct=0.02,
        trailing_stop_pct=0.01,
    )
    defaults.update(overrides)
    return TradingConfig(**defaults)


def make_candles(close: float, volume: int, rows: int = 3) -> pd.DataFrame:
    """간단한 캔들 DataFrame 생성."""
    return pd.DataFrame(
        {
            "open": [close] * rows,
            "high": [close] * rows,
            "low": [close] * rows,
            "close": [close] * rows,
            "volume": [volume // rows] * rows,
        }
    )


def make_tick(ticker: str, price: float) -> dict:
    return {"ticker": ticker, "price": price}


def patch_tradable():
    """is_tradable_time() 를 True 로 고정하는 context manager."""
    return patch(
        "strategy.base_strategy.datetime",
        **{"return_value": None,
           "now.return_value.time.return_value": time(10, 0)},
    )


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def strategy() -> MomentumStrategy:
    cfg = make_config()
    strat = MomentumStrategy(cfg)
    strat.set_prev_day_data(high=10_000, volume=1_000_000)
    strat.configure_multi_trade(max_trades=5, cooldown_minutes=0)
    return strat


# ---------------------------------------------------------------------------
# 1. 전일 고점 미달 → None
# ---------------------------------------------------------------------------

def test_no_signal_below_prev_high(strategy: MomentumStrategy):
    """현재가가 전일 고점 이하이면 신호 없음."""
    candles = make_candles(close=9_800, volume=3_000_000)
    tick = make_tick("005930", price=9_800)

    with patch("strategy.base_strategy.datetime") as mock_dt:
        mock_dt.now.return_value.time.return_value = time(10, 0)
        result = strategy.generate_signal(candles, tick)

    assert result is None


# ---------------------------------------------------------------------------
# 2. 돌파 + 거래량 충족 → 매수 신호
# ---------------------------------------------------------------------------

def test_signal_on_breakout(strategy: MomentumStrategy):
    """전일 고점 돌파 + 거래량 200% 이상 → Signal(side='buy') 반환."""
    # 전일 고점 10_000, 현재가 10_100 (돌파)
    # 전일 거래량 1_000_000 × 2.0 = 2_000_000 필요 → 2_100_000 공급
    candles = make_candles(close=10_100, volume=2_100_000)
    tick = make_tick("005930", price=10_100)

    with patch("strategy.base_strategy.datetime") as mock_dt:
        mock_dt.now.return_value.time.return_value = time(10, 0)
        result = strategy.generate_signal(candles, tick)

    assert result is not None
    assert result.side == "buy"
    assert result.ticker == "005930"
    assert result.strategy == "momentum"
    assert result.price == 10_100


# ---------------------------------------------------------------------------
# 3. 돌파했지만 거래량 부족 → None
# ---------------------------------------------------------------------------

def test_no_signal_low_volume(strategy: MomentumStrategy):
    """전일 고점 돌파라도 누적 거래량 < 200% 이면 신호 없음."""
    # 전일 거래량 1_000_000 × 2.0 = 2_000_000 필요 → 1_500_000 부족
    candles = make_candles(close=10_100, volume=1_500_000)
    tick = make_tick("005930", price=10_100)

    with patch("strategy.base_strategy.datetime") as mock_dt:
        mock_dt.now.return_value.time.return_value = time(10, 0)
        result = strategy.generate_signal(candles, tick)

    assert result is None


# ---------------------------------------------------------------------------
# 4. 손절가: 진입가 × (1 + momentum_stop_loss_pct)
# ---------------------------------------------------------------------------

def test_stop_loss(strategy: MomentumStrategy):
    """손절가 = 진입가 × (1 + momentum_stop_loss_pct)."""
    entry = 10_000
    sl = strategy.get_stop_loss(entry)
    expected = entry * (1 + strategy._config.momentum_stop_loss_pct)
    assert sl == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 5. 신호 1회만 발생
# ---------------------------------------------------------------------------

def test_no_signal_while_in_position(strategy: MomentumStrategy):
    """포지션 보유 중에는 추가 신호가 발생하지 않는다."""
    candles = make_candles(close=10_100, volume=2_100_000)
    tick = make_tick("005930", price=10_100)

    with patch("strategy.base_strategy.datetime") as mock_dt:
        mock_dt.now.return_value.time.return_value = time(10, 0)
        mock_dt.combine = datetime.combine
        first = strategy.generate_signal(candles, tick)
        assert first is not None
        strategy.on_entry()
        second = strategy.generate_signal(candles, tick)
        assert second is None
        strategy.on_exit()
        third = strategy.generate_signal(candles, tick)
        assert third is not None
