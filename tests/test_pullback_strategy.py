"""tests/test_pullback_strategy.py — 눌림목 매매 전략 테스트."""

import pytest
import pandas as pd
from unittest.mock import patch

from strategy.pullback_strategy import PullbackStrategy
from config.settings import TradingConfig


OPEN_PRICE = 10000  # 당일 시가


@pytest.fixture
def strategy():
    s = PullbackStrategy(TradingConfig())
    s.set_open_price(OPEN_PRICE)
    return s


def _make_candles(n=25, *, close_values=None, open_values=None, ascending_ma20=True):
    """테스트용 캔들 DataFrame 생성.

    ascending_ma20=True 이면 MA20이 우상향하도록 close를 점진적으로 높임.
    close_values / open_values 가 주어지면 마지막 len(values)개 캔들을 덮어씀.
    """
    if ascending_ma20:
        # 0~n-3 구간은 완만하게 상승 (MA20 정배열 보장)
        base_closes = [10300 + i * 2 for i in range(n)]
    else:
        # MA20 하락 구간
        base_closes = [10300 + (n - i) * 2 for i in range(n)]

    base_opens = [c - 10 for c in base_closes]  # 기본적으로 양봉

    if close_values is not None:
        for i, v in enumerate(close_values):
            base_closes[n - len(close_values) + i] = v
    if open_values is not None:
        for i, v in enumerate(open_values):
            base_opens[n - len(open_values) + i] = v

    return pd.DataFrame({
        "open": base_opens,
        "high": [c + 20 for c in base_closes],
        "low": [c - 20 for c in base_closes],
        "close": base_closes,
        "volume": [1000] * n,
    })


def _tick(price: float, ticker: str = "005930") -> dict:
    return {"ticker": ticker, "price": price, "time": "100000", "volume": 100}


# ──────────────────────────────────────────────
# 1. 시가 대비 상승률 < 3% → None
# ──────────────────────────────────────────────
def test_no_signal_small_gain(strategy):
    """시가 대비 2.9% 상승 — 최소 상승률 미달 → 신호 없음."""
    candles = _make_candles(ascending_ma20=True)
    # MA5 근처 가격이지만 gain이 2.9%
    price = int(OPEN_PRICE * 1.029)
    tick = _tick(price)
    with patch.object(strategy, "is_tradable_time", return_value=True):
        signal = strategy.generate_signal(candles, tick)
    assert signal is None


# ──────────────────────────────────────────────
# 2. 정상 눌림목 조건 → 매수 신호 발생
# ──────────────────────────────────────────────
def test_signal_on_pullback(strategy):
    """모든 조건 충족 시 매수 신호가 발생하고 ticker/side/strategy 가 올바른지 확인."""
    n = 25
    # 마지막 2캔들: 직전=음봉, 현재=양봉
    # close 값을 MA5(마지막 5개 평균)에 가깝게 설정
    # ascending_ma20=True 로 MA20 정배열 보장
    candles = _make_candles(n=n, ascending_ma20=True)

    # MA5 계산 (마지막 5 캔들 평균을 기준으로 현재가 결정)
    ma5 = candles["close"].iloc[-5:].mean()
    # 현재가 = MA5 (거리 0%)
    current_price = ma5

    # 직전 캔들 음봉, 현재 캔들 양봉으로 조정
    candles.at[n - 2, "close"] = candles.at[n - 2, "open"] - 10   # 직전: 음봉
    candles.at[n - 1, "close"] = candles.at[n - 1, "open"] + 10   # 현재: 양봉

    # gain >= 3%: OPEN_PRICE=10000, 현재가 = ma5 ≈ 10340+
    # 기본 base_closes 시작이 10300이어서 충분히 3% 이상
    tick = _tick(float(current_price), ticker="005930")

    with patch.object(strategy, "is_tradable_time", return_value=True):
        signal = strategy.generate_signal(candles, tick)

    assert signal is not None
    assert signal.side == "buy"
    assert signal.ticker == "005930"
    assert signal.strategy == "pullback"


# ──────────────────────────────────────────────
# 3. 음봉→양봉 전환 없음 → None
# ──────────────────────────────────────────────
def test_no_signal_no_reversal(strategy):
    """직전 캔들도 양봉인 경우 — 음봉→양봉 전환 패턴 미충족 → 신호 없음."""
    n = 25
    candles = _make_candles(n=n, ascending_ma20=True)
    ma5 = candles["close"].iloc[-5:].mean()

    # 직전 캔들 양봉 (음봉→양봉 전환 아님)
    candles.at[n - 2, "close"] = candles.at[n - 2, "open"] + 10   # 직전: 양봉
    candles.at[n - 1, "close"] = candles.at[n - 1, "open"] + 10   # 현재: 양봉

    current_price = ma5
    tick = _tick(float(current_price))

    with patch.object(strategy, "is_tradable_time", return_value=True):
        signal = strategy.generate_signal(candles, tick)

    assert signal is None


# ──────────────────────────────────────────────
# 4. 손절가 검증 — -1.5%
# ──────────────────────────────────────────────
def test_stop_loss(strategy):
    """손절가 = 진입가 * (1 + pullback_stop_loss_pct) = -1.5%."""
    entry = 10000.0
    sl = strategy.get_stop_loss(entry)
    expected = entry * (1 + TradingConfig().pullback_stop_loss_pct)
    assert sl == pytest.approx(expected)
    assert sl == pytest.approx(9850.0)


# ──────────────────────────────────────────────
# 보조 테스트: 신호는 1회만 발생
# ──────────────────────────────────────────────
def test_signal_fires_only_once(strategy):
    """동일 조건에서 신호는 1회만 발생해야 한다."""
    n = 25
    candles = _make_candles(n=n, ascending_ma20=True)
    ma5 = candles["close"].iloc[-5:].mean()
    candles.at[n - 2, "close"] = candles.at[n - 2, "open"] - 10
    candles.at[n - 1, "close"] = candles.at[n - 1, "open"] + 10
    tick = _tick(float(ma5))

    with patch.object(strategy, "is_tradable_time", return_value=True):
        sig1 = strategy.generate_signal(candles, tick)
        sig2 = strategy.generate_signal(candles, tick)

    assert sig1 is not None
    assert sig2 is None


# ──────────────────────────────────────────────
# 보조 테스트: MA20 하락 (역배열) → None
# ──────────────────────────────────────────────
def test_no_signal_ma20_descending(strategy):
    """MA20이 하락 중(역배열)이면 신호 없음."""
    n = 25
    candles = _make_candles(n=n, ascending_ma20=False)
    ma5 = candles["close"].iloc[-5:].mean()
    candles.at[n - 2, "close"] = candles.at[n - 2, "open"] - 10
    candles.at[n - 1, "close"] = candles.at[n - 1, "open"] + 10
    tick = _tick(float(ma5))

    with patch.object(strategy, "is_tradable_time", return_value=True):
        signal = strategy.generate_signal(candles, tick)

    assert signal is None


# ──────────────────────────────────────────────
# 보조 테스트: 익절가 검증
# ──────────────────────────────────────────────
def test_take_profit(strategy):
    """익절 tp1 = 진입가 * (1 + tp1_pct) = +2%, tp2 = 0."""
    entry = 10000.0
    tp1, tp2 = strategy.get_take_profit(entry)
    assert tp1 == pytest.approx(entry * (1 + TradingConfig().tp1_pct))
    assert tp2 == 0


# ──────────────────────────────────────────────
# 보조 테스트: reset() 후 재진입 가능
# ──────────────────────────────────────────────
def test_reset_allows_new_signal(strategy):
    """reset() 후 open_price 재설정 시 신호 재발생 가능."""
    n = 25
    candles = _make_candles(n=n, ascending_ma20=True)
    ma5 = candles["close"].iloc[-5:].mean()
    candles.at[n - 2, "close"] = candles.at[n - 2, "open"] - 10
    candles.at[n - 1, "close"] = candles.at[n - 1, "open"] + 10
    tick = _tick(float(ma5))

    with patch.object(strategy, "is_tradable_time", return_value=True):
        sig1 = strategy.generate_signal(candles, tick)
        assert sig1 is not None

        strategy.reset()
        strategy.set_open_price(OPEN_PRICE)
        sig2 = strategy.generate_signal(candles, tick)
        assert sig2 is not None
