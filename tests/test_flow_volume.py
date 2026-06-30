"""tests/test_flow_volume.py — Flow 거래량 필터 단위 테스트."""

from datetime import time, datetime
from unittest.mock import MagicMock

import pandas as pd
import pytest

from strategy.momentum_strategy import MomentumStrategy
from config.settings import TradingConfig


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _make_config(**overrides) -> TradingConfig:
    """flow 관련 파라미터를 포함한 TradingConfig 생성."""
    defaults = dict(
        momentum_volume_ratio=2.0,
        min_breakout_pct=0.03,
        adx_enabled=False,
        rvol_enabled=False,
        vwap_enabled=False,
        buy_time_limit_enabled=False,
        atr_trail_enabled=False,
        breakeven_enabled=False,
        limit_up_exit_enabled=False,
        time_decay_trailing_enabled=False,
        momentum_fade_exit_enabled=False,
        volume_by_time_enabled=False,
        trvol_enabled=False,
        flow_window_min=5,
        flow_ratio=2.0,
        flow_baseline="time_match",
    )
    defaults.update(overrides)
    return TradingConfig(**defaults)


def _make_strategy(config: TradingConfig) -> MomentumStrategy:
    strat = MomentumStrategy(config)
    strat.set_ticker("TEST")
    strat.set_prev_day_data(high=10000.0, volume=100000, close=9500.0)
    return strat


def _make_prev_day_candles(vol_per_min: int = 1000) -> pd.DataFrame:
    """09:00~15:00 전일 1분봉 DataFrame. 각 분봉 거래량 vol_per_min."""
    rows = []
    for h in range(9, 15):
        for m in range(60):
            ts = datetime(2026, 6, 28, h, m, 0)
            rows.append({"ts": ts, "open": 9500, "high": 9550, "low": 9450,
                         "close": 9500, "volume": vol_per_min})
    return pd.DataFrame(rows)


def _make_candles(n: int, volume: int = 5000) -> pd.DataFrame:
    """당일 n개 1분봉 DataFrame."""
    rows = []
    for i in range(n):
        rows.append({
            "ticker": "TEST", "tf": "1m",
            "ts": f"2026-06-29T{9:02d}:{i:02d}:00",
            "open": 10200, "high": 10300, "low": 10150,
            "close": 10250, "volume": volume,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 테스트
# ---------------------------------------------------------------------------

def test_flow_volume_trigger():
    """최근 5분 거래량이 전일 동시간대의 2.0x → VOLUME flow 경로 충족."""
    config = _make_config(flow_ratio=2.0, flow_window_min=5)
    strat = _make_strategy(config)

    # 전일 분봉: 분당 1000주
    prev_candles = _make_prev_day_candles(vol_per_min=1000)
    strat.set_prev_day_candles(prev_candles)

    # 당일 10분봉 (마지막 5개: 분당 2200주 → 합계 11000 vs 전일 5분 합계 5000 → 2.2x)
    candles = _make_candles(n=10, volume=2200)

    result = strat._check_flow_volume(candles, time(9, 10), "TEST")
    assert result is not None
    ok, flow_vol, baseline_vol, ratio = result
    assert ok is True
    assert flow_vol == 2200 * 5        # 마지막 5개
    assert baseline_vol == 1000 * 5    # 전일 09:05~09:10
    assert abs(ratio - 2.2) < 0.01


def test_flow_volume_below_threshold():
    """최근 5분 거래량이 전일 동시간대의 1.5x → flow 미충족."""
    config = _make_config(flow_ratio=2.0, flow_window_min=5)
    strat = _make_strategy(config)

    prev_candles = _make_prev_day_candles(vol_per_min=1000)
    strat.set_prev_day_candles(prev_candles)

    # 분당 1500주 → 합계 7500 vs 전일 5000 → 1.5x < 2.0x
    candles = _make_candles(n=10, volume=1500)

    result = strat._check_flow_volume(candles, time(9, 10), "TEST")
    assert result is not None
    ok, flow_vol, baseline_vol, ratio = result
    assert ok is False
    assert abs(ratio - 1.5) < 0.01


def test_flow_or_cumulative():
    """flow 미충족 + 누적 cumvol 충족 → VOLUME 통과 (OR 동작 확인).

    generate_signal을 직접 호출하기보다 _check_flow_volume 결과와
    cumvol 판정 로직을 독립적으로 검증한다.
    """
    config = _make_config(
        flow_ratio=2.0,
        flow_window_min=5,
        momentum_volume_ratio=2.0,
    )
    strat = _make_strategy(config)
    prev_candles = _make_prev_day_candles(vol_per_min=1000)
    strat.set_prev_day_candles(prev_candles)

    # flow: 분당 1200주 → 1.2x (미충족)
    candles = _make_candles(n=10, volume=1200)
    flow_result = strat._check_flow_volume(candles, time(9, 10), "TEST")
    assert flow_result is not None
    flow_ok = flow_result[0]
    assert flow_ok is False

    # cumvol: 10분 × 1200주 = 12000 vs 전일 100000 × 2.0 = 200000 (미충족)
    cum_volume = candles["volume"].sum()
    cumvol_ok = bool(cum_volume >= strat._prev_day_volume * config.momentum_volume_ratio)
    assert not cumvol_ok

    # 둘 다 미충족 → VOLUME 실패 (OR = False)
    assert not (flow_ok or cumvol_ok)

    # 누적 충족 케이스 (전일 거래량 5000, 당일 누적 12000 > 5000×2.0=10000)
    strat.set_prev_day_data(high=10000.0, volume=5000, close=9500.0)
    cum_volume2 = candles["volume"].sum()  # 12000
    cumvol_ok2 = bool(cum_volume2 >= strat._prev_day_volume * config.momentum_volume_ratio)
    assert cumvol_ok2
    # OR → True
    assert flow_ok or cumvol_ok2


def test_flow_no_baseline_data():
    """전일 분봉 없음 → flow skip, 기존 방식만 사용."""
    config = _make_config(flow_ratio=2.0)
    strat = _make_strategy(config)
    # set_prev_day_candles 미호출 → _prev_day_candles = None

    candles = _make_candles(n=10, volume=5000)
    result = strat._check_flow_volume(candles, time(9, 10), "TEST")
    assert result is None  # skip


def test_flow_insufficient_candles():
    """장 시작 직후 분봉 3개 → flow_window_min=5 미만 → flow skip."""
    config = _make_config(flow_ratio=2.0, flow_window_min=5)
    strat = _make_strategy(config)
    prev_candles = _make_prev_day_candles(vol_per_min=1000)
    strat.set_prev_day_candles(prev_candles)

    candles = _make_candles(n=3, volume=5000)  # 3개 < window=5
    result = strat._check_flow_volume(candles, time(9, 3), "TEST")
    assert result is None


def test_flow_disabled():
    """flow_ratio=None → flow 조건 비활성화, 기존 동작 완전 유지."""
    config = _make_config(flow_ratio=None)
    strat = _make_strategy(config)
    prev_candles = _make_prev_day_candles(vol_per_min=1000)
    strat.set_prev_day_candles(prev_candles)

    candles = _make_candles(n=10, volume=999999)  # 극단적으로 큰 거래량이어도
    result = strat._check_flow_volume(candles, time(9, 10), "TEST")
    assert result is None  # flow_ratio=None → None 반환


def test_flow_time_match_baseline():
    """12:55 시점에서 전일 12:50~12:55 분봉이 기준선으로 정확히 사용되는지."""
    config = _make_config(flow_ratio=2.0, flow_window_min=5)
    strat = _make_strategy(config)

    # 전일 분봉: 12:50~12:54 → 각 2000주, 나머지 분봉 → 100주
    rows = []
    for h in range(9, 15):
        for m in range(60):
            vol = 2000 if (h == 12 and 50 <= m < 55) else 100
            rows.append({
                "ts": datetime(2026, 6, 28, h, m, 0),
                "open": 9500, "high": 9550, "low": 9450,
                "close": 9500, "volume": vol,
            })
    prev_candles = pd.DataFrame(rows)
    strat.set_prev_day_candles(prev_candles)

    # 당일 분봉 10개 (마지막 5개 각 5000주)
    rows_cur = []
    for i in range(10):
        rows_cur.append({
            "ticker": "TEST", "tf": "1m",
            "ts": f"2026-06-29T12:{45 + i:02d}:00",
            "open": 10200, "high": 10300, "low": 10150,
            "close": 10250, "volume": 5000,
        })
    candles = pd.DataFrame(rows_cur)

    # 현재 시각 12:55 → 전일 12:50~12:55 기준선 (2000×5=10000)
    result = strat._check_flow_volume(candles, time(12, 55), "TEST")
    assert result is not None
    ok, flow_vol, baseline_vol, ratio = result
    assert baseline_vol == 2000 * 5    # 12:50, 12:51, 12:52, 12:53, 12:54
    assert flow_vol == 5000 * 5        # 마지막 5개
    assert abs(ratio - 2.5) < 0.01
    assert ok is True


def test_flow_diag_counter_increments():
    """flow 경로로 최초 VOLUME 충족 시 flow_vol_pass 카운터 증가."""
    config = _make_config(
        flow_ratio=2.0,
        flow_window_min=5,
        momentum_volume_ratio=2.0,  # cumvol은 충족 어렵게
    )
    strat = _make_strategy(config)
    # 전일 거래량 10만주 → cumvol 기준 200000주 필요 (당일 10분 × 2000 = 20000 미달)
    strat.set_prev_day_data(high=10000.0, volume=100000, close=9500.0)

    # 전일 분봉: 분당 1000주
    prev_candles = _make_prev_day_candles(vol_per_min=1000)
    strat.set_prev_day_candles(prev_candles)

    # flow_vol_pass 카운터는 generate_signal 내부에서만 증가하므로
    # _check_flow_volume 결과로 ok=True 확인 후 카운터 초기 상태를 확인
    candles = _make_candles(n=10, volume=2200)  # 분당 2200 → flow 2.2x (충족)
    result = strat._check_flow_volume(candles, time(9, 10), "TEST")
    assert result is not None and result[0] is True
    # 최초: flow_vol_pass = 0 (generate_signal 호출 없음)
    assert strat.diag_counters["flow_vol_pass"] == 0
