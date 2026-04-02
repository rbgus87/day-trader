"""core/kiwoom_ws.py — 키움 OpenAPI+ WebSocket 클라이언트 (asyncio Queue 통합).

키움 WS 메시지 형식 (플랫 구조):
    구독: {"trnm": "REG", "grp_no": "1", "refresh": "1", "authorization": "Bearer ...", "data": [...]}
    응답: {"trnm": "REG", "return_code": "0", "return_msg": "..."}
    체결: {"type": "0B", "item": "005930", "values": {"10": "-70000", "15": "100", ...}}
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
        notifier=None,
        risk_manager=None,
        order_manager=None,
    ):
        self._ws_url = ws_url
        self._token_manager = token_manager
        self._tick_queue = tick_queue
        self._order_queue = order_queue
        self._notifier = notifier
        self._risk_manager = risk_manager
        self._order_manager = order_manager
        self._ws = None
        self._subscriptions: dict[str, list[str]] = {}  # {real_type: [codes]}
        self._listen_task: asyncio.Task | None = None
        self._running = False
        self._reconnect_failures = 0

    async def connect(self) -> None:
        """WebSocket 연결 및 수신 루프 시작."""
        await self._establish_connection()
        self._running = True
        self._reconnect_failures = 0
        self._listen_task = asyncio.create_task(self._listen_loop())
        logger.info("WebSocket 연결 완료")

    async def _establish_connection(self) -> None:
        """WS 연결 수립 + 인증 + 구독 복원."""
        token = await self._token_manager.get_token()
        self._ws = await ws_connect(
            self._ws_url,
            additional_headers={"authorization": f"Bearer {token}"},
            ping_interval=self.HEARTBEAT_INTERVAL,
            ping_timeout=10,
        )

        # 접속허용요청 — REG 전에 토큰 인증 필수
        auth_msg = json.dumps({
            "authorization": f"Bearer {token}",
        })
        await self._ws.send(auth_msg)
        logger.info("[WS] 접속허용요청 전송")

        # 인증 응답 대기
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=5.0)
            auth_resp = json.loads(raw)
            logger.info(f"[WS] 접속허용 응답: {auth_resp}")
            rc = auth_resp.get("return_code", -1)
            if isinstance(rc, str):
                rc = int(rc)
            if rc != 0:
                logger.error(f"[WS] 접속허용 실패 (code={rc}): {auth_resp.get('return_msg', '')}")
        except asyncio.TimeoutError:
            logger.warning("[WS] 접속허용 응답 타임아웃 (5초) — 구독 시도 계속")
        except Exception as e:
            logger.warning(f"[WS] 접속허용 응답 처리 오류: {e}")

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
        token = await self._token_manager.get_token()
        msg = json.dumps({
            "trnm": "REG",
            "grp_no": "1",
            "refresh": "1",
            "authorization": f"Bearer {token}",
            "data": [{"item": tickers, "type": [real_type]}],
        })
        if self._ws:
            await self._ws.send(msg)
        existing = set(self._subscriptions.get(real_type, []))
        existing.update(tickers)
        self._subscriptions[real_type] = list(existing)
        logger.info(f"구독 요청: {len(tickers)}종목 (type={real_type})")

    async def unsubscribe(self, tickers: list[str], real_type: str = WS_TYPE_TICK) -> None:
        token = await self._token_manager.get_token()
        msg = json.dumps({
            "trnm": "REMOVE",
            "grp_no": "1",
            "authorization": f"Bearer {token}",
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
        import time as _time
        reconnect_delay = self.RECONNECT_BASE_DELAY
        ws_msg_count = 0
        last_ws_log = _time.time()
        while self._running:
            try:
                async for message in self._ws:
                    ws_msg_count += 1
                    if ws_msg_count <= 3:
                        logger.info(f"[WS-DIAG] raw msg #{ws_msg_count} len={len(message)} preview={message[:100]}")
                    try:
                        data = json.loads(message)
                        await self._dispatch(data)
                    except Exception as e:
                        logger.error(f"메시지 처리 오류: {e}")
                    if ws_msg_count == 1:
                        logger.info("[WS] 첫 메시지 수신")
                    now_ws = _time.time()
                    if now_ws - last_ws_log >= 300:
                        logger.info(f"[WS] {ws_msg_count}건 수신 (최근 5분)")
                        ws_msg_count = 0
                        last_ws_log = now_ws
                    reconnect_delay = self.RECONNECT_BASE_DELAY
                    self._reconnect_failures = 0
                # async for 종료 = 연결 끊김
                logger.warning(f"[WS-DIAG] async for 종료 — 수신 {ws_msg_count}건 후 연결 끊김")
            except Exception as e:
                if not self._running:
                    break

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
                    self._reconnect_failures = 0
                except Exception as e2:
                    logger.error(f"재연결 실패: {e2}")
                    self._reconnect_failures += 1
                    if self._reconnect_failures >= 3 and self._notifier:
                        await self._notifier.send_urgent(
                            f"WS 재연결 3회 실패!\n"
                            f"마지막 오류: {e2}\n"
                            f"포지션 확인 필요"
                        )
                        # 포지션 보유 중이면 REST로 긴급 청산
                        if self._risk_manager and self._order_manager:
                            positions = self._risk_manager.get_open_positions()
                            for ticker, pos in positions.items():
                                if pos.get("remaining_qty", 0) > 0:
                                    await self._order_manager.execute_sell_force_close(
                                        ticker=ticker, qty=pos["remaining_qty"],
                                    )
                                    await self._notifier.send_urgent(
                                        f"WS 장애 긴급 청산: {ticker} {pos['remaining_qty']}주"
                                    )

    async def _dispatch(self, data: dict) -> None:
        """수신 데이터 타입별 라우팅."""
        # 진단: 처음 5건 메시지 구조 로깅
        if not hasattr(self, '_dispatch_count'):
            self._dispatch_count = 0
        self._dispatch_count += 1
        if self._dispatch_count <= 5:
            data_summary = str(data)[:200]
            logger.info(f"[WS-DIAG] msg #{self._dispatch_count} type='{data.get('type', '')}' trnm='{data.get('trnm', '')}' keys={list(data.keys())} data={data_summary}")

        # 등록/해지 응답 (return_code 존재)
        return_code = data.get("return_code")
        if return_code is not None:
            rc = int(return_code)
            trnm = data.get("trnm", "")
            if rc == 0:
                logger.info(f"[WS] {trnm} 성공")
            else:
                logger.error(f"[WS] {trnm} 실패 (code={rc}): {data.get('return_msg', '')}")
            return

        # 실시간 데이터
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
                "time": values.get("20", ""),
                "price": abs(int(values.get("10", 0))),
                "volume": int(values.get("15", 0)),
                "cum_volume": int(values.get("13", 0)),
                "change": int(values.get("12", 0)),
            }
        except (ValueError, KeyError) as e:
            logger.warning(f"틱 파싱 실패: {e}")
            return None

    async def _dispatch_tick(self, tick: dict) -> None:
        """틱 데이터를 Queue로 전달 (논블로킹)."""
        if self._tick_queue:
            try:
                self._tick_queue.put_nowait(tick)
            except asyncio.QueueFull:
                # 오래된 틱 1개 드랍 후 새 틱 삽입
                try:
                    self._tick_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    self._tick_queue.put_nowait(tick)
                except asyncio.QueueFull:
                    logger.warning("tick_queue 가득 참 — 틱 드랍")

    async def _restore_subscriptions(self) -> None:
        """재연결 후 구독 복원."""
        token = await self._token_manager.get_token()
        for real_type, codes in self._subscriptions.items():
            if codes:
                msg = json.dumps({
                    "trnm": "REG",
                    "grp_no": "1",
                    "refresh": "1",
                    "authorization": f"Bearer {token}",
                    "data": [{"item": codes, "type": [real_type]}],
                })
                if self._ws:
                    await self._ws.send(msg)
                logger.info(f"구독 복원: {len(codes)}종목 (type={real_type})")

    @property
    def connected(self) -> bool:
        return self._ws is not None and self._running
