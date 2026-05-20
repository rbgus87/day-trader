"""tests/test_orb_strategy.py — ORB 전략 단위 테스트."""
import dataclasses
from datetime import datetime, time

import pandas as pd
import pytest

from config.settings import TradingConfig
from strategy.orb_strategy import ORBStrategy


# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------

@pytest.fixture
def base_cfg() -> TradingConfig:
    return dataclasses.replace(
        TradingConfig(),
        orb_enabled=True,
        orb_range_minutes=5,
        orb_min_range_pct=0.005,
        orb_max_range_pct=0.05,
        orb_breakout_buffer=0.0,
        orb_entry_deadline="10:00",
        orb_sl_ratio=1.0,
        orb_tp_ratio=2.0,
        orb_use_volume_filter=False,  # 기본 테스트에서 비활성
        orb_rvol_min=1.5,
        max_trades_per_day=1,
        cooldown_minutes=0,
    )


def _make_candles(data: list[dict]) -> pd.DataFrame:
    """간단한 캔들 DataFrame 생성 헬퍼."""
    df = pd.DataFrame(data)
    df["ts"] = pd.to_datetime(df["ts"])
    return df


def _make_range_candles(range_high: float = 10_100, range_low: float = 9_900) -> pd.DataFrame:
    """09:00~09:04 레인지 분봉 + 09:05 이후 가격 상승 캔들."""
    candles = []
    # 09:00~09:04 — 레인지 설정
    for m in range(0, 5):
        ts = f"2026-01-02 09:0{m}:00"
        candles.append({
            "ts": ts, "open": 10_000, "high": range_high,
            "low": range_low, "close": 10_000, "volume": 5000, "vwap": 10_000,
        })
    return _make_candles(candles)


# ---------------------------------------------------------------------------
# 레인지 계산 테스트
# ---------------------------------------------------------------------------

def test_range_computed_on_generate_signal(base_cfg):
    strat = ORBStrategy(base_cfg)
    strat.set_prev_day_data(high=9_500, volume=100_000, close=9_800)

    # 레인지: 09:00~09:04 H=10100 L=9900 → size=200, pct=2.04%
    range_candles = _make_range_candles()

    tick = {"ticker": "TEST", "price": 10_200, "volume": 0}
    strat.set_backtest_time(time(9, 5))

    sig = strat.generate_signal(range_candles, tick)
    assert strat._range_valid
    assert strat._range_high == 10_100
    assert strat._range_low == 9_900
    assert strat._range_size == pytest.approx(200.0)


def test_range_too_narrow_no_signal(base_cfg):
    """레인지 < min_range_pct (0.5%) → 신호 없음."""
    strat = ORBStrategy(dataclasses.replace(base_cfg, orb_min_range_pct=0.01))
    strat.set_prev_day_data(high=9_500, volume=100_000, close=10_000)

    # range_size=10, pct=0.1% < min=1%
    candles = _make_range_candles(range_high=10_005, range_low=9_995)
    tick = {"ticker": "TEST", "price": 10_006, "volume": 0}
    strat.set_backtest_time(time(9, 5))

    sig = strat.generate_signal(candles, tick)
    assert not strat._range_valid
    assert sig is None


def test_range_too_wide_no_signal(base_cfg):
    """레인지 > max_range_pct (5%) → 신호 없음."""
    strat = ORBStrategy(dataclasses.replace(base_cfg, orb_max_range_pct=0.03))
    strat.set_prev_day_data(high=9_500, volume=100_000, close=10_000)

    # range_size=600, pct=6% > max=3%
    candles = _make_range_candles(range_high=10_300, range_low=9_700)
    tick = {"ticker": "TEST", "price": 10_350, "volume": 0}
    strat.set_backtest_time(time(9, 5))

    sig = strat.generate_signal(candles, tick)
    assert not strat._range_valid
    assert sig is None


# ---------------------------------------------------------------------------
# 진입 조건 테스트
# ---------------------------------------------------------------------------

def test_signal_on_breakout(base_cfg):
    strat = ORBStrategy(base_cfg)
    strat.set_prev_day_data(high=9_500, volume=100_000, close=9_800)
    candles = _make_range_candles()  # H=10100 L=9900 size=200

    # price=10110 > range_high(10100) → 돌파 신호
    tick = {"ticker": "TEST", "price": 10_110, "volume": 0}
    strat.set_backtest_time(time(9, 5))

    sig = strat.generate_signal(candles, tick)
    assert sig is not None
    assert sig.side == "buy"
    assert sig.strategy == "orb"


def test_no_signal_below_breakout(base_cfg):
    strat = ORBStrategy(base_cfg)
    strat.set_prev_day_data(high=9_500, volume=100_000, close=9_800)
    candles = _make_range_candles()

    # price=10050 < range_high(10100) → 돌파 미달
    tick = {"ticker": "TEST", "price": 10_050, "volume": 0}
    strat.set_backtest_time(time(9, 5))

    sig = strat.generate_signal(candles, tick)
    assert sig is None


def test_no_signal_before_block_until(base_cfg):
    """09:05 이전 신호 차단."""
    strat = ORBStrategy(base_cfg)
    strat.set_prev_day_data(high=9_500, volume=100_000, close=9_800)
    candles = _make_range_candles()

    tick = {"ticker": "TEST", "price": 10_200, "volume": 0}
    strat.set_backtest_time(time(9, 4))  # 09:04 — 아직 차단 시간

    sig = strat.generate_signal(candles, tick)
    assert sig is None


def test_no_signal_after_deadline(base_cfg):
    """entry_deadline(10:00) 이후 진입 차단."""
    strat = ORBStrategy(base_cfg)
    strat.set_prev_day_data(high=9_500, volume=100_000, close=9_800)
    candles = _make_range_candles()

    tick = {"ticker": "TEST", "price": 10_200, "volume": 0}
    strat.set_backtest_time(time(10, 1))  # 10:01 — deadline 초과

    sig = strat.generate_signal(candles, tick)
    assert sig is None


def test_breakout_buffer_applied(base_cfg):
    """breakout_buffer=0.5 → threshold = range_high + range_size*0.5."""
    strat = ORBStrategy(dataclasses.replace(base_cfg, orb_breakout_buffer=0.5))
    strat.set_prev_day_data(high=9_500, volume=100_000, close=9_800)
    candles = _make_range_candles()  # H=10100 L=9900 size=200

    # threshold = 10100 + 200*0.5 = 10200
    tick_below = {"ticker": "TEST", "price": 10_150, "volume": 0}
    tick_above = {"ticker": "TEST", "price": 10_250, "volume": 0}
    strat.set_backtest_time(time(9, 5))

    sig_below = strat.generate_signal(candles, tick_below)
    strat.reset()
    strat.set_prev_day_data(high=9_500, volume=100_000, close=9_800)
    strat.set_backtest_time(time(9, 5))
    sig_above = strat.generate_signal(candles, tick_above)

    assert sig_below is None
    assert sig_above is not None


# ---------------------------------------------------------------------------
# 손절 / 익절 테스트
# ---------------------------------------------------------------------------

def test_stop_loss_calculation(base_cfg):
    """stop = entry - range_size * sl_ratio, 하한: range_low * 0.99."""
    strat = ORBStrategy(base_cfg)
    strat.set_prev_day_data(high=9_500, volume=100_000, close=9_800)
    candles = _make_range_candles()  # H=10100 L=9900 size=200
    tick = {"ticker": "TEST", "price": 10_110, "volume": 0}
    strat.set_backtest_time(time(9, 5))
    strat.generate_signal(candles, tick)  # 레인지 계산 트리거

    entry = 10_110
    sl = strat.get_stop_loss(entry)
    # sl_ratio=1.0 → stop = 10110 - 200 = 9910
    # range_low*0.99 = 9900*0.99 = 9801 → max(9910, 9801) = 9910
    assert sl == pytest.approx(9_910.0)


def test_stop_loss_clamped_to_range_low(base_cfg):
    """sl_ratio=3.0 → 손절이 range_low*0.99 아래 → 클램프."""
    strat = ORBStrategy(dataclasses.replace(base_cfg, orb_sl_ratio=3.0))
    strat.set_prev_day_data(high=9_500, volume=100_000, close=9_800)
    candles = _make_range_candles()  # H=10100 L=9900 size=200
    tick = {"ticker": "TEST", "price": 10_110, "volume": 0}
    strat.set_backtest_time(time(9, 5))
    strat.generate_signal(candles, tick)

    entry = 10_110
    sl = strat.get_stop_loss(entry)
    # sl_ratio=3.0 → 10110 - 200*3 = 9510 < range_low*0.99=9801 → clamp to 9801
    assert sl == pytest.approx(9_900 * 0.99)


def test_take_profit_calculation(base_cfg):
    strat = ORBStrategy(base_cfg)
    strat.set_prev_day_data(high=9_500, volume=100_000, close=9_800)
    candles = _make_range_candles()  # size=200
    tick = {"ticker": "TEST", "price": 10_110, "volume": 0}
    strat.set_backtest_time(time(9, 5))
    strat.generate_signal(candles, tick)

    entry = 10_110
    tp = strat.get_take_profit(entry)
    # tp_ratio=2.0 → tp = 10110 + 200*2 = 10510
    assert tp == pytest.approx(10_510.0)


# ---------------------------------------------------------------------------
# 거래량 필터 테스트
# ---------------------------------------------------------------------------

def test_volume_filter_passes(base_cfg):
    strat = ORBStrategy(dataclasses.replace(base_cfg, orb_use_volume_filter=True, orb_rvol_min=1.5))
    strat.set_prev_day_data(high=9_500, volume=100_000, close=9_800)
    strat.set_prev_day_volume(100_000)

    # 누적 거래량 200,000 >= 100,000 * 1.5 = 150,000
    range_c = _make_range_candles()
    extra = _make_candles([{
        "ts": "2026-01-02 09:05:00",
        "open": 10_000, "high": 10_200, "low": 10_000, "close": 10_200,
        "volume": 175_000, "vwap": 10_100,
    }])
    candles = pd.concat([range_c, extra], ignore_index=True)

    tick = {"ticker": "TEST", "price": 10_200, "volume": 0}
    strat.set_backtest_time(time(9, 5))

    sig = strat.generate_signal(candles, tick)
    assert sig is not None


def test_volume_filter_blocks(base_cfg):
    strat = ORBStrategy(dataclasses.replace(base_cfg, orb_use_volume_filter=True, orb_rvol_min=1.5))
    strat.set_prev_day_data(high=9_500, volume=100_000, close=9_800)
    strat.set_prev_day_volume(100_000)

    # 누적 거래량 25000+110=25110 < 150,000
    candles = _make_range_candles()  # volume=5000*5=25000 total

    tick = {"ticker": "TEST", "price": 10_200, "volume": 0}
    strat.set_backtest_time(time(9, 5))

    sig = strat.generate_signal(candles, tick)
    assert sig is None


# ---------------------------------------------------------------------------
# reset 테스트
# ---------------------------------------------------------------------------

def test_reset_clears_range(base_cfg):
    strat = ORBStrategy(base_cfg)
    strat.set_prev_day_data(high=9_500, volume=100_000, close=9_800)
    candles = _make_range_candles()
    tick = {"ticker": "TEST", "price": 10_200, "volume": 0}
    strat.set_backtest_time(time(9, 5))
    strat.generate_signal(candles, tick)

    assert strat._range_valid
    strat.reset()
    assert not strat._range_valid
    assert not strat._range_computed
    assert strat._range_size == 0.0


# ---------------------------------------------------------------------------
# ORBFastBacktester 통합 테스트
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orb_fast_backtester_tp_exit(base_cfg):
    """TP 도달 시 tp_exit 청산."""
    from backtest.backtester_fast import ORBFastBacktester
    from config.settings import BacktestConfig

    cfg = dataclasses.replace(base_cfg, orb_sl_ratio=1.0, orb_tp_ratio=2.0)
    bt_cfg = BacktestConfig()
    bt = ORBFastBacktester(
        db=None, config=cfg, backtest_config=bt_cfg,
        ticker_market="kosdaq", market_strong_by_date={},
    )

    # 레인지: 09:00~09:04 H=10100 L=9900 size=200
    # 09:05: close=10110 → 진입 (price=10110)
    # 09:06: high=10520 >= tp=10110+400=10510 → TP 청산
    candles_data = [
        {"ts": "2026-01-02 09:00:00", "open": 10000, "high": 10100, "low": 9900, "close": 10000, "volume": 5000, "vwap": 10000},
        {"ts": "2026-01-02 09:01:00", "open": 10000, "high": 10100, "low": 9900, "close": 10000, "volume": 5000, "vwap": 10000},
        {"ts": "2026-01-02 09:02:00", "open": 10000, "high": 10100, "low": 9900, "close": 10000, "volume": 5000, "vwap": 10000},
        {"ts": "2026-01-02 09:03:00", "open": 10000, "high": 10100, "low": 9900, "close": 10000, "volume": 5000, "vwap": 10000},
        {"ts": "2026-01-02 09:04:00", "open": 10000, "high": 10100, "low": 9900, "close": 10000, "volume": 5000, "vwap": 10000},
        {"ts": "2026-01-02 09:05:00", "open": 10100, "high": 10200, "low": 10000, "close": 10110, "volume": 50000, "vwap": 10110},
        {"ts": "2026-01-02 09:06:00", "open": 10110, "high": 10520, "low": 10100, "close": 10500, "volume": 20000, "vwap": 10400},
    ]
    df = pd.DataFrame(candles_data)
    df["ts"] = pd.to_datetime(df["ts"])

    strat = ORBStrategy(cfg)
    strat.set_prev_day_data(high=9500, volume=100000, close=9800)

    result = bt.run_backtest(df, strat)
    trades = result["trades"]

    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "tp_exit"
    assert trades[0]["pnl"] > 0


@pytest.mark.asyncio
async def test_orb_fast_backtester_stop_loss(base_cfg):
    """손절 도달 시 stop_loss 청산."""
    from backtest.backtester_fast import ORBFastBacktester
    from config.settings import BacktestConfig

    cfg = dataclasses.replace(base_cfg, orb_sl_ratio=1.0, orb_tp_ratio=10.0)
    bt_cfg = BacktestConfig()
    bt = ORBFastBacktester(
        db=None, config=cfg, backtest_config=bt_cfg,
        ticker_market="kosdaq", market_strong_by_date={},
    )

    candles_data = [
        {"ts": "2026-01-02 09:00:00", "open": 10000, "high": 10100, "low": 9900, "close": 10000, "volume": 5000, "vwap": 10000},
        {"ts": "2026-01-02 09:01:00", "open": 10000, "high": 10100, "low": 9900, "close": 10000, "volume": 5000, "vwap": 10000},
        {"ts": "2026-01-02 09:02:00", "open": 10000, "high": 10100, "low": 9900, "close": 10000, "volume": 5000, "vwap": 10000},
        {"ts": "2026-01-02 09:03:00", "open": 10000, "high": 10100, "low": 9900, "close": 10000, "volume": 5000, "vwap": 10000},
        {"ts": "2026-01-02 09:04:00", "open": 10000, "high": 10100, "low": 9900, "close": 10000, "volume": 5000, "vwap": 10000},
        # 09:05: 진입 close=10110
        {"ts": "2026-01-02 09:05:00", "open": 10100, "high": 10200, "low": 10000, "close": 10110, "volume": 50000, "vwap": 10110},
        # 09:06: 손절 — low=9890 < stop=10110-200=9910
        {"ts": "2026-01-02 09:06:00", "open": 10110, "high": 10110, "low": 9890, "close": 9950, "volume": 20000, "vwap": 10000},
    ]
    df = pd.DataFrame(candles_data)
    df["ts"] = pd.to_datetime(df["ts"])

    strat = ORBStrategy(cfg)
    strat.set_prev_day_data(high=9500, volume=100000, close=9800)

    result = bt.run_backtest(df, strat)
    trades = result["trades"]

    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "stop_loss"
    assert trades[0]["pnl"] < 0


@pytest.mark.asyncio
async def test_orb_fast_backtester_forced_close(base_cfg):
    """마지막 캔들 강제 청산."""
    from backtest.backtester_fast import ORBFastBacktester
    from config.settings import BacktestConfig

    cfg = dataclasses.replace(base_cfg, orb_sl_ratio=2.0, orb_tp_ratio=10.0)
    bt_cfg = BacktestConfig()
    bt = ORBFastBacktester(
        db=None, config=cfg, backtest_config=bt_cfg,
        ticker_market="kosdaq", market_strong_by_date={},
    )

    candles_data = [
        {"ts": "2026-01-02 09:00:00", "open": 10000, "high": 10100, "low": 9900, "close": 10000, "volume": 5000, "vwap": 10000},
        {"ts": "2026-01-02 09:01:00", "open": 10000, "high": 10100, "low": 9900, "close": 10000, "volume": 5000, "vwap": 10000},
        {"ts": "2026-01-02 09:02:00", "open": 10000, "high": 10100, "low": 9900, "close": 10000, "volume": 5000, "vwap": 10000},
        {"ts": "2026-01-02 09:03:00", "open": 10000, "high": 10100, "low": 9900, "close": 10000, "volume": 5000, "vwap": 10000},
        {"ts": "2026-01-02 09:04:00", "open": 10000, "high": 10100, "low": 9900, "close": 10000, "volume": 5000, "vwap": 10000},
        # 09:05: 진입 close=10110
        {"ts": "2026-01-02 09:05:00", "open": 10100, "high": 10200, "low": 10000, "close": 10110, "volume": 50000, "vwap": 10110},
        # 09:06~09:09: 횡보
        {"ts": "2026-01-02 09:06:00", "open": 10110, "high": 10150, "low": 10050, "close": 10120, "volume": 10000, "vwap": 10120},
        {"ts": "2026-01-02 09:07:00", "open": 10120, "high": 10140, "low": 10080, "close": 10100, "volume": 8000,  "vwap": 10110},
        {"ts": "2026-01-02 09:08:00", "open": 10100, "high": 10120, "low": 10060, "close": 10110, "volume": 7000,  "vwap": 10100},
        # 마지막 캔들: 강제 청산
        {"ts": "2026-01-02 09:09:00", "open": 10110, "high": 10130, "low": 10090, "close": 10115, "volume": 6000,  "vwap": 10110},
    ]
    df = pd.DataFrame(candles_data)
    df["ts"] = pd.to_datetime(df["ts"])

    strat = ORBStrategy(cfg)
    strat.set_prev_day_data(high=9500, volume=100000, close=9800)

    result = bt.run_backtest(df, strat)
    trades = result["trades"]

    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "forced_close"


@pytest.mark.asyncio
async def test_orb_no_range_returns_empty(base_cfg):
    """레인지 분봉 없으면 거래 없음."""
    from backtest.backtester_fast import ORBFastBacktester
    from config.settings import BacktestConfig

    bt_cfg = BacktestConfig()
    bt = ORBFastBacktester(
        db=None, config=base_cfg, backtest_config=bt_cfg,
        ticker_market="kosdaq", market_strong_by_date={},
    )

    # 09:00~09:04 분봉 없이 09:05부터 시작
    candles_data = [
        {"ts": "2026-01-02 09:05:00", "open": 10100, "high": 10200, "low": 10000, "close": 10150, "volume": 50000, "vwap": 10100},
        {"ts": "2026-01-02 09:06:00", "open": 10150, "high": 10300, "low": 10100, "close": 10250, "volume": 20000, "vwap": 10200},
    ]
    df = pd.DataFrame(candles_data)
    df["ts"] = pd.to_datetime(df["ts"])

    strat = ORBStrategy(base_cfg)
    strat.set_prev_day_data(high=9500, volume=100000, close=9800)

    result = bt.run_backtest(df, strat)
    assert result["trades"] == []
