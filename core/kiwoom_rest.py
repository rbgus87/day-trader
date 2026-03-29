"""core/kiwoom_rest.py — 키움 OpenAPI+ REST 클라이언트."""

import asyncio
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
API_STOCK_DAILY = "ka10081"
API_STOCK_MINUTE = "ka10080"
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

# 시장 지수 / ETF 종목코드
KOSPI_ETF = "069500"       # KODEX 200 (KOSPI 대표 ETF)
SECTOR_ETFS = {
    "반도체": "091160",     # KODEX 반도체
    "2차전지": "305720",    # KODEX 2차전지산업
    "바이오": "244580",     # KODEX 바이오
    "자동차": "091170",     # KODEX 자동차
    "금융": "102110",       # TIGER 200 금융
    "철강": "117680",       # KODEX 철강
}


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
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """TCP 연결 풀을 재사용하는 세션을 반환한다."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
            )
        return self._session

    async def aclose(self) -> None:
        """HTTP 세션을 닫는다. 애플리케이션 종료 시 호출."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

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
        session = await self._get_session()

        async with session.request(
            method, url, headers=headers, json=data, params=params,
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

    async def get_minute_ohlcv(self, ticker: str, tic_scope: int = 1,
                                base_dt: str = "") -> dict:
        """분봉 데이터 조회.

        Args:
            ticker: 종목코드
            tic_scope: 분봉 단위 (1/3/5/10/15/30/45/60)
            base_dt: 기준일자 YYYYMMDD (빈 문자열이면 최신)
        """
        body = {"stk_cd": ticker, "tic_scope": str(tic_scope), "upd_stkpc_tp": "0"}
        if base_dt:
            body["base_dt"] = base_dt
        return await self.request("POST", EP_CHART, API_STOCK_MINUTE, data=body)

    async def get_daily_ohlcv(self, ticker: str, base_dt: str = "") -> dict:
        """일봉 데이터 조회.

        Args:
            ticker: 종목코드
            base_dt: 기준일자 YYYYMMDD (해당일 이전 데이터 반환)
        """
        body = {"stk_cd": ticker, "upd_stkpc_tp": "0"}
        if base_dt:
            body["base_dt"] = base_dt
        return await self.request("POST", EP_CHART, API_STOCK_DAILY, data=body)

    async def get_stock_info(self, ticker: str) -> dict:
        """종목 현재가 정보를 정리된 dict로 반환한다.

        Returns:
            open, high, low, close, prev_close, volume 키를 포함하는 dict.
            API 응답의 output1 필드를 매핑한다.
        """
        raw = await self.get_current_price(ticker)
        out = raw.get("output1", {})

        return {
            "open": abs(int(out.get("strt_pric", 0))),
            "high": abs(int(out.get("high_pric", 0))),
            "low": abs(int(out.get("low_pric", 0))),
            "close": abs(int(out.get("cur_pric", 0))),
            "prev_close": abs(int(out.get("base_pric", 0))),
            "volume": abs(int(out.get("trde_qty", 0))),
        }

    async def get_market_snapshot(self) -> dict:
        """시장 지수/섹터 ETF 스냅샷을 반환한다.

        KOSPI 대표 ETF 시가갭, 섹터 ETF 등락률, 장중 변동폭을 계산한다.
        API 호출 실패 시 안전한 기본값(0.0)을 반환한다.

        Returns:
            kospi_gap_pct: (시가 - 전일종가) / 전일종가 * 100
            sector_etf_change_pct: 섹터 ETF 중 최대 등락률 (%)
            top_sector: 최대 등락 섹터명
            index_range_pct: (고가 - 저가) / 시가 * 100
        """
        defaults = {
            "kospi_gap_pct": 0.0,
            "sector_etf_change_pct": 0.0,
            "top_sector": "",
            "index_range_pct": 0.0,
        }

        # KOSPI ETF 조회
        try:
            kospi = await self.get_stock_info(KOSPI_ETF)
        except Exception as e:
            logger.warning(f"KOSPI ETF({KOSPI_ETF}) 조회 실패, 기본값 반환: {e}")
            return defaults

        prev_close = kospi["prev_close"]
        open_price = kospi["open"]

        if prev_close == 0:
            kospi_gap_pct = 0.0
        else:
            kospi_gap_pct = (open_price - prev_close) / prev_close * 100

        if open_price == 0:
            index_range_pct = 0.0
        else:
            index_range_pct = (kospi["high"] - kospi["low"]) / open_price * 100

        # 섹터 ETF 병렬 조회
        async def _fetch_sector(name: str, code: str) -> tuple[str, dict | None]:
            """섹터 ETF 한 종목을 조회하고 (섹터명, 결과) 반환."""
            try:
                info = await self.get_stock_info(code)
                return name, info
            except Exception as e:
                logger.warning(f"섹터 ETF {name}({code}) 조회 실패, 건너뜀: {e}")
                return name, None

        tasks = [
            _fetch_sector(name, code)
            for name, code in SECTOR_ETFS.items()
        ]
        results = await asyncio.gather(*tasks)

        # 최대 등락 섹터 계산
        top_sector = ""
        max_change_pct = 0.0

        for name, info in results:
            if info is None:
                continue
            pc = info["prev_close"]
            if pc == 0:
                continue
            change_pct = (info["close"] - pc) / pc * 100
            if abs(change_pct) > abs(max_change_pct):
                max_change_pct = change_pct
                top_sector = name

        if top_sector:
            logger.info(
                f"시장 스냅샷 — 갭: {kospi_gap_pct:+.2f}%, "
                f"최대 등락 섹터: {top_sector} ({max_change_pct:+.2f}%), "
                f"장중 변동폭: {index_range_pct:.2f}%"
            )

        return {
            "kospi_gap_pct": round(kospi_gap_pct, 4),
            "sector_etf_change_pct": round(max_change_pct, 4),
            "top_sector": top_sector,
            "index_range_pct": round(index_range_pct, 4),
        }
