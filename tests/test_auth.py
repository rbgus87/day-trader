"""tests/test_auth.py"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timedelta

from core.auth import TokenManager


@pytest.mark.asyncio
async def test_get_token_fetches_new():
    tm = TokenManager(app_key="k", secret_key="s", base_url="http://test")
    mock_resp = {
        "access_token": "tok123",
        "token_token_expired": (datetime.now() + timedelta(hours=12)).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    }
    with patch("core.auth.aiohttp.ClientSession") as mock_session_cls:
        mock_session = MagicMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_resp_obj = MagicMock()
        mock_resp_obj.json = AsyncMock(return_value=mock_resp)
        mock_resp_obj.raise_for_status = MagicMock()
        post_ctx = MagicMock()
        post_ctx.__aenter__ = AsyncMock(return_value=mock_resp_obj)
        post_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.post.return_value = post_ctx

        token = await tm.get_token()
        assert token == "tok123"


@pytest.mark.asyncio
async def test_get_token_reuses_valid():
    tm = TokenManager(app_key="k", secret_key="s", base_url="http://test")
    tm._access_token = "cached"
    tm._token_expires = datetime.now() + timedelta(hours=1)

    token = await tm.get_token()
    assert token == "cached"


@pytest.mark.asyncio
async def test_get_token_refreshes_near_expiry():
    tm = TokenManager(app_key="k", secret_key="s", base_url="http://test")
    tm._access_token = "old"
    tm._token_expires = datetime.now() + timedelta(minutes=5)

    mock_resp = {
        "access_token": "new_tok",
        "token_token_expired": (datetime.now() + timedelta(hours=12)).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    }
    with patch("core.auth.aiohttp.ClientSession") as mock_session_cls:
        mock_session = MagicMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_resp_obj = MagicMock()
        mock_resp_obj.json = AsyncMock(return_value=mock_resp)
        mock_resp_obj.raise_for_status = MagicMock()
        post_ctx = MagicMock()
        post_ctx.__aenter__ = AsyncMock(return_value=mock_resp_obj)
        post_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.post.return_value = post_ctx

        token = await tm.get_token()
        assert token == "new_tok"
