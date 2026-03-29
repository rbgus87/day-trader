"""tests/test_candle_builder.py"""

import asyncio
import pytest

from data.candle_builder import CandleBuilder


@pytest.mark.asyncio
async def test_builds_1m_candle():
    out_queue = asyncio.Queue()
    builder = CandleBuilder(candle_queue=out_queue)

    ticks = [
        {"ticker": "005930", "time": "090100", "price": 70000, "volume": 100, "cum_volume": 100},
        {"ticker": "005930", "time": "090130", "price": 70500, "volume": 200, "cum_volume": 300},
        {"ticker": "005930", "time": "090155", "price": 69500, "volume": 150, "cum_volume": 450},
        {"ticker": "005930", "time": "090200", "price": 70200, "volume": 50, "cum_volume": 500},
    ]

    for tick in ticks:
        await builder.on_tick(tick)

    candle = await asyncio.wait_for(out_queue.get(), timeout=1.0)
    assert candle["ticker"] == "005930"
    assert candle["tf"] == "1m"
    assert candle["open"] == 70000
    assert candle["high"] == 70500
    assert candle["low"] == 69500
    assert candle["close"] == 69500
    assert candle["volume"] == 450


@pytest.mark.asyncio
async def test_vwap_calculation():
    out_queue = asyncio.Queue()
    builder = CandleBuilder(candle_queue=out_queue)

    ticks_min1 = [
        {"ticker": "005930", "time": "090100", "price": 70000, "volume": 100, "cum_volume": 100},
        {"ticker": "005930", "time": "090130", "price": 70500, "volume": 200, "cum_volume": 300},
        {"ticker": "005930", "time": "090155", "price": 69500, "volume": 150, "cum_volume": 450},
    ]
    tick_next = {"ticker": "005930", "time": "090200", "price": 70200, "volume": 50, "cum_volume": 500}

    for t in ticks_min1:
        await builder.on_tick(t)
    await builder.on_tick(tick_next)

    candle = await asyncio.wait_for(out_queue.get(), timeout=1.0)
    assert candle["vwap"] is not None
    # VWAP is cumulative: includes all ticks up to (and including) the trigger tick
    # pv_sum = 70000*100 + 70500*200 + 69500*150 + 70200*50 = 35_035_000
    # vol_sum = 100 + 200 + 150 + 50 = 500  → VWAP = 70_070.0
    assert 70000 < candle["vwap"] < 70200


@pytest.mark.asyncio
async def test_candle_ts_is_iso8601():
    """캔들 ts가 ISO8601 형식(날짜 포함)인지 확인."""
    out_queue = asyncio.Queue()
    builder = CandleBuilder(candle_queue=out_queue)

    ticks = [
        {"ticker": "005930", "time": "090100", "price": 70000, "volume": 100, "cum_volume": 100},
        {"ticker": "005930", "time": "090200", "price": 70200, "volume": 50, "cum_volume": 150},
    ]
    for t in ticks:
        await builder.on_tick(t)

    candle = await asyncio.wait_for(out_queue.get(), timeout=1.0)
    # ts에 날짜 + 시간이 모두 포함되어야 함 (ISO8601: YYYY-MM-DDTHH:MM:SS)
    assert "T" in candle["ts"], f"ts에 날짜가 포함되어야 한다: {candle['ts']}"
    assert len(candle["ts"]) >= 19, f"ISO8601 형식이어야 한다: {candle['ts']}"


@pytest.mark.asyncio
async def test_5m_candle():
    out_queue = asyncio.Queue()
    builder = CandleBuilder(candle_queue=out_queue, timeframes=["1m", "5m"])

    times = ["090100", "090200", "090300", "090400", "090500", "090600"]
    prices = [70000, 70100, 70200, 69800, 70300, 70400]
    volumes = [100, 100, 100, 100, 100, 100]

    for i, (t, p, v) in enumerate(zip(times, prices, volumes)):
        await builder.on_tick({
            "ticker": "005930", "time": t, "price": p,
            "volume": v, "cum_volume": (i + 1) * 100,
        })

    candles = []
    while not out_queue.empty():
        candles.append(await out_queue.get())

    tf_5m = [c for c in candles if c["tf"] == "5m"]
    assert len(tf_5m) == 1
    assert tf_5m[0]["open"] == 70000
    assert tf_5m[0]["high"] == 70300
    assert tf_5m[0]["low"] == 69800
