"""core/kiwoom_ws.py — 키움 OpenAPI+ WebSocket 클라이언트 (asyncio Queue 통합).

키움 WS 메시지 형식:
    구독: {"trnm": "REG", "grp_no": "1", "refresh": "1", "data": [{"item": ["005930"], "type": ["0B"]}]}
    체결: {"type": "0B", "item": "005930", "values": {"10": "-70000", "15": "100", ...}}
    주문: {"type": "00", ...}
"""

import asyncio
import json

from websockets.asyncio.client import connect as ws_connect
from loguru import logger

from core.auth import TokenManager
from utils.market_calendar import is_ws_active_hours

# 실시간 타입
WS_TYPE_TICK = "0B"        # 체결
WS_TYPE_ORDERBOOK = "0D"   # 호가
WS_TYPE_ORDER = "00"       # 주문체결


class KiwoomWebSocketClient:
    """키움 WebSocket — 체결/호가/주문체결 구독, Queue 기반 데이터 전달."""

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
        self._subscriptions: dict[str, list[str]] = {}  # {real_type: [codes]}
        self._listen_task: asyncio.Task | None = None
        self._running = False

    async def connect(self) -> None:
        """WebSocket 연결 및 수신 루프 시작."""
        await self._establish_connection()
        self._running = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        logger.info("WebSocket 연결 완료")

    async def _establish_connection(self) -> None:
        """WS 연결 수립 + 구독 복원."""
        token = await self._token_manager.get_token()
        self._ws = await ws_connect(
            self._ws_url,
            additional_headers={"authorization": f"Bearer {token}"},
            ping_interval=self.HEARTBEAT_INTERVAL,
            ping_timeout=10,
        )
        await self._restore_subscriptions()

    async def disconnect(self) -> None:
        """연결 종료."""
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
        if self._ws:
            await self._ws.close()
        logger.info("WebSocket 연결 종료")

    async def subscribe(self, tickers: list[str], real_type: str = WS_TYPE_TICK) -> None:
        """실시간 구독 등록.

        Args:
            tickers: 종목 코드 리스트
            real_type: "0B"(체결), "0D"(호가), "00"(주문체결)
        """
        msg = json.dumps({
            "trnm": "REG",
            "grp_no": "1",
            "refresh": "1",
            "data": [{"item": tickers, "type": [real_type]}],
        })
        if self._ws:
            await self._ws.send(msg)
        existing = set(self._subscriptions.get(real_type, []))
        existing.update(tickers)
        self._subscriptions[real_type] = list(existing)
        logger.debug(f"구독: {len(tickers)}종목 (type={real_type})")

    async def unsubscribe(self, tickers: list[str], real_type: str = WS_TYPE_TICK) -> None:
        """구독 해제."""
        msg = json.dumps({
            "trnm": "REMOVE",
            "grp_no": "1",
            "data": [{"item": tickers, "type": [real_type]}],
        })
        if self._ws:
            await self._ws.send(msg)
        if real_type in self._subscriptions:
            existing = set(self._subscriptions[real_type])
            existing -= set(tickers)
            self._subscriptions[real_type] = list(existing)

    async def _listen_loop(self) -> None:
        """수신 루프 — 재연결 포함 (장 시간에만)."""
        reconnect_delay = self.RECONNECT_BASE_DELAY
        while self._running:
            try:
                async for message in self._ws:
                    try:
                        data = json.loads(message)
                        await self._dispatch(data)
                    except Exception as e:
                        logger.error(f"메시지 처리 오류: {e}")
                    reconnect_delay = self.RECONNECT_BASE_DELAY
            except Exception as e:
                if not self._running:
                    break

                # 장외 시간이면 재연결하지 않고 대기
                if not is_ws_active_hours():
                    logger.info("WS 끊김 — 장외 시간이므로 재연결 생략, 60초 후 재확인")
                    await asyncio.sleep(60)
                    continue

                logger.warning(f"WS 연결 끊김: {e}, {reconnect_delay}초 후 재연결")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, self.RECONNECT_MAX_DELAY)
                try:
                    await self._establish_connection()
                    reconnect_delay = self.RECONNECT_BASE_DELAY
                except Exception as e2:
                    logger.error(f"재연결 실패: {e2}")

    async def _dispatch(self, data: dict) -> None:
        """수신 데이터 타입별 라우팅."""
        msg_type = data.get("type", "")

        if msg_type == WS_TYPE_TICK:
            tick = self._parse_tick(data)
            if tick:
                await self._dispatch_tick(tick)
        elif msg_type == WS_TYPE_ORDER:
            if self._order_queue:
                await self._order_queue.put(data)

    def _parse_tick(self, data: dict) -> dict | None:
        """키움 체결 데이터 → 표준 tick dict."""
        try:
            values = data.get("values", {})
            if isinstance(values, str):
                values = json.loads(values)
            return {
                "ticker": data.get("item", ""),
                "time": values.get("20", ""),           # FID 20: 체결시간
                "price": abs(int(values.get("10", 0))),  # FID 10: 현재가 (음수=하락)
                "volume": int(values.get("15", 0)),      # FID 15: 거래량
                "cum_volume": int(values.get("13", 0)),  # FID 13: 누적거래량
                "change": int(values.get("12", 0)),      # FID 12: 전일대비
            }
        except (ValueError, KeyError) as e:
            logger.warning(f"틱 파싱 실패: {e}")
            return None

    async def _dispatch_tick(self, tick: dict) -> None:
        """틱 데이터를 Queue로 전달."""
        if self._tick_queue:
            await self._tick_queue.put(tick)

    async def _restore_subscriptions(self) -> None:
        """재연결 후 구독 복원."""
        for real_type, codes in self._subscriptions.items():
            if codes:
                msg = json.dumps({
                    "trnm": "REG",
                    "grp_no": "1",
                    "refresh": "1",
                    "data": [{"item": codes, "type": [real_type]}],
                })
                if self._ws:
                    await self._ws.send(msg)

    @property
    def connected(self) -> bool:
        return self._ws is not None and self._running
