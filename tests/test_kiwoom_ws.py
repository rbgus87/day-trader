"""tests/test_kiwoom_ws.py — 키움 WebSocket 테스트."""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock

from core.kiwoom_ws import KiwoomWebSocketClient, WS_TYPE_TICK


@pytest.mark.asyncio
async def test_subscribe_builds_message():
    ws = KiwoomWebSocketClient(
        ws_url="ws://test",
        token_manager=AsyncMock(get_token=AsyncMock(return_value="tok")),
    )
    ws._ws = AsyncMock()

    await ws.subscribe(["005930"], WS_TYPE_TICK)
    ws._ws.send.assert_called_once()
    sent = json.loads(ws._ws.send.call_args[0][0])
    assert sent["trnm"] == "REG"
    assert "Bearer tok" in sent["authorization"]
    assert "005930" in sent["data"][0]["item"]
    assert WS_TYPE_TICK in sent["data"][0]["type"]


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
async def test_parse_tick():
    ws = KiwoomWebSocketClient(
        ws_url="ws://test",
        token_manager=AsyncMock(get_token=AsyncMock(return_value="tok")),
    )
    data = {
        "type": "0B",
        "item": "005930",
        "values": {"10": "-70000", "15": "100", "13": "50000", "12": "-500", "20": "090100"},
    }
    tick = ws._parse_tick(data)
    assert tick["ticker"] == "005930"
    assert tick["price"] == 70000  # abs 적용
    assert tick["volume"] == 100


@pytest.mark.asyncio
async def test_reconnect_restores_subscriptions():
    ws = KiwoomWebSocketClient(
        ws_url="ws://test",
        token_manager=AsyncMock(get_token=AsyncMock(return_value="tok")),
    )
    ws._subscriptions = {"0B": ["005930", "035720"]}
    ws._ws = AsyncMock()

    await ws._restore_subscriptions()
    assert ws._ws.send.call_count == 1  # 키움은 종목 리스트를 한번에 전송
    sent = json.loads(ws._ws.send.call_args[0][0])
    assert sent["trnm"] == "REG"
    assert "Bearer tok" in sent["authorization"]
    assert set(sent["data"][0]["item"]) == {"005930", "035720"}
