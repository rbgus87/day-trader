"""tests/test_telegram_bot.py — sync send + ThreadPool fire-and-forget 테스트."""

from unittest.mock import MagicMock, patch

import pytest

from notification.telegram_bot import TelegramNotifier
from config.settings import TelegramConfig


@pytest.fixture
def notifier():
    n = TelegramNotifier(TelegramConfig(bot_token="test_token", chat_id="test_chat"))
    yield n
    n.aclose()


def test_send_sync_success(notifier):
    """_send_sync가 200 응답에서 True를 반환한다."""
    with patch("notification.telegram_bot.requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        ok = notifier._send_sync("테스트", "HTML", retries=1, retry_sleep_sec=0)
        assert ok is True
        mock_post.assert_called_once()
        kwargs = mock_post.call_args.kwargs
        assert kwargs["json"]["text"] == "테스트"
        assert kwargs["timeout"] == 30


def test_send_sync_retries_on_failure(notifier):
    """실패 시 retry_sleep_sec 후 재시도."""
    with patch("notification.telegram_bot.requests.post") as mock_post:
        mock_post.side_effect = Exception("ConnectionTimeout")
        ok = notifier._send_sync("테스트", "HTML", retries=2, retry_sleep_sec=0)
        assert ok is False
        assert mock_post.call_count == 2


def test_send_dispatches_to_executor(notifier):
    """send()는 ThreadPool에 fire-and-forget."""
    with patch.object(notifier, "_executor") as mock_exec:
        notifier.send("테스트")
        mock_exec.submit.assert_called_once()


def test_send_buy_signal_format(notifier):
    """send_buy_signal이 send에 정상 포맷 문자열을 넘긴다."""
    with patch.object(notifier, "send") as mock_send:
        notifier.send_buy_signal(
            ticker="005930", name="삼성전자",
            strategy="orb", price=70000, reason="ORB 상단 돌파",
        )
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "삼성전자" in msg
        assert "70,000" in msg


def test_send_urgent_uses_3_retries(notifier):
    """send_urgent는 retries=3."""
    with patch.object(notifier, "send") as mock_send:
        notifier.send_urgent("손절 주문 실패!")
        mock_send.assert_called_once()
        assert mock_send.call_args.kwargs.get("retries") == 3


def test_aclose_shuts_down_executor():
    """aclose가 executor를 종료하고 closed 플래그를 세팅."""
    n = TelegramNotifier(TelegramConfig(bot_token="t", chat_id="c"))
    n.aclose()
    assert n._closed is True
    # 종료 후 send는 no-op (예외 없이 반환)
    n.send("post-close")
