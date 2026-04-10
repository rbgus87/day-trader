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

        # 종목명 매핑 로드
        self._name_map: dict[str, str] = {}
        from pathlib import Path
        import yaml
        uni_path = Path("config/universe.yaml")
        if uni_path.exists():
            try:
                uni = yaml.safe_load(open(uni_path, encoding="utf-8")) or {}
                for s in uni.get("stocks", []):
                    self._name_map[s["ticker"]] = s.get("name", s["ticker"])
            except Exception:
                pass

    def _format_ticker(self, ticker: str) -> str:
        """종목명(코드) 형식으로 변환."""
        name = self._name_map.get(ticker, "")
        return f"{name}({ticker})" if name else ticker

    async def execute_buy(self, ticker: str, price: int, total_qty: int, strategy: str = "unknown") -> dict | None:
        if ticker in self._active_orders:
            logger.warning(f"중복 주문 차단: {ticker}")
            return None

        async with self._lock:
            self._active_orders[ticker] = True
            try:
                qty_1st = max(1, int(total_qty * self._config.entry_1st_ratio))
                result = await self._rest_client.send_order(
                    ticker=ticker, qty=qty_1st, price=price,
                    side="buy", order_type="01",
                )
                if result.get("rt_cd") == "0":
                    order_no = result["output"]["ODNO"]
                    logger.info(f"1차 매수 주문: {ticker} {qty_1st}주 @ {price:,}")
                    # DB 기록
                    if self._db:
                        from datetime import datetime
                        now = datetime.now().isoformat()
                        await self._db.execute_safe(
                            "INSERT INTO trades (ticker, strategy, side, order_type, "
                            "price, qty, amount, traded_at) "
                            "VALUES (?, ?, 'buy', 'limit', ?, ?, ?, ?)",
                            (ticker, strategy, price, qty_1st, price * qty_1st, now),
                        )
                    return {"order_no": order_no, "qty": qty_1st}
                else:
                    logger.error(f"주문 실패: {result}")
                    return None
            finally:
                self._active_orders.pop(ticker, None)

    async def execute_buy_2nd(self, ticker: str, price: int, remaining_qty: int, strategy: str = "unknown") -> dict | None:
        return await self._send_order(ticker, remaining_qty, price, "buy", strategy=strategy)

    async def execute_sell_tp1(
        self, ticker: str, price: int, remaining_qty: int,
        strategy: str = "unknown", pnl: float | None = None, pnl_pct: float | None = None,
    ) -> dict | None:
        if remaining_qty <= 1:
            sell_qty = remaining_qty  # 1주 보유 시 전량 매도
        else:
            sell_qty = max(1, int(remaining_qty * self._config.tp1_sell_ratio))
        return await self._send_order(ticker, sell_qty, price, "sell", order_type="01", reason="tp1", strategy=strategy, pnl=pnl, pnl_pct=pnl_pct)

    async def execute_sell_stop(
        self, ticker: str, qty: int, price: int = 0,
        strategy: str = "unknown", pnl: float | None = None, pnl_pct: float | None = None,
    ) -> dict | None:
        return await self._send_order(ticker, qty, price, "sell", order_type="00", reason="stop_loss", strategy=strategy, pnl=pnl, pnl_pct=pnl_pct)

    async def execute_sell_force_close(
        self, ticker: str, qty: int, price: int = 0,
        strategy: str = "unknown", pnl: float | None = None, pnl_pct: float | None = None,
    ) -> dict | None:
        logger.warning(f"강제 청산: {ticker} {qty}주")
        return await self._send_order(ticker, qty, price, "sell", order_type="00", reason="force_close", strategy=strategy, pnl=pnl, pnl_pct=pnl_pct)

    async def _send_order(
        self, ticker, qty, price, side, order_type="01", reason: str = "",
        strategy: str = "unknown", pnl: float | None = None, pnl_pct: float | None = None,
    ) -> dict | None:
        try:
            result = await self._rest_client.send_order(
                ticker=ticker, qty=qty, price=price,
                side=side, order_type=order_type,
            )
            if result.get("rt_cd") == "0":
                # DB 기록
                if self._db:
                    from datetime import datetime
                    now = datetime.now().isoformat()
                    await self._db.execute_safe(
                        "INSERT INTO trades (ticker, strategy, side, order_type, "
                        "price, qty, amount, pnl, pnl_pct, exit_reason, traded_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (ticker, strategy, side, order_type, price, qty, price * qty, pnl, pnl_pct, reason, now),
                    )
                return {"order_no": result["output"]["ODNO"], "qty": qty}
            logger.error(f"주문 실패: {result}")
            return None
        except Exception as e:
            logger.error(f"주문 예외: {e}")
            if self._notifier:
                await self._notifier.send_urgent(f"주문 실패: {self._format_ticker(ticker)} {side} {qty}주 — {e}")
            return None

    async def wait_for_confirmation(self, order_no: str) -> dict | None:
        try:
            return await asyncio.wait_for(
                self._order_queue.get(), timeout=self.CONFIRMATION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(f"체결 확인 타임아웃: {order_no}")
            return None
