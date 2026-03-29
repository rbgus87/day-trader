"""tests/test_momentum_strategy.py — MomentumStrategy v2 단위 테스트."""

from datetime import datetime, time

import pandas as pd
import pytest

from config.settings import TradingConfig
from strategy.momentum_strategy import (
    MomentumStrategy,
    STATE_WAITING,
    STATE_RETEST,
)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def make_config(**overrides) -> TradingConfig:
    defaults = dict(
        momentum_volume_ratio=1.5,
        tp1_pct=0.03,
        trailing_stop_pct=0.01,
        momentum_retest_band_pct=0.003,
        momentum_retest_timeout_min=30,
        momentum_vwap_filter=True,
    )
    defaults.update(overrides)
    return TradingConfig(**defaults)


def make_candles(close: float, volume: int, rows: int = 3, vwap: float | None = None,
                 open_price: float | None = None) -> pd.DataFrame:
    o = open_price if open_price is not None else close - 10
    data = {
        "open": [o] * rows,
        "high": [close + 10] * rows,
        "low": [close - 10] * rows,
        "close": [close] * rows,
        "volume": [volume // rows] * rows,
    }
    if vwap is not None:
        data["vwap"] = [vwap] * rows
    return pd.DataFrame(data)


def make_tick(ticker: str, price: float) -> dict:
    return {"ticker": ticker, "price": price}


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

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

def test_no_signal_below_prev_high(strategy: MomentumStrategy):
    candles = make_candles(close=9_800, volume=2_000_000)
    tick = make_tick("005930", price=9_800)
    result = strategy.generate_signal(candles, tick)
    assert result is None
    assert strategy._state == STATE_WAITING


# ---------------------------------------------------------------------------
# 2. 돌파 → 리테스트 → 재돌파 → 매수 신호
# ---------------------------------------------------------------------------

def test_signal_on_retest_breakout(strategy: MomentumStrategy):
    # Step 1: 돌파 (10_100 > 10_000)
    candles = make_candles(close=10_100, volume=2_000_000)
    tick = make_tick("005930", price=10_100)
    result = strategy.generate_signal(candles, tick)
    assert result is None
    assert strategy._state == STATE_RETEST

    # Step 2: 리테스트 (전일 고점 ±0.3% = 9970~10030 이내)
    candles = make_candles(close=10_010, volume=2_000_000)
    tick = make_tick("005930", price=10_010)
    result = strategy.generate_signal(candles, tick)
    assert result is None

    # Step 3: 재돌파 (돌파가 10_100 상회 + 양봉)
    candles = make_candles(close=10_150, volume=2_000_000, open_price=10_050)
    tick = make_tick("005930", price=10_150)
    result = strategy.generate_signal(candles, tick)
    assert result is not None
    assert result.side == "buy"
    assert result.strategy == "momentum"


# ---------------------------------------------------------------------------
# 3. 돌파 → 30분 초과 → 타임아웃
# ---------------------------------------------------------------------------

def test_retest_timeout(strategy: MomentumStrategy):
    # 10:00에 돌파
    strategy.set_backtest_time(time(10, 0))
    candles = make_candles(close=10_100, volume=2_000_000)
    strategy.generate_signal(candles, make_tick("005930", 10_100))
    assert strategy._state == STATE_RETEST

    # 10:35에 리테스트 시도 (31분 초과)
    strategy.set_backtest_time(time(10, 35))
    candles = make_candles(close=10_010, volume=2_000_000)
    result = strategy.generate_signal(candles, make_tick("005930", 10_010))
    assert result is None
    assert strategy._state == STATE_WAITING


# ---------------------------------------------------------------------------
# 4. 돌파 → 리테스트 없이 계속 상승 → 신호 없음 (위꼬리 방지)
# ---------------------------------------------------------------------------

def test_no_signal_without_retest(strategy: MomentumStrategy):
    # 돌파
    candles = make_candles(close=10_100, volume=2_000_000)
    strategy.generate_signal(candles, make_tick("005930", 10_100))
    assert strategy._state == STATE_RETEST

    # 계속 상승 (리테스트 밴드에 진입 안 함) — 타임아웃 전
    for price in [10_200, 10_300, 10_400]:
        candles = make_candles(close=price, volume=2_000_000, open_price=price - 50)
        tick = make_tick("005930", price=price)
        result = strategy.generate_signal(candles, tick)
        # retest_low가 밴드 내로 내려온 적 없으므로 재돌파 불인정
        assert result is None


# ---------------------------------------------------------------------------
# 5. VWAP 하회 시 → 신호 차단
# ---------------------------------------------------------------------------

def test_vwap_filter_blocks_signal(strategy: MomentumStrategy):
    # 돌파
    candles = make_candles(close=10_100, volume=2_000_000, vwap=10_200)
    strategy.generate_signal(candles, make_tick("005930", 10_100))

    # 리테스트
    candles = make_candles(close=10_010, volume=2_000_000, vwap=10_200)
    strategy.generate_signal(candles, make_tick("005930", 10_010))

    # 재돌파 but VWAP 하회 (price=10_150 < vwap=10_200)
    candles = make_candles(close=10_150, volume=2_000_000, open_price=10_050, vwap=10_200)
    result = strategy.generate_signal(candles, make_tick("005930", 10_150))
    assert result is None


# ---------------------------------------------------------------------------
# 6. VWAP 필터 off → 신호 통과
# ---------------------------------------------------------------------------

def test_vwap_filter_off_allows_signal():
    cfg = make_config(momentum_vwap_filter=False)
    strat = MomentumStrategy(cfg)
    strat.set_prev_day_data(high=10_000, volume=1_000_000)
    strat.configure_multi_trade(max_trades=5, cooldown_minutes=0)
    strat.set_backtest_time(time(10, 0))

    # 돌파 → 리테스트 → 재돌파 (VWAP 하회이지만 필터 off)
    strat.generate_signal(
        make_candles(close=10_100, volume=2_000_000, vwap=10_200),
        make_tick("005930", 10_100),
    )
    strat.generate_signal(
        make_candles(close=10_010, volume=2_000_000, vwap=10_200),
        make_tick("005930", 10_010),
    )
    result = strat.generate_signal(
        make_candles(close=10_150, volume=2_000_000, open_price=10_050, vwap=10_200),
        make_tick("005930", 10_150),
    )
    assert result is not None
    assert result.strategy == "momentum"


# ---------------------------------------------------------------------------
# 7. 동적 손절 계산
# ---------------------------------------------------------------------------

def test_dynamic_stop_loss(strategy: MomentumStrategy):
    strategy._retest_low = 10_050
    entry = 10_100
    sl = strategy.get_stop_loss(entry)
    fixed_sl = entry * (1 + strategy._config.momentum_stop_loss_pct)  # 10019.2
    dynamic_sl = 10_050 * (1 - 0.003)  # 10019.85
    assert sl == pytest.approx(max(dynamic_sl, fixed_sl))
    assert sl >= fixed_sl


def test_stop_loss_without_retest(strategy: MomentumStrategy):
    strategy._retest_low = 0
    entry = 10_000
    sl = strategy.get_stop_loss(entry)
    expected = entry * (1 + strategy._config.momentum_stop_loss_pct)
    assert sl == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 8. 포지션 보유 중 신호 차단
# ---------------------------------------------------------------------------

def test_no_signal_while_in_position(strategy: MomentumStrategy):
    # 돌파 → 리테스트 → 재돌파 → 신호
    strategy.generate_signal(
        make_candles(close=10_100, volume=2_000_000),
        make_tick("005930", 10_100),
    )
    strategy.generate_signal(
        make_candles(close=10_010, volume=2_000_000),
        make_tick("005930", 10_010),
    )
    first = strategy.generate_signal(
        make_candles(close=10_150, volume=2_000_000, open_price=10_050),
        make_tick("005930", 10_150),
    )
    assert first is not None
    strategy.on_entry()

    # 포지션 보유 중 → 차단
    second = strategy.generate_signal(
        make_candles(close=10_200, volume=2_000_000),
        make_tick("005930", 10_200),
    )
    assert second is None


# ---------------------------------------------------------------------------
# 9. 거래량 부족 → None
# ---------------------------------------------------------------------------

def test_no_signal_low_volume(strategy: MomentumStrategy):
    candles = make_candles(close=10_100, volume=1_000_000)
    tick = make_tick("005930", price=10_100)
    result = strategy.generate_signal(candles, tick)
    assert result is None


# ---------------------------------------------------------------------------
# 10. reset() 상태 초기화
# ---------------------------------------------------------------------------

def test_reset_clears_state(strategy: MomentumStrategy):
    strategy._state = STATE_RETEST
    strategy._breakout_price = 10_100
    strategy._retest_low = 10_010
    strategy.reset()
    assert strategy._state == STATE_WAITING
    assert strategy._breakout_price == 0.0
    assert strategy._retest_low == 0.0
