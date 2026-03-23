"""tests/test_kiwoom_ws.py"""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from core.kiwoom_ws import KiwoomWebSocketClient


@pytest.mark.asyncio
async def test_subscribe_builds_message():
    ws = KiwoomWebSocketClient(
        ws_url="ws://test",
        token_manager=AsyncMock(get_token=AsyncMock(return_value="tok")),
    )
    ws._ws = AsyncMock()

    await ws.subscribe("005930", "H0STCNT0")
    ws._ws.send.assert_called_once()
    sent = ws._ws.send.call_args[0][0]
    assert "005930" in sent
    assert "H0STCNT0" in sent


@pytest.mark.asyncio
async def test_tick_queue_receives_data():
    tick_queue = asyncio.Queue()
    ws = KiwoomWebSocketClient(
        ws_url="ws://test",
        token_manager=AsyncMock(get_token=AsyncMock(return_value="tok")),
        tick_queue=tick_queue,
    )
    await ws._dispatch_tick({"ticker": "005930", "price": 70000})
    item = await asyncio.wait_for(tick_queue.get(), timeout=1.0)
    assert item["ticker"] == "005930"


@pytest.mark.asyncio
async def test_reconnect_restores_subscriptions():
    ws = KiwoomWebSocketClient(
        ws_url="ws://test",
        token_manager=AsyncMock(get_token=AsyncMock(return_value="tok")),
    )
    ws._subscriptions = {"H0STCNT0": ["005930", "035720"]}
    ws._ws = AsyncMock()

    await ws._restore_subscriptions()
    assert ws._ws.send.call_count == 2
