"""core/order_manager.py — 주문 실행기."""

import asyncio
from loguru import logger
from config.settings import TradingConfig
from core.kiwoom_rest import KiwoomRestClient
from data.db_manager import DbManager
from notification.telegram_bot import TelegramNotifier


class OrderManager:
    CONFIRMATION_TIMEOUT = 5.0

    def __init__(
        self,
        rest_client: KiwoomRestClient,
        risk_manager=None,
        notifier: TelegramNotifier | None = None,
        db: DbManager | None = None,
        trading_config: TradingConfig | None = None,
        order_queue: asyncio.Queue | None = None,
    ):
        self._rest_client = rest_client
        self._risk_manager = risk_manager
        self._notifier = notifier
        self._db = db
        self._config = trading_config or TradingConfig()
        self._lock = asyncio.Lock()
        self._active_orders: dict[str, bool] = {}
        self._order_queue: asyncio.Queue = order_queue or asyncio.Queue()

    async def execute_buy(self, ticker: str, price: int, total_qty: int) -> dict | None:
        if ticker in self._active_orders:
            logger.warning(f"중복 주문 차단: {ticker}")
            return None

        async with self._lock:
            self._active_orders[ticker] = True
            try:
                qty_1st = int(total_qty * self._config.entry_1st_ratio)
                result = await self._rest_client.send_order(
                    ticker=ticker, qty=qty_1st, price=price,
                    side="buy", order_type="01",
                )
                if result.get("rt_cd") == "0":
                    order_no = result["output"]["ODNO"]
                    logger.info(f"1차 매수 주문: {ticker} {qty_1st}주 @ {price:,}")
                    return {"order_no": order_no, "qty": qty_1st}
                else:
                    logger.error(f"주문 실패: {result}")
                    return None
            finally:
                self._active_orders.pop(ticker, None)

    async def execute_buy_2nd(self, ticker: str, price: int, remaining_qty: int) -> dict | None:
        return await self._send_order(ticker, remaining_qty, price, "buy")

    async def execute_sell_tp1(self, ticker: str, price: int, remaining_qty: int) -> dict | None:
        sell_qty = int(remaining_qty * self._config.tp1_sell_ratio)
        return await self._send_order(ticker, sell_qty, price, "sell", order_type="01")

    async def execute_sell_stop(self, ticker: str, qty: int) -> dict | None:
        return await self._send_order(ticker, qty, 0, "sell", order_type="00")

    async def execute_sell_force_close(self, ticker: str, qty: int) -> dict | None:
        logger.warning(f"강제 청산: {ticker} {qty}주")
        return await self._send_order(ticker, qty, 0, "sell", order_type="00")

    async def _send_order(self, ticker, qty, price, side, order_type="01") -> dict | None:
        try:
            result = await self._rest_client.send_order(
                ticker=ticker, qty=qty, price=price,
                side=side, order_type=order_type,
            )
            if result.get("rt_cd") == "0":
                return {"order_no": result["output"]["ODNO"], "qty": qty}
            logger.error(f"주문 실패: {result}")
            return None
        except Exception as e:
            logger.error(f"주문 예외: {e}")
            if self._notifier:
                await self._notifier.send_urgent(f"주문 실패: {ticker} {side} {qty}주 — {e}")
            return None

    async def wait_for_confirmation(self, order_no: str) -> dict | None:
        try:
            return await asyncio.wait_for(
                self._order_queue.get(), timeout=self.CONFIRMATION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(f"체결 확인 타임아웃: {order_no}")
            return None
