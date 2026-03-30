"""tests/test_big_candle_strategy.py — BigCandleStrategy 단위 테스트."""

from datetime import time

import pandas as pd
import pytest

from config.settings import TradingConfig
from strategy.big_candle_strategy import BigCandleStrategy, STATE_SCANNING, STATE_PULLBACK


def make_config(**overrides) -> TradingConfig:
    defaults = dict(
        big_candle_atr_multiplier=1.5,
        big_candle_timeout_min=30,
        big_candle_stop_loss_pct=-0.01,
        tp1_pct=0.03,
    )
    defaults.update(overrides)
    return TradingConfig(**defaults)


def make_tick(price: float, ticker: str = "005930") -> dict:
    return {"ticker": ticker, "price": price}


def _build_candles_with_big_and_pullback(*, include_pullback=True, bearish_big=False, timeout=False):
    """세력 캔들 + 되돌림 + 반등 시나리오를 위한 캔들 데이터 생성.

    12개의 일반 캔들 (ATR 계산용) + 1개 세력 캔들 + 되돌림/반등 캔들.
    """
    rows = []
    # 10개의 안정적 캔들 (ATR ~20 정도)
    for i in range(12):
        base = 10000 + i * 5
        rows.append({"open": base, "high": base + 10, "low": base - 10, "close": base + 5, "volume": 1000})

    # 세력 캔들: body = 50 > ATR(~20) × 1.5 = 30
    if bearish_big:
        # 큰 음봉
        rows.append({"open": 10100, "high": 10110, "low": 10040, "close": 10050, "volume": 5000})
    else:
        # 큰 양봉: open=10060, close=10160 → body=100 >> ATR*1.5
        rows.append({"open": 10060, "high": 10170, "low": 10055, "close": 10160, "volume": 5000})

    if include_pullback and not bearish_big:
        if timeout:
            # 타임아웃: 31개의 추가 캔들 (timeout_min=30 초과)
            for i in range(31):
                rows.append({"open": 10150, "high": 10155, "low": 10145, "close": 10150, "volume": 1000})
            # 되돌림 + 양봉 (하지만 이미 타임아웃)
            rows.append({"open": 10100, "high": 10120, "low": 10090, "close": 10110, "volume": 1000})
        else:
            # 되돌림: low <= mid (mid ≈ (10170+10055)/2 ≈ 10112.5)
            rows.append({"open": 10130, "high": 10135, "low": 10100, "close": 10105, "volume": 1000})
            # 반등 양봉: close > open
            rows.append({"open": 10105, "high": 10130, "low": 10100, "close": 10125, "volume": 1000})

    return pd.DataFrame(rows)


@pytest.fixture
def strategy() -> BigCandleStrategy:
    cfg = make_config()
    strat = BigCandleStrategy(cfg)
    strat.configure_multi_trade(max_trades=5, cooldown_minutes=0)
    strat.set_backtest_time(time(10, 0))
    return strat


def test_signal_on_big_candle_pullback_bounce(strategy):
    """큰 양봉 → 되돌림 → 반등 양봉 → 신호."""
    candles = _build_candles_with_big_and_pullback()

    # 세력 캔들까지 (13행): 스캔 → PULLBACK 전환
    for i in range(13):
        sub = candles.iloc[:i+1]
        signal = strategy.generate_signal(sub, make_tick(float(sub.iloc[-1]["close"])))
        assert signal is None  # 아직 신호 없음

    assert strategy._state == STATE_PULLBACK

    # 되돌림 캔들 (14번째)
    sub = candles.iloc[:14]
    signal = strategy.generate_signal(sub, make_tick(float(sub.iloc[-1]["close"])))
    assert signal is None  # 되돌림만, 양봉 아님

    # 반등 양봉 (15번째) → 신호!
    sub = candles.iloc[:15]
    signal = strategy.generate_signal(sub, make_tick(float(sub.iloc[-1]["close"])))
    assert signal is not None
    assert signal.side == "buy"
    assert signal.strategy == "big_candle"


def test_timeout_no_signal(strategy):
    """큰 양봉 → 30분 초과 → 타임아웃 → 신호 없음."""
    candles = _build_candles_with_big_and_pullback(timeout=True)

    # 모든 캔들 순회
    signal = None
    for i in range(len(candles)):
        sub = candles.iloc[:i+1]
        signal = strategy.generate_signal(sub, make_tick(float(sub.iloc[-1]["close"])))

    # 타임아웃으로 리셋되어 신호 없음
    assert signal is None
    assert strategy._state == STATE_SCANNING


def test_bearish_big_candle_ignored(strategy):
    """큰 음봉(하락) → 무시."""
    candles = _build_candles_with_big_and_pullback(include_pullback=False, bearish_big=True)

    for i in range(len(candles)):
        sub = candles.iloc[:i+1]
        signal = strategy.generate_signal(sub, make_tick(float(sub.iloc[-1]["close"])))
        assert signal is None

    assert strategy._state == STATE_SCANNING


def test_stop_loss(strategy):
    sl = strategy.get_stop_loss(10_000)
    assert sl == pytest.approx(10_000 * (1 + strategy._config.big_candle_stop_loss_pct))


def test_reset(strategy):
    strategy._state = STATE_PULLBACK
    strategy._big_candle_high = 100
    strategy.reset()
    assert strategy._state == STATE_SCANNING
    assert strategy._big_candle_high == 0.0
