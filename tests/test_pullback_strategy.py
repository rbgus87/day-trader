"""tests/test_pullback_strategy.py — 눌림목 매매 전략 v2 테스트."""

import pytest
import pandas as pd
from unittest.mock import patch

from strategy.pullback_strategy import PullbackStrategy
from config.settings import TradingConfig


OPEN_PRICE = 9700  # 당일 시가 (min_gain_pct=2.5% 기준: MA10 ~10000 → gain ~3.1%)


@pytest.fixture
def strategy():
    s = PullbackStrategy(TradingConfig())
    s.set_open_price(OPEN_PRICE)
    s.configure_multi_trade(max_trades=5, cooldown_minutes=0)
    return s


def _make_candles(n=15, *, close_values=None, ascending_ma=True, include_ohlc=True):
    """테스트용 캔들 DataFrame 생성 (MA10 기반)."""
    if ascending_ma:
        base_closes = [10000 + i * 5 for i in range(n)]
    else:
        base_closes = [10000 + (n - i) * 5 for i in range(n)]

    base_opens = [c - 10 for c in base_closes]

    if close_values is not None:
        for i, v in enumerate(close_values):
            base_closes[n - len(close_values) + i] = v

    data = {
        "open": base_opens,
        "close": base_closes,
    }
    if include_ohlc:
        data["high"] = [c + 200 for c in base_closes]  # 변동폭 ~4% (ATR 필터 통과)
        data["low"] = [c - 200 for c in base_closes]

    return pd.DataFrame(data)


def _tick(price: float, ticker: str = "005930") -> dict:
    return {"ticker": ticker, "price": price, "time": "100000", "volume": 100}


# 1. 시가 대비 상승 미달
def test_no_signal_small_gain(strategy):
    """시가 대비 2.4% 상승 — 최소 상승률(2.5%) 미달 → 신호 없음."""
    candles = _make_candles(ascending_ma=True)
    price = int(OPEN_PRICE * 1.024)
    with patch.object(strategy, "is_tradable_time", return_value=True):
        signal = strategy.generate_signal(candles, _tick(price))
    assert signal is None


# 2. 정상 눌림목 조건 → 매수 신호
def test_signal_on_pullback(strategy):
    n = 15
    candles = _make_candles(n=n, ascending_ma=True)
    ma10 = candles["close"].iloc[-10:].mean()

    # 직전 음봉, 현재 양봉
    candles.at[n - 2, "close"] = candles.at[n - 2, "open"] - 10
    candles.at[n - 1, "close"] = candles.at[n - 1, "open"] + 10

    current_price = ma10
    with patch.object(strategy, "is_tradable_time", return_value=True):
        signal = strategy.generate_signal(candles, _tick(float(current_price)))
    assert signal is not None
    assert signal.side == "buy"
    assert signal.strategy == "pullback"


# 3. 음봉→양봉 전환 없음
def test_no_signal_no_reversal(strategy):
    n = 15
    candles = _make_candles(n=n, ascending_ma=True)
    ma10 = candles["close"].iloc[-10:].mean()
    candles.at[n - 2, "close"] = candles.at[n - 2, "open"] + 10  # 양봉
    candles.at[n - 1, "close"] = candles.at[n - 1, "open"] + 10  # 양봉
    with patch.object(strategy, "is_tradable_time", return_value=True):
        signal = strategy.generate_signal(candles, _tick(float(ma10)))
    assert signal is None


# 4. 손절가 검증 (-1.8%)
def test_stop_loss(strategy):
    entry = 10000.0
    sl = strategy.get_stop_loss(entry)
    expected = entry * (1 + TradingConfig().pullback_stop_loss_pct)
    assert sl == pytest.approx(expected)
    assert sl == pytest.approx(9820.0)


# 5. 포지션 보유 중 신호 차단
def test_no_signal_while_in_position(strategy):
    n = 15
    candles = _make_candles(n=n, ascending_ma=True)
    ma10 = candles["close"].iloc[-10:].mean()
    candles.at[n - 2, "close"] = candles.at[n - 2, "open"] - 10
    candles.at[n - 1, "close"] = candles.at[n - 1, "open"] + 10
    tick = _tick(float(ma10))

    with patch.object(strategy, "is_tradable_time", return_value=True):
        sig1 = strategy.generate_signal(candles, tick)
        assert sig1 is not None
        strategy.on_entry()
        sig2 = strategy.generate_signal(candles, tick)
        assert sig2 is None
        strategy.on_exit()
        sig3 = strategy.generate_signal(candles, tick)
        assert sig3 is not None


# 6. MA 역배열 → None
def test_no_signal_ma_descending(strategy):
    n = 15
    candles = _make_candles(n=n, ascending_ma=False)
    ma10 = candles["close"].iloc[-10:].mean()
    candles.at[n - 2, "close"] = candles.at[n - 2, "open"] - 10
    candles.at[n - 1, "close"] = candles.at[n - 1, "open"] + 10
    with patch.object(strategy, "is_tradable_time", return_value=True):
        signal = strategy.generate_signal(candles, _tick(float(ma10)))
    assert signal is None


# 7. 익절가 검증 (+3%)
def test_take_profit(strategy):
    entry = 10000.0
    tp1, tp2 = strategy.get_take_profit(entry)
    assert tp1 == pytest.approx(entry * (1 + TradingConfig().tp1_pct))
    assert tp2 == 0


# 8. reset 후 재진입
def test_reset_allows_new_signal(strategy):
    n = 15
    candles = _make_candles(n=n, ascending_ma=True)
    ma10 = candles["close"].iloc[-10:].mean()
    candles.at[n - 2, "close"] = candles.at[n - 2, "open"] - 10
    candles.at[n - 1, "close"] = candles.at[n - 1, "open"] + 10
    tick = _tick(float(ma10))

    with patch.object(strategy, "is_tradable_time", return_value=True):
        sig1 = strategy.generate_signal(candles, tick)
        assert sig1 is not None
        strategy.reset()
        strategy.set_open_price(OPEN_PRICE)
        sig2 = strategy.generate_signal(candles, tick)
        assert sig2 is not None
