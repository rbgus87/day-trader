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
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"output": []})
    mock_resp.raise_for_status = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.request.return_value = mock_ctx
    rest_client._session = mock_session

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


# ---------------------------------------------------------------------------
# get_stock_info / get_market_snapshot 테스트
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_stock_info(rest_client):
    """get_stock_info가 응답 필드를 올바르게 매핑한다."""
    raw = {
        "output1": {
            "strt_pric": "70000",
            "high_pric": "72000",
            "low_pric": "69000",
            "cur_pric": "71500",
            "base_pric": "69500",
            "trde_qty": "1000000",
        },
    }
    with patch.object(rest_client, "get_current_price", new_callable=AsyncMock, return_value=raw):
        info = await rest_client.get_stock_info("069500")

    assert info["open"] == 70000
    assert info["high"] == 72000
    assert info["low"] == 69000
    assert info["close"] == 71500
    assert info["prev_close"] == 69500
    assert info["volume"] == 1000000


@pytest.mark.asyncio
async def test_get_market_snapshot(rest_client):
    """get_market_snapshot이 KOSPI 갭/섹터ETF/변동폭을 계산한다."""
    async def mock_stock_info(ticker):
        if ticker == "069500":
            return {"open": 70000, "high": 71000, "low": 69500, "close": 70500, "prev_close": 69500, "volume": 100}
        # 섹터 ETF: 반도체만 +2% 상승
        if ticker == "091160":
            return {"open": 10000, "high": 10200, "low": 9900, "close": 10200, "prev_close": 10000, "volume": 50}
        return {"open": 10000, "high": 10050, "low": 9950, "close": 10010, "prev_close": 10000, "volume": 50}

    with patch.object(rest_client, "get_stock_info", side_effect=mock_stock_info):
        snapshot = await rest_client.get_market_snapshot()

    # KOSPI 갭: (70000 - 69500) / 69500 * 100 ≈ 0.7194%
    assert abs(snapshot["kospi_gap_pct"] - 0.7194) < 0.01
    # 섹터 ETF 최대 등락: 반도체 +2%
    assert abs(snapshot["sector_etf_change_pct"] - 2.0) < 0.01
    assert snapshot["top_sector"] == "반도체"
    # 변동폭: (71000 - 69500) / 70000 * 100 ≈ 2.1429%
    assert snapshot["index_range_pct"] > 2.0


@pytest.mark.asyncio
async def test_get_market_snapshot_api_failure(rest_client):
    """KOSPI ETF 조회 실패 시 안전한 기본값 반환."""
    with patch.object(rest_client, "get_stock_info", side_effect=Exception("API error")):
        snapshot = await rest_client.get_market_snapshot()

    assert snapshot["kospi_gap_pct"] == 0.0
    assert snapshot["sector_etf_change_pct"] == 0.0
    assert snapshot["index_range_pct"] == 0.0
