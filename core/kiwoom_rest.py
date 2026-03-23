"""core/kiwoom_rest.py — 키움 REST API 클라이언트."""

import aiohttp
from loguru import logger

from core.auth import TokenManager
from core.rate_limiter import AsyncRateLimiter
from core.retry import retry_async
from config.settings import KiwoomConfig


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
        tr_id: str = "",
        data: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """범용 API 요청 (재시도 + Rate Limit 포함)."""
        return await retry_async(
            self._do_request,
            method, endpoint, tr_id, data, params,
            max_retries=3,
            base_delay=1.0,
        )

    async def _do_request(
        self,
        method: str,
        endpoint: str,
        tr_id: str,
        data: dict | None,
        params: dict | None,
    ) -> dict:
        if self._rate_limiter:
            await self._rate_limiter.wait()

        token = await self._token_manager.get_token()
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self._config.app_key,
        }
        if tr_id:
            headers["tr_id"] = tr_id

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
        order_type: str = "00",
    ) -> dict:
        import re
        if not re.match(r"^\d{6}$", ticker):
            raise ValueError(f"잘못된 종목코드: {ticker} (6자리 숫자)")
        if qty < 1:
            raise ValueError(f"주문 수량은 1 이상: {qty}")
        if price < 0:
            raise ValueError(f"주문 가격은 0 이상: {price}")
        if side not in ("buy", "sell"):
            raise ValueError(f"잘못된 매매 구분: {side}")
        # 모의투자: VTTC, 실거래: TTTC
        prefix = "VTTC" if self._config.paper_trading else "TTTC"
        tr_id = f"{prefix}0802U" if side == "buy" else f"{prefix}0801U"
        body = {
            "CANO": self._config.account_no[:8],
            "ACNT_PRDT_CD": self._config.account_no[8:] or "01",
            "PDNO": ticker,
            "ORD_DVSN": order_type,
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price),
        }
        return await self.request("POST", "/uapi/domestic-stock/v1/trading/order-cash", tr_id=tr_id, data=body)

    async def get_account_balance(self) -> dict:
        params = {
            "CANO": self._config.account_no[:8],
            "ACNT_PRDT_CD": self._config.account_no[8:] or "01",
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        return await self.request(
            "GET", "/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id=f"{'VTTC' if self._config.paper_trading else 'TTTC'}8434R",
            params=params,
        )

    async def get_current_price(self, ticker: str) -> dict:
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
        return await self.request(
            "GET", "/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id="FHKST01010100", params=params,
        )

    async def get_minute_ohlcv(self, ticker: str, time_unit: str = "1") -> dict:
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
            "FID_ETC_CLS_CODE": "",
            "FID_INPUT_HOUR_1": time_unit,
            "FID_PW_DATA_INCU_YN": "Y",
        }
        return await self.request(
            "GET", "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            tr_id="FHKST03010200", params=params,
        )
