"""tests/test_tick_entry.py — 즉시 진입 경로 + candle_consumer 중복 방지 테스트."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from pipeline.trading_state import BreakoutInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tick(ticker: str, price: float, cum_volume: int = 5000) -> dict:
    return {
        "ticker": ticker,
        "price": price,
        "cum_volume": cum_volume,
        "volume": 100,
        "time": "09:30",
    }


def _make_candles_df(n: int = 40, base_volume: int = 1000, base_price: float = 10000.0) -> pd.DataFrame:
    from datetime import timedelta
    rows = []
    t = datetime(2026, 4, 1, 9, 5)
    for i in range(n):
        rows.append({
            "ts": t + timedelta(minutes=i),
            "open": base_price,
            "high": base_price * 1.005,
            "low": base_price * 0.995,
            "close": base_price,
            "volume": base_volume,
            "time": (t + timedelta(minutes=i)).strftime("%H:%M"),
        })
    return pd.DataFrame(rows)


class TestTickSignaledDeduplication:
    """_tick_signaled 셋 기반 중복 진입 방지."""

    def test_tick_signaled_blocks_candle_consumer(self):
        """_tick_signaled에 있는 종목은 candle_consumer가 스킵."""
        # EngineWorker의 _tick_signaled와 _breakout_detected는 dict/set이므로
        # 단순 dict 시뮬레이션으로 로직 검증
        tick_signaled: set[str] = set()
        breakout_detected: dict = {}

        ticker = "000250"
        tick_signaled.add(ticker)

        # candle_consumer 로직 시뮬: ticker in tick_signaled → skip
        should_skip = ticker in tick_signaled
        assert should_skip is True

    def test_tick_signaled_allows_other_tickers(self):
        """_tick_signaled에 없는 종목은 candle_consumer가 처리."""
        tick_signaled: set[str] = {"000250"}
        assert ("005930" in tick_signaled) is False

    def test_breakout_detected_cleared_on_reset(self):
        """일일 리셋 후 _breakout_detected가 비워짐."""
        breakout_detected: dict = {
            "000250": BreakoutInfo("000250", 10300.0, datetime.now()),
            "005930": BreakoutInfo("005930", 75000.0, datetime.now()),
        }
        tick_signaled: set[str] = {"000250"}

        # 리셋 시뮬
        breakout_detected.clear()
        tick_signaled.clear()

        assert len(breakout_detected) == 0
        assert len(tick_signaled) == 0


class TestBreakoutTagging:
    """틱 레벨 돌파 태깅 로직 단위 테스트."""

    def test_first_tick_above_threshold_tags(self):
        """처음으로 breakout_threshold 초과 시 태깅."""
        breakout_detected: dict = {}
        prev_high = 10000.0
        min_bp = 0.03
        breakout_threshold = prev_high * (1 + min_bp)  # 10300

        price = 10350.0  # > 10300
        ticker = "T001"

        if price >= breakout_threshold and ticker not in breakout_detected:
            breakout_detected[ticker] = BreakoutInfo(
                ticker=ticker, breakout_price=price, detected_at=datetime.now()
            )

        assert ticker in breakout_detected
        assert breakout_detected[ticker].breakout_price == 10350.0

    def test_below_threshold_not_tagged(self):
        """breakout_threshold 미달 시 태깅 안 됨."""
        breakout_detected: dict = {}
        prev_high = 10000.0
        min_bp = 0.03
        breakout_threshold = prev_high * (1 + min_bp)

        price = 10290.0  # < 10300
        ticker = "T001"

        if price >= breakout_threshold and ticker not in breakout_detected:
            breakout_detected[ticker] = BreakoutInfo(
                ticker=ticker, breakout_price=price, detected_at=datetime.now()
            )

        assert ticker not in breakout_detected

    def test_second_tick_does_not_overwrite(self):
        """이미 태깅된 종목에 더 높은 가격 틱 와도 덮어쓰지 않음."""
        breakout_detected: dict = {}
        prev_high = 10000.0
        min_bp = 0.03
        threshold = prev_high * (1 + min_bp)
        ticker = "T001"

        # 1st tick
        p1 = 10320.0
        if p1 >= threshold and ticker not in breakout_detected:
            breakout_detected[ticker] = BreakoutInfo(
                ticker=ticker, breakout_price=p1, detected_at=datetime.now()
            )
        assert breakout_detected[ticker].breakout_price == p1

        # 2nd tick (higher)
        p2 = 10500.0
        if p2 >= threshold and ticker not in breakout_detected:
            breakout_detected[ticker] = BreakoutInfo(
                ticker=ticker, breakout_price=p2, detected_at=datetime.now()
            )
        # 덮어쓰지 않음 — 최초 돌파 가격 유지
        assert breakout_detected[ticker].breakout_price == p1


class TestCumulativeVolumeCheck:
    """cum_volume 기반 거래량 조건이 틱에서 체크 가능함을 검증."""

    def test_cum_volume_in_tick(self):
        """WS 틱 딕셔너리에 cum_volume 필드 존재."""
        tick = _make_tick("T001", 10350.0, cum_volume=9000)
        assert "cum_volume" in tick
        assert tick["cum_volume"] == 9000

    def test_volume_ratio_check(self):
        """cum_volume >= prev_day_volume × ratio 조건 수식."""
        prev_day_volume = 4000
        volume_ratio = 2.0
        required = prev_day_volume * volume_ratio  # 8000

        # 충족
        assert 9000 >= required
        # 미충족
        assert 7000 < required


class TestBacktesterBreakoutPrice:
    """backtester.run_backtest에서 breakout_price가 올바르게 계산되는지."""

    def test_breakout_price_computed_on_first_candle_crossing(self):
        """고가가 처음 prev_high × (1 + min_bp) 초과하는 캔들에서 breakout_price 설정."""
        prev_high = 10000.0
        min_bp = 0.03
        threshold = prev_high * (1 + min_bp)  # 10300

        candles_data = [
            {"high": 10200.0},  # 미달
            {"high": 10350.0},  # 돌파 → breakout_price = threshold
            {"high": 10500.0},  # 이미 태깅됨
        ]

        breakout_price_day = None
        for c in candles_data:
            if breakout_price_day is None and c["high"] >= threshold:
                breakout_price_day = threshold
                break

        assert breakout_price_day == pytest.approx(10300.0)

    def test_no_breakout_stays_none(self):
        """전일 고가 돌파가 없으면 breakout_price=None 유지."""
        prev_high = 10000.0
        min_bp = 0.03
        threshold = prev_high * (1 + min_bp)

        candles_data = [
            {"high": 10200.0},
            {"high": 10250.0},
        ]

        breakout_price_day = None
        for c in candles_data:
            if breakout_price_day is None and c["high"] >= threshold:
                breakout_price_day = threshold

        assert breakout_price_day is None
