"""tests/test_telegram_bot.py"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from notification.telegram_bot import TelegramNotifier
from config.settings import TelegramConfig


@pytest.fixture
def notifier():
    return TelegramNotifier(TelegramConfig(bot_token="test_token", chat_id="test_chat"))


@pytest.mark.asyncio
async def test_send_message(notifier):
    """세션 재사용 기반 send() 테스트."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.post.return_value = mock_ctx
    mock_session.closed = False

    # _get_session()이 mock 세션을 반환하도록 패치
    notifier._session = mock_session

    ok = await notifier.send("테스트 메시지")
    assert ok is True
    mock_session.post.assert_called_once()


@pytest.mark.asyncio
async def test_send_buy_signal(notifier):
    with patch.object(notifier, "send", new_callable=AsyncMock, return_value=True) as mock_send:
        await notifier.send_buy_signal(
            ticker="005930", name="삼성전자",
            strategy="orb", price=70000, reason="ORB 상단 돌파",
        )
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "삼성전자" in msg
        assert "70,000" in msg


@pytest.mark.asyncio
async def test_send_urgent_retries(notifier):
    with patch.object(notifier, "send", new_callable=AsyncMock, return_value=True) as mock_send:
        await notifier.send_urgent("손절 주문 실패!")
        mock_send.assert_called_once()
        args = mock_send.call_args
        assert args.kwargs.get("retries", 1) == 3


@pytest.mark.asyncio
async def test_aclose(notifier):
    """aclose()가 세션을 정리."""
    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.close = AsyncMock()
    notifier._session = mock_session

    await notifier.aclose()
    mock_session.close.assert_called_once()
    assert notifier._session is None
