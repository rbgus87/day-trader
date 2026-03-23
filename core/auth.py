"""core/auth.py — OAuth2 토큰 발급 및 자동 갱신."""

from datetime import datetime, timedelta

import aiohttp
from loguru import logger


class TokenManager:
    """키움 REST API OAuth2 토큰 관리."""

    REFRESH_MARGIN = timedelta(minutes=10)

    def __init__(self, app_key: str, secret_key: str, base_url: str):
        self._app_key = app_key
        self._secret_key = secret_key
        self._base_url = base_url
        self._access_token: str | None = None
        self._token_expires: datetime | None = None

    async def get_token(self) -> str:
        """유효한 토큰 반환. 만료 임박 시 자동 갱신."""
        if self._is_valid():
            return self._access_token

        await self._fetch_token()
        return self._access_token

    def _is_valid(self) -> bool:
        if not self._access_token or not self._token_expires:
            return False
        return datetime.now() + self.REFRESH_MARGIN < self._token_expires

    async def _fetch_token(self) -> None:
        url = f"{self._base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "appsecret": self._secret_key,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body) as resp:
                resp.raise_for_status()
                data = await resp.json()

        self._access_token = data["access_token"]
        expires_str = data.get("token_token_expired", "")
        if expires_str:
            self._token_expires = datetime.strptime(expires_str, "%Y-%m-%d %H:%M:%S")
        else:
            self._token_expires = datetime.now() + timedelta(hours=12)

        logger.info(f"토큰 발급 완료 — 만료: {self._token_expires}")
