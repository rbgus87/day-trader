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


def test_send_plain_text_omits_parse_mode(notifier):
    """parse_mode='' 로 발송 시 요청 body에 parse_mode 키가 없어야 한다."""
    with patch("notification.telegram_bot.requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        notifier._send_sync("-> 특수문자 <- [] () + - . %", "", retries=1, retry_sleep_sec=0)
        payload = mock_post.call_args.kwargs["json"]
        assert "parse_mode" not in payload


def test_send_html_mode_includes_parse_mode(notifier):
    """parse_mode='HTML' 로 발송 시 요청 body에 parse_mode 키가 포함된다."""
    with patch("notification.telegram_bot.requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        notifier._send_sync("<b>굵은</b> 텍스트", "HTML", retries=1, retry_sleep_sec=0)
        payload = mock_post.call_args.kwargs["json"]
        assert payload.get("parse_mode") == "HTML"


def test_send_special_chars_plain_no_400(notifier):
    """-> <- [] () + - . % 특수문자 메시지를 plain text로 발송하면 성공(200) 반환."""
    msg = "[SHADOW] 시장 필터 차단 1건\n  377480: 차단가 19,390 -> 현재 17,838 (-8.0%) (손절) [최고 +6.2%] <- 차단 정당"
    with patch("notification.telegram_bot.requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        ok = notifier._send_sync(msg, "", retries=1, retry_sleep_sec=0)
        assert ok is True
        payload = mock_post.call_args.kwargs["json"]
        assert "parse_mode" not in payload
        assert payload["text"] == msg


def test_send_truncates_long_message(notifier):
    """4096자 초과 메시지는 _MAX_MSG_LEN 이하로 잘리고 '…(생략)' 이 붙는다."""
    long_msg = "가" * 5000
    with patch("notification.telegram_bot.requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        notifier._send_sync(long_msg, "HTML", retries=1, retry_sleep_sec=0)
        sent_text = mock_post.call_args.kwargs["json"]["text"]
        assert len(sent_text) <= notifier._MAX_MSG_LEN + len("\n…(생략)")
        assert sent_text.endswith("\n…(생략)")


def test_send_400_logs_description(notifier):
    """400 응답 시 Telegram API description 필드가 경고 로그에 포함된다."""
    from loguru import logger

    messages: list[str] = []
    sink_id = logger.add(lambda msg: messages.append(msg), level="WARNING")
    try:
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"ok": False, "description": "Can't parse entities"}
        with patch("notification.telegram_bot.requests.post", return_value=mock_resp):
            notifier._send_sync("bad <html", "HTML", retries=1, retry_sleep_sec=0)
    finally:
        logger.remove(sink_id)
    assert any("Can't parse entities" in m for m in messages)
