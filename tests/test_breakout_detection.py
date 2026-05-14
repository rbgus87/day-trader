"""tests/test_breakout_detection.py — 틱 레벨 돌파 감지 + entry_too_high 차단 테스트."""

from datetime import time

import pandas as pd
import pytest

from config.settings import TradingConfig
from strategy.momentum_strategy import MomentumStrategy

_TRADE_TIME = time(10, 0)  # 테스트용 고정 시각 (is_tradable_time 통과 보장)


def _make_config(**overrides) -> TradingConfig:
    defaults = dict(
        market_filter_enabled=False,
        market_ma_length=5,
        momentum_volume_ratio=2.0,
        min_breakout_pct=0.03,
        adx_enabled=False,
        rvol_enabled=False,
        vwap_enabled=False,
        atr_stop_enabled=False,
        momentum_stop_loss_pct=-0.08,
        atr_tp_enabled=False,
        atr_trail_enabled=True,
        atr_trail_multiplier=1.0,
        atr_trail_min_pct=0.02,
        atr_trail_max_pct=0.10,
        breakeven_enabled=False,
        limit_up_exit_enabled=False,
        limit_up_pct=0.30,
        limit_up_stop_floor_pct=0.99,
        time_decay_trailing_enabled=False,
        time_decay_min_pct_floor=0.01,
        time_decay_phases=[],
        momentum_fade_exit_enabled=False,
        momentum_fade_lookback=10,
        momentum_fade_threshold=-0.008,
        momentum_fade_min_hold_min=15,
        momentum_fade_min_profit=0.03,
        buy_time_limit_enabled=False,
        buy_time_end="12:00",
        max_entry_above_breakout_pct=0.05,
        max_trades_per_day=2,
        cooldown_minutes=0,
        max_positions=3,
        signal_block_until="09:05",
        force_close_time="15:10",
        daily_max_loss_pct=-0.015,
        daily_max_loss_enabled=False,
        blacklist_enabled=False,
        blacklist_lookback_days=5,
        blacklist_loss_threshold=3,
        blacklist_days=7,
        consecutive_loss_rest_enabled=False,
        consecutive_loss_threshold=3,
        consecutive_loss_rest_days=1,
        entry_1st_ratio=1.0,
        screening_top_n=5,
        screening_time="08:30",
        report_time="15:30",
        initial_capital=5_000_000,
        reduced_position_pct=0.5,
        consecutive_loss_days=3,
        vi_static_pct=0.095,
        vi_assumed_duration_sec=150,
        vi_suspected_duration_sec=60,
        order_confirmation_timeout_sec=10.0,
        order_timeout_consecutive_threshold=3,
    )
    defaults.update(overrides)
    return TradingConfig(**defaults)


def _make_candles(n: int = 40, base_volume: int = 1000) -> pd.DataFrame:
    """최소 ADX 계산 요건 충족 캔들 생성 (전일 고가 10000 기준 3.5% 위 — BREAKOUT_LAST 통과)."""
    from datetime import datetime, timedelta
    rows = []
    t = datetime(2026, 4, 1, 9, 5)
    price = 10350.0  # prev_day_high(10000) * 1.035 → BREAKOUT_LAST 3% 통과
    for i in range(n):
        rows.append({
            "ts": t + timedelta(minutes=i),
            "open": price,
            "high": price * 1.005,
            "low": price * 0.995,
            "close": price,
            "volume": base_volume,
            "time": (t + timedelta(minutes=i)).strftime("%H:%M"),
        })
    df = pd.DataFrame(rows)
    return df


class TestBreakoutDetection:
    """틱 레벨 돌파 감지 및 breakout_price 파라미터 동작."""

    def test_breakout_price_none_passes(self):
        """breakout_price=None이면 괴리 검사 없이 기존대로 통과."""
        cfg = _make_config()
        strat = MomentumStrategy(cfg)
        strat.set_prev_day_data(high=10000.0, volume=1000)
        strat.set_ticker("T001")
        strat.set_backtest_time(_TRADE_TIME)

        candles = _make_candles(40, base_volume=3000)
        tick = {"ticker": "T001", "price": 10310.0, "cum_volume": 3000, "volume": 100}

        # breakout_price=None → entry_too_high 검사 없음
        signal = strat.generate_signal(candles, tick, breakout_price=None)
        assert signal is not None
        assert strat.diag_counters["entry_too_high"] == 0

    def test_breakout_price_within_limit(self):
        """현재가 ≤ breakout_price × (1 + max_gap) → 통과."""
        cfg = _make_config(max_entry_above_breakout_pct=0.05)
        strat = MomentumStrategy(cfg)
        strat.set_prev_day_data(high=10000.0, volume=1000)
        strat.set_ticker("T001")
        strat.set_backtest_time(_TRADE_TIME)

        candles = _make_candles(40, base_volume=3000)
        # breakout_price=10300, current_price=10300 × 1.04 = 10712 → gap=4% < 5%
        bp = 10300.0
        current = bp * 1.04  # 4% 괴리
        tick = {"ticker": "T001", "price": current, "cum_volume": 3000, "volume": 100}

        signal = strat.generate_signal(candles, tick, breakout_price=bp)
        assert signal is not None
        assert strat.diag_counters["entry_too_high"] == 0

    def test_entry_too_high_blocked(self):
        """현재가가 breakout_price 대비 5% 초과 → 진입 차단."""
        cfg = _make_config(max_entry_above_breakout_pct=0.05)
        strat = MomentumStrategy(cfg)
        strat.set_prev_day_data(high=10000.0, volume=1000)
        strat.set_ticker("T001")
        strat.set_backtest_time(_TRADE_TIME)

        candles = _make_candles(40, base_volume=3000)
        bp = 10300.0
        current = bp * 1.06  # 6% 괴리 → 차단
        tick = {"ticker": "T001", "price": current, "cum_volume": 3000, "volume": 100}

        signal = strat.generate_signal(candles, tick, breakout_price=bp)
        assert signal is None
        assert strat.diag_counters["entry_too_high"] == 1

    def test_entry_too_high_exact_boundary(self):
        """정확히 5% 괴리는 통과 (>가 아닌 >이므로 경계는 허용)."""
        cfg = _make_config(max_entry_above_breakout_pct=0.05)
        strat = MomentumStrategy(cfg)
        strat.set_prev_day_data(high=10000.0, volume=1000)
        strat.set_ticker("T001")
        strat.set_backtest_time(_TRADE_TIME)

        candles = _make_candles(40, base_volume=3000)
        bp = 10300.0
        current = bp * 1.05  # 정확히 5%
        tick = {"ticker": "T001", "price": current, "cum_volume": 3000, "volume": 100}

        signal = strat.generate_signal(candles, tick, breakout_price=bp)
        # gap == max_gap → 차단하지 않음 (엄격 부등호 >)
        assert strat.diag_counters["entry_too_high"] == 0

    def test_breakout_price_zero_passes(self):
        """breakout_price=0.0은 무효값으로 간주 → 검사 생략."""
        cfg = _make_config(max_entry_above_breakout_pct=0.05)
        strat = MomentumStrategy(cfg)
        strat.set_prev_day_data(high=10000.0, volume=1000)
        strat.set_ticker("T001")
        strat.set_backtest_time(_TRADE_TIME)

        candles = _make_candles(40, base_volume=3000)
        tick = {"ticker": "T001", "price": 15000.0, "cum_volume": 3000, "volume": 100}

        signal = strat.generate_signal(candles, tick, breakout_price=0.0)
        # breakout_price=0은 검사 없음
        assert strat.diag_counters["entry_too_high"] == 0

    def test_diag_counter_accumulates(self):
        """entry_too_high 카운터가 매 차단마다 누적."""
        cfg = _make_config(max_entry_above_breakout_pct=0.05)
        strat = MomentumStrategy(cfg)
        strat.set_prev_day_data(high=10000.0, volume=1000)
        strat.set_ticker("T001")
        strat.set_backtest_time(_TRADE_TIME)

        candles = _make_candles(40, base_volume=3000)
        bp = 10300.0
        tick = {"ticker": "T001", "price": bp * 1.10, "cum_volume": 3000, "volume": 100}

        for _ in range(3):
            strat.generate_signal(candles, tick, breakout_price=bp)
            # can_trade()가 False로 바뀌지 않도록 _has_position은 False 유지
            strat._has_position = False

        # 3회 차단
        assert strat.diag_counters["entry_too_high"] == 3

    def test_custom_threshold(self):
        """max_entry_above_breakout_pct=0.03으로 축소 시 3% 초과 차단."""
        cfg = _make_config(max_entry_above_breakout_pct=0.03)
        strat = MomentumStrategy(cfg)
        strat.set_prev_day_data(high=10000.0, volume=1000)
        strat.set_ticker("T001")
        strat.set_backtest_time(_TRADE_TIME)

        candles = _make_candles(40, base_volume=3000)
        bp = 10300.0
        # 4% 괴리 → 차단 (threshold 3%)
        tick = {"ticker": "T001", "price": bp * 1.04, "cum_volume": 3000, "volume": 100}

        signal = strat.generate_signal(candles, tick, breakout_price=bp)
        assert signal is None
        assert strat.diag_counters["entry_too_high"] == 1


class TestBreakoutInfoDataclass:
    """BreakoutInfo 데이터클래스 기본 동작."""

    def test_breakout_info_fields(self):
        from datetime import datetime
        from pipeline.trading_state import BreakoutInfo

        now = datetime.now()
        bi = BreakoutInfo(ticker="005930", breakout_price=75000.0, detected_at=now)
        assert bi.ticker == "005930"
        assert bi.breakout_price == 75000.0
        assert bi.detected_at == now
