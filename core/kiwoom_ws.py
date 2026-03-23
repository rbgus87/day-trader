"""core/kiwoom_ws.py — 키움 WebSocket 클라이언트 (asyncio Queue 통합)."""

import asyncio
import json

import websockets
from loguru import logger

from core.auth import TokenManager


class KiwoomWebSocketClient:
    """키움 WebSocket — 체결/호가/체결통보 구독, Queue 기반 데이터 전달."""

    HEARTBEAT_INTERVAL = 30
    RECONNECT_BASE_DELAY = 2
    RECONNECT_MAX_DELAY = 60

    def __init__(
        self,
        ws_url: str,
        token_manager: TokenManager,
        tick_queue: asyncio.Queue | None = None,
        order_queue: asyncio.Queue | None = None,
    ):
        self._ws_url = ws_url
        self._token_manager = token_manager
        self._tick_queue = tick_queue
        self._order_queue = order_queue
        self._ws = None
        self._subscriptions: dict[str, list[str]] = {}
        self._listen_task: asyncio.Task | None = None
        self._running = False

    async def connect(self) -> None:
        """WebSocket 연결 및 수신 루프 시작."""
        await self._establish_connection()
        self._running = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        logger.info("WebSocket 연결 완료")

    async def _establish_connection(self) -> None:
        """WS 연결 수립 + 구독 복원 (connect/reconnect 공통)."""
        token = await self._token_manager.get_token()
        self._ws = await websockets.connect(
            self._ws_url,
            additional_headers={"authorization": f"Bearer {token}"},
            ping_interval=self.HEARTBEAT_INTERVAL,
            ping_timeout=10,
        )
        await self._restore_subscriptions()

    async def disconnect(self) -> None:
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
        if self._ws:
            await self._ws.close()
        logger.info("WebSocket 연결 종료")

    async def subscribe(self, ticker: str, tr_type: str) -> None:
        msg = json.dumps({
            "header": {
                "approval_key": await self._token_manager.get_token(),
                "custtype": "P",
                "tr_type": "1",
                "content-type": "utf-8",
            },
            "body": {
                "input": {"tr_id": tr_type, "tr_key": ticker},
            },
        })
        if self._ws:
            await self._ws.send(msg)
        self._subscriptions.setdefault(tr_type, [])
        if ticker not in self._subscriptions[tr_type]:
            self._subscriptions[tr_type].append(ticker)
        logger.debug(f"구독: {tr_type} / {ticker}")

    async def unsubscribe(self, ticker: str, tr_type: str) -> None:
        msg = json.dumps({
            "header": {
                "approval_key": await self._token_manager.get_token(),
                "custtype": "P",
                "tr_type": "2",
                "content-type": "utf-8",
            },
            "body": {
                "input": {"tr_id": tr_type, "tr_key": ticker},
            },
        })
        if self._ws:
            await self._ws.send(msg)
        if tr_type in self._subscriptions:
            self._subscriptions[tr_type] = [
                t for t in self._subscriptions[tr_type] if t != ticker
            ]

    async def _listen_loop(self) -> None:
        reconnect_delay = self.RECONNECT_BASE_DELAY
        while self._running:
            try:
                async for message in self._ws:
                    try:
                        await self._handle_message(message)
                    except Exception as e:
                        logger.error(f"메시지 처리 오류: {e}")
                    reconnect_delay = self.RECONNECT_BASE_DELAY
            except websockets.ConnectionClosed as e:
                if not self._running:
                    break
                logger.warning(f"WS 연결 끊김 (code={e.code}), {reconnect_delay}초 후 재연결")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, self.RECONNECT_MAX_DELAY)
                try:
                    await self._establish_connection()
                except Exception as e2:
                    logger.error(f"재연결 실패: {e2}")
            except Exception as e:
                if not self._running:
                    break
                logger.error(f"WS 오류: {e}")
                await asyncio.sleep(reconnect_delay)

    async def _handle_message(self, raw: str) -> None:
        if raw.startswith("{"):
            data = json.loads(raw)
            logger.debug(f"WS JSON: {data.get('header', {}).get('tr_id', 'unknown')}")
            return

        parts = raw.split("|")
        if len(parts) < 4:
            return

        tr_id = parts[1]
        body = parts[3]

        if tr_id in ("H0STCNT0",):
            tick = self._parse_tick(body)
            if tick:
                await self._dispatch_tick(tick)
        elif tr_id in ("H0STCNI0", "H0STCNI9"):
            order_data = self._parse_order_execution(body)
            if order_data and self._order_queue:
                await self._order_queue.put(order_data)

    def _parse_tick(self, body: str) -> dict | None:
        fields = body.split("^")
        if len(fields) < 20:
            return None
        try:
            return {
                "ticker": fields[0],
                "time": fields[1],
                "price": int(fields[2]),
                "change": int(fields[4]) if fields[4] else 0,
                "volume": int(fields[12]) if fields[12] else 0,
                "cum_volume": int(fields[13]) if fields[13] else 0,
            }
        except (ValueError, IndexError) as e:
            logger.warning(f"틱 파싱 실패: {e}")
            return None

    def _parse_order_execution(self, body: str) -> dict | None:
        fields = body.split("^")
        if len(fields) < 15:
            return None
        try:
            return {
                "order_no": fields[1],
                "ticker": fields[2],
                "side": "buy" if fields[4] == "02" else "sell",
                "price": int(fields[5]) if fields[5] else 0,
                "qty": int(fields[6]) if fields[6] else 0,
                "status": fields[3],
            }
        except (ValueError, IndexError) as e:
            logger.warning(f"체결통보 파싱 실패: {e}")
            return None

    async def _dispatch_tick(self, tick: dict) -> None:
        if self._tick_queue:
            await self._tick_queue.put(tick)

    async def _restore_subscriptions(self) -> None:
        """재연결 후 구독 복원 (subscribe 호출 대신 직접 메시지 전송)."""
        for tr_type, tickers in list(self._subscriptions.items()):
            for ticker in tickers:
                msg = json.dumps({
                    "header": {
                        "approval_key": await self._token_manager.get_token(),
                        "custtype": "P", "tr_type": "1", "content-type": "utf-8",
                    },
                    "body": {"input": {"tr_id": tr_type, "tr_key": ticker}},
                })
                if self._ws:
                    await self._ws.send(msg)

    @property
    def connected(self) -> bool:
        return self._ws is not None and self._ws.open
