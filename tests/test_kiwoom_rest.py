"""tests/test_kiwoom_rest.py — 키움 REST API 테스트."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from core.kiwoom_rest import KiwoomRestClient
from config.settings import KiwoomConfig


@pytest.fixture
def kiwoom_config():
    return KiwoomConfig(
        app_key="test_key",
        secret_key="test_secret",
        account_no="12345678",
    )


@pytest.fixture
def rest_client(kiwoom_config):
    return KiwoomRestClient(
        config=kiwoom_config,
        token_manager=AsyncMock(get_token=AsyncMock(return_value="test_token")),
    )


@pytest.mark.asyncio
async def test_request_adds_auth_headers(rest_client):
    with patch("core.kiwoom_rest.aiohttp.ClientSession") as mock_cls:
        mock_session = MagicMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"output": []})
        mock_resp.raise_for_status = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.request.return_value = mock_ctx

        result = await rest_client.request("POST", "/test", api_id="ka10001")
        call_args = mock_session.request.call_args
        headers = call_args.kwargs.get("headers", {})
        assert headers["Authorization"] == "Bearer test_token"
        assert headers["api-id"] == "ka10001"


@pytest.mark.asyncio
async def test_get_account_balance(rest_client):
    mock_data = {"balance": [{"stk_cd": "005930", "hldg_qty": 10}]}
    with patch.object(rest_client, "request", new_callable=AsyncMock, return_value=mock_data):
        result = await rest_client.get_account_balance()
        assert result == mock_data


@pytest.mark.asyncio
async def test_send_order_validation(rest_client):
    """잘못된 종목코드 → ValueError."""
    with pytest.raises(ValueError, match="잘못된 종목코드"):
        await rest_client.send_order(ticker="ABC", qty=10, price=70000, side="buy")

    with pytest.raises(ValueError, match="주문 수량"):
        await rest_client.send_order(ticker="005930", qty=0, price=70000, side="buy")
