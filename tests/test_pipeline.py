"""tests/test_pipeline.py"""

import asyncio
import pytest

from data.candle_builder import CandleBuilder


@pytest.mark.asyncio
async def test_pipeline_tick_to_candle():
    """틱 → 캔들빌더 → 캔들 Queue 전달 확인."""
    candle_queue = asyncio.Queue()
    builder = CandleBuilder(candle_queue=candle_queue)

    ticks = [
        {"ticker": "005930", "time": "090500", "price": 70000, "volume": 100, "cum_volume": 100},
        {"ticker": "005930", "time": "090600", "price": 70500, "volume": 200, "cum_volume": 300},
    ]

    for t in ticks:
        await builder.on_tick(t)

    candle = await asyncio.wait_for(candle_queue.get(), timeout=1.0)
    assert candle["ticker"] == "005930"
    assert candle["tf"] == "1m"


@pytest.mark.asyncio
async def test_pipeline_candle_to_strategy():
    """캔들 → 전략 엔진 신호 생성 확인."""
    import pandas as pd
    from strategy.orb_strategy import OrbStrategy
    from config.settings import TradingConfig
    from unittest.mock import patch

    orb = OrbStrategy(TradingConfig())
    orb._range_high = 70400
    orb._range_low = 69600

    candles = pd.DataFrame({
        "time": ["09:16"], "close": [70500], "high": [70600],
        "low": [70400], "volume": [5000],
    })
    tick = {"ticker": "005930", "price": 70500, "time": "091600", "volume": 500}

    with patch.object(orb, "is_tradable_time", return_value=True):
        signal = orb.generate_signal(candles, tick)
        assert signal is not None
        assert signal.strategy == "orb"
