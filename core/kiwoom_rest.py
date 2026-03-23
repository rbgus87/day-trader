"""core/kiwoom_rest.py — 키움 OpenAPI+ REST 클라이언트."""

import re

import aiohttp
from loguru import logger

from core.auth import TokenManager
from core.rate_limiter import AsyncRateLimiter
from core.retry import retry_async
from config.settings import KiwoomConfig

# 키움 REST API ID / 엔드포인트 상수
API_AUTH_TOKEN = "au10001"
API_STOCK_ORDER = "kt10000"
API_STOCK_CANCEL = "kt10001"
API_STOCK_PRICE = "ka10001"
API_STOCK_DAILY = "ka10002"
API_STOCK_MINUTE = "ka10003"
API_ACCOUNT_BALANCE = "ka10070"

EP_ORDER = "/api/dostk/ordr"
EP_STOCK = "/api/dostk/stkinfo"
EP_CHART = "/api/dostk/chart"
EP_ACCOUNT = "/api/dostk/acnt"

# 주문 구분
ORDER_BUY = 1
ORDER_SELL = 2

# 호가 구분
PRICE_LIMIT = "00"   # 지정가
PRICE_MARKET = "03"  # 시장가


class KiwoomRestClient:
    """키움증권 REST API 비동기 클라이언트."""

    def __init__(
        self,
        config: KiwoomConfig,
        token_manager: TokenManager,
        rate_limiter: AsyncRateLimiter | None = None,
    ):
        self._config = config
        self._token_manager = token_manager
        self._rate_limiter = rate_limiter or AsyncRateLimiter(
            max_calls=config.rate_limit_calls,
            period=config.rate_limit_period,
        )

    async def request(
        self,
        method: str,
        endpoint: str,
        api_id: str = "",
        data: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """범용 API 요청 (재시도 + Rate Limit 포함)."""
        return await retry_async(
            self._do_request,
            method, endpoint, api_id, data, params,
            max_retries=3,
            base_delay=1.0,
        )

    async def _do_request(
        self,
        method: str,
        endpoint: str,
        api_id: str,
        data: dict | None,
        params: dict | None,
    ) -> dict:
        if self._rate_limiter:
            await self._rate_limiter.wait()

        token = await self._token_manager.get_token()
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "Authorization": f"Bearer {token}",
        }
        if api_id:
            headers["api-id"] = api_id
            headers["cont-yn"] = "N"
            headers["next-key"] = ""

        url = f"{self._config.rest_base_url}{endpoint}"

        async with aiohttp.ClientSession() as session:
            async with session.request(
                method, url, headers=headers, json=data, params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()
                result = await resp.json()
                logger.debug(f"REST {method} {endpoint} → {resp.status}")
                return result

    async def send_order(
        self,
        ticker: str,
        qty: int,
        price: int,
        side: str,
        order_type: str = PRICE_MARKET,
    ) -> dict:
        """주문 발송. side: 'buy'/'sell', order_type: '00'지정가/'03'시장가."""
        if not re.match(r"^\d{6}$", ticker):
            raise ValueError(f"잘못된 종목코드: {ticker} (6자리 숫자)")
        if qty < 1:
            raise ValueError(f"주문 수량은 1 이상: {qty}")
        if price < 0:
            raise ValueError(f"주문 가격은 0 이상: {price}")
        if side not in ("buy", "sell"):
            raise ValueError(f"잘못된 매매 구분: {side}")

        ord_tp = ORDER_BUY if side == "buy" else ORDER_SELL
        body = {
            "stk_cd": ticker,
            "ord_qty": qty,
            "ord_uv": price,
            "trde_tp": order_type,
            "ord_tp": ord_tp,
            "acnt_no": self._config.account_no,
        }
        return await self.request("POST", EP_ORDER, API_STOCK_ORDER, data=body)

    async def get_account_balance(self) -> dict:
        """계좌 잔고 조회."""
        body = {"acnt_no": self._config.account_no}
        return await self.request("POST", EP_ACCOUNT, API_ACCOUNT_BALANCE, data=body)

    async def get_current_price(self, ticker: str) -> dict:
        """현재가 조회."""
        body = {"stk_cd": ticker}
        return await self.request("POST", EP_STOCK, API_STOCK_PRICE, data=body)

    async def get_minute_ohlcv(self, ticker: str, tick_range: int = 60,
                                count: int = 100) -> dict:
        """분봉 데이터 조회."""
        body = {"stk_cd": ticker, "tick_range": str(tick_range), "count": count}
        return await self.request("POST", EP_CHART, API_STOCK_MINUTE, data=body)

    async def get_daily_ohlcv(self, ticker: str, start_date: str,
                               end_date: str) -> dict:
        """일봉 데이터 조회."""
        body = {"stk_cd": ticker, "start_date": start_date, "end_date": end_date, "period": "D"}
        return await self.request("POST", EP_CHART, API_STOCK_DAILY, data=body)
