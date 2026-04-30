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
    assert "authorization" not in sent  # LOGIN으로 이미 인증
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
    assert "authorization" not in sent
    assert set(sent["data"][0]["item"]) == {"005930", "035720"}


@pytest.mark.asyncio
async def test_reconnect_uses_subscription_provider():
    """provider 등록 시 _subscriptions가 부족해도 provider 결과로 복원.

    재현 시나리오: 장외 시간 condition_search가 _active_strategies는 110종목으로
    갱신했으나 WS subscribe send 실패로 _subscriptions에는 41종목만 남은 상태.
    재연결 시 provider 결과(현재 감시 110종목)로 복원되어야 한다.
    """
    ws = KiwoomWebSocketClient(
        ws_url="ws://test",
        token_manager=AsyncMock(get_token=AsyncMock(return_value="tok")),
    )
    ws._subscriptions = {"0B": ["005930"]}  # 부분만 기록된 상태
    ws._ws = AsyncMock()

    full = ["005930", "035720", "000660", "247540"]
    ws.set_subscription_provider(lambda: list(full))

    await ws._restore_subscriptions()
    assert ws._ws.send.call_count == 1
    sent = json.loads(ws._ws.send.call_args[0][0])
    assert sent["trnm"] == "REG"
    assert set(sent["data"][0]["item"]) == set(full)
    # _subscriptions도 동기화되어야 함 (GUI 상태 표시 정확성)
    assert set(ws._subscriptions[WS_TYPE_TICK]) == set(full)


@pytest.mark.asyncio
async def test_provider_empty_falls_back_to_subscriptions():
    """provider 결과가 비어있으면 기존 _subscriptions로 fallback.

    초기 connect() 시점엔 _active_strategies가 비어 provider가 []를 반환.
    이때 기존 _subscriptions 동작(빈 dict이면 no-op)을 그대로 유지해야 한다.
    """
    ws = KiwoomWebSocketClient(
        ws_url="ws://test",
        token_manager=AsyncMock(get_token=AsyncMock(return_value="tok")),
    )
    ws._subscriptions = {"0B": ["005930"]}
    ws._ws = AsyncMock()
    ws.set_subscription_provider(lambda: [])

    await ws._restore_subscriptions()
    sent = json.loads(ws._ws.send.call_args[0][0])
    assert set(sent["data"][0]["item"]) == {"005930"}
