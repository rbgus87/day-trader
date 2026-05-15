"""tests/test_gap_pullback_strategy.py — GapPullbackStrategy 단위 테스트."""

from datetime import time

import pandas as pd
import pytest

from config.settings import TradingConfig
from strategy.gap_pullback_strategy import GapPullbackStrategy


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def make_config(**overrides) -> TradingConfig:
    defaults = dict(
        gap_pullback_enabled=True,
        gap_pullback_min_pct=0.02,
        gap_pullback_max_pct=0.08,
        gap_pullback_min_pullback_pct=0.01,
        gap_pullback_max_pullback_pct=0.03,
        gap_pullback_entry_start="09:00",
        gap_pullback_entry_end="09:20",
        gap_pullback_force_close="09:45",
        gap_pullback_volume_ratio=1.5,
        gap_pullback_atr_stop_mult=0.5,
    )
    defaults.update(overrides)
    return TradingConfig(**defaults)


def make_candles(prices: list[float], volume: int = 100_000) -> pd.DataFrame:
    """가격 목록으로 캔들 생성. 마지막 가격이 현재가."""
    rows = []
    for i, p in enumerate(prices):
        rows.append({
            "open": p, "high": p * 1.002, "low": p * 0.998,
            "close": p, "volume": volume,
        })
    return pd.DataFrame(rows)


def make_tick(ticker: str, price: float) -> dict:
    return {"ticker": ticker, "price": price}


@pytest.fixture
def strategy() -> GapPullbackStrategy:
    cfg = make_config()
    strat = GapPullbackStrategy(cfg)
    # 전일 종가 9_000 → 당일 갭업 시가 9_360 (갭 4%)
    strat.set_prev_close(9_000)
    strat.set_open_price(9_360)
    strat.set_prev_day_data(0, 1_000_000, 9_000)
    strat.set_backtest_time(time(9, 10))  # 09:10 — 진입 시간창 내
    strat.configure_multi_trade(max_trades=5, cooldown_minutes=0)
    return strat


# ---------------------------------------------------------------------------
# 시간창 테스트
# ---------------------------------------------------------------------------

def test_no_signal_before_entry_window(strategy):
    strategy.set_backtest_time(time(8, 59))
    candles = make_candles([9_360, 9_260])  # 눌림 후 반등
    tick = make_tick("005930", 9_270)
    assert strategy.generate_signal(candles, tick) is None


def test_no_signal_after_entry_window(strategy):
    strategy.set_backtest_time(time(9, 21))
    candles = make_candles([9_360, 9_260])
    tick = make_tick("005930", 9_270)
    assert strategy.generate_signal(candles, tick) is None


def test_signal_within_entry_window(strategy):
    # 갭 4%, 눌림 ~1.07%, 반등 확인
    candles = make_candles([9_360, 9_260, 9_265])
    tick = make_tick("005930", 9_265)
    sig = strategy.generate_signal(candles, tick)
    assert sig is not None
    assert sig.side == "buy"
    assert sig.strategy == "gap_pullback"


# ---------------------------------------------------------------------------
# 갭 조건 테스트
# ---------------------------------------------------------------------------

def test_no_signal_gap_too_small(strategy):
    # 갭 1% (min 2% 미달)
    strategy.set_open_price(9_090)  # gap = 1%
    candles = make_candles([9_090, 9_000])
    tick = make_tick("005930", 9_005)
    assert strategy.generate_signal(candles, tick) is None
    assert strategy.diag_counters["gap_too_small"] > 0


def test_no_signal_gap_too_large(strategy):
    # 갭 10% (max 8% 초과)
    strategy.set_open_price(9_900)  # gap = 10%
    candles = make_candles([9_900, 9_800])
    tick = make_tick("005930", 9_810)
    assert strategy.generate_signal(candles, tick) is None
    assert strategy.diag_counters["gap_too_large"] > 0


# ---------------------------------------------------------------------------
# 눌림 조건 테스트
# ---------------------------------------------------------------------------

def test_no_signal_pullback_too_small(strategy):
    # 눌림 0.3% (min 1% 미달)
    tick = make_tick("005930", 9_332)  # pullback = 0.3%
    candles = make_candles([9_360, 9_330])
    assert strategy.generate_signal(candles, tick) is None
    assert strategy.diag_counters["pullback_too_small"] > 0


def test_no_signal_pullback_too_large(strategy):
    # 눌림 4% (max 3% 초과)
    tick = make_tick("005930", 9_000)  # pullback ≈ 3.8%
    candles = make_candles([9_360, 9_100, 9_010])
    assert strategy.generate_signal(candles, tick) is None
    assert strategy.diag_counters["pullback_too_large"] > 0


# ---------------------------------------------------------------------------
# 반등 확인 테스트
# ---------------------------------------------------------------------------

def test_no_signal_no_bounce(strategy):
    # 현재가 ≤ 직전 완성 캔들 저가 → 반등 미확인
    # 직전 완성 캔들(iloc[-1]) low=9_250, current=9_240 → bounce_fail
    candles = pd.DataFrame([
        {"open": 9_360, "high": 9_370, "low": 9_350, "close": 9_360, "volume": 50_000},
        {"open": 9_255, "high": 9_260, "low": 9_250, "close": 9_255, "volume": 50_000},
    ])
    tick = make_tick("005930", 9_240)  # pullback=(9360-9240)/9360=1.28%, low=9250 초과 × → bounce_fail
    assert strategy.generate_signal(candles, tick) is None
    assert strategy.diag_counters["bounce_fail"] > 0


def test_signal_with_bounce(strategy):
    # open=9360, 눌림 1.07%(9260/9360), 직전 저가 9250 → 9260 > 9250 (반등)
    candles = pd.DataFrame([
        {"open": 9_360, "high": 9_370, "low": 9_350, "close": 9_360, "volume": 50_000},
        {"open": 9_255, "high": 9_260, "low": 9_250, "close": 9_255, "volume": 50_000},
    ])
    tick = make_tick("005930", 9_260)  # pullback=(9360-9260)/9360=1.07%, prev_low=9250 → OK
    sig = strategy.generate_signal(candles, tick)
    assert sig is not None
    assert sig.strategy == "gap_pullback"


# ---------------------------------------------------------------------------
# 데이터 미설정 테스트
# ---------------------------------------------------------------------------

def test_no_signal_without_prev_close(strategy):
    strategy.set_prev_close(0)
    candles = make_candles([9_360, 9_260])
    tick = make_tick("005930", 9_270)
    assert strategy.generate_signal(candles, tick) is None
    assert strategy.diag_counters["prev_data_missing"] > 0


def test_no_signal_without_open_price(strategy):
    strategy.set_open_price(0)
    candles = make_candles([9_360, 9_260])
    tick = make_tick("005930", 9_270)
    assert strategy.generate_signal(candles, tick) is None
    assert strategy.diag_counters["prev_data_missing"] > 0


# ---------------------------------------------------------------------------
# 포지션 상태 테스트
# ---------------------------------------------------------------------------

def test_no_signal_while_in_position(strategy):
    candles = pd.DataFrame([
        {"open": 9_360, "high": 9_370, "low": 9_350, "close": 9_360, "volume": 50_000},
        {"open": 9_255, "high": 9_260, "low": 9_250, "close": 9_255, "volume": 50_000},
    ])
    tick = make_tick("005930", 9_260)  # pullback=1.07%, prev_low=9250 → OK
    sig = strategy.generate_signal(candles, tick)
    assert sig is not None
    strategy.on_entry()
    assert strategy.generate_signal(candles, tick) is None
    strategy.on_exit()
    assert strategy.generate_signal(candles, tick) is not None


# ---------------------------------------------------------------------------
# 청산 가격 테스트
# ---------------------------------------------------------------------------

def test_take_profit_is_open_price(strategy):
    tp = strategy.get_take_profit(9_200)
    assert tp == pytest.approx(9_360)  # open_price


def test_take_profit_fallback_when_open_below_entry(strategy):
    strategy.set_open_price(9_000)  # open < entry
    tp = strategy.get_take_profit(9_200)
    assert tp == pytest.approx(9_200 * 1.02)


def test_stop_loss_without_ticker(strategy):
    strategy._pullback_low = 9_200.0
    sl = strategy.get_stop_loss(9_270)
    # ATR 없음 → pullback_low × 0.995 or fallback (9270 × 0.97)
    fallback = 9_270 * 0.97
    pullback_sl = 9_200 * 0.995
    assert sl >= fallback or sl == pytest.approx(pullback_sl, rel=0.01)


# ---------------------------------------------------------------------------
# 강제 청산 시각 테스트
# ---------------------------------------------------------------------------

def test_force_close_time_parsed():
    cfg = make_config(gap_pullback_force_close="09:45")
    strat = GapPullbackStrategy(cfg)
    assert strat._force_close_time == time(9, 45)


def test_is_tradable_time_from_0900(strategy):
    strategy.set_backtest_time(time(9, 0))
    assert strategy.is_tradable_time() is True


def test_not_tradable_before_0900(strategy):
    strategy.set_backtest_time(time(8, 59))
    assert strategy.is_tradable_time() is False


# ---------------------------------------------------------------------------
# 리셋 테스트
# ---------------------------------------------------------------------------

def test_reset_clears_pullback_low(strategy):
    strategy._pullback_low = 9_100.0
    strategy.reset()
    assert strategy._pullback_low == 0.0


def test_reset_keeps_prev_close_and_open(strategy):
    strategy.reset()
    # _prev_close, _open_price는 setup에서 덮어쓰므로 reset에서 건드리지 않음
    assert strategy._prev_close == 9_000  # 변경 없음
    assert strategy._open_price == 9_360  # 변경 없음


# ---------------------------------------------------------------------------
# 컨텍스트 정보 테스트
# ---------------------------------------------------------------------------

def test_signal_context_contains_gap_info(strategy):
    candles = pd.DataFrame([
        {"open": 9_360, "high": 9_370, "low": 9_350, "close": 9_360, "volume": 50_000},
        {"open": 9_255, "high": 9_260, "low": 9_250, "close": 9_255, "volume": 50_000},
    ])
    tick = make_tick("005930", 9_260)  # pullback=1.07%, prev_low=9250 → OK
    sig = strategy.generate_signal(candles, tick)
    assert sig is not None
    assert "gap_pct" in sig.context
    assert "pullback_pct" in sig.context
    assert "open_price" in sig.context
    assert sig.context["gap_pct"] == pytest.approx(0.04, rel=0.01)
