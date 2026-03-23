"""tests/test_realtime_scanner.py — RealtimeScanner 단위 테스트."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from screener.realtime_scanner import RealtimeScanner


def _make_scanner() -> tuple[RealtimeScanner, AsyncMock]:
    """테스트용 RealtimeScanner와 mock notifier를 생성한다."""
    notifier = MagicMock()
    notifier.send = AsyncMock(return_value=True)
    scanner = RealtimeScanner(notifier)
    return scanner, notifier


def _make_candle(tf: str, ticker: str, volume: int) -> dict:
    return {"tf": tf, "ticker": ticker, "volume": volume}


# ---------------------------------------------------------------------------
# 1. tf="1m" 캔들은 무시
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ignores_1m_candle():
    """1분봉 캔들은 처리하지 않고 False를 반환해야 한다."""
    scanner, notifier = _make_scanner()

    result = await scanner.on_candle(_make_candle("1m", "005930", 100_000))

    assert result is False
    notifier.send.assert_not_called()


# ---------------------------------------------------------------------------
# 2. 정상 거래량 — 알림 없음
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_alert_normal_volume():
    """거래량이 평균의 3배 미만이면 알림 없이 False를 반환해야 한다."""
    scanner, notifier = _make_scanner()
    ticker = "005930"

    # 히스토리 4개 적재 (평균 1000)
    for _ in range(4):
        await scanner.on_candle(_make_candle("5m", ticker, 1_000))

    # 현재 거래량 2999 < 1000 * 3 = 3000
    result = await scanner.on_candle(_make_candle("5m", ticker, 2_999))

    assert result is False
    notifier.send.assert_not_called()


# ---------------------------------------------------------------------------
# 3. 거래량 급등 — 알림 발송 및 True 반환
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_alert_on_volume_surge():
    """5분 거래량이 20분 평균의 3배 이상이면 알림 발송 후 True를 반환해야 한다."""
    scanner, notifier = _make_scanner()
    ticker = "000660"

    # 히스토리 4개 적재 (평균 1000)
    for _ in range(4):
        await scanner.on_candle(_make_candle("5m", ticker, 1_000))

    # 현재 거래량 3000 == 1000 * 3 → 급등 조건 충족
    result = await scanner.on_candle(_make_candle("5m", ticker, 3_000))

    assert result is True
    notifier.send.assert_called_once()
    # 알림 메시지에 종목 코드 포함 확인
    call_args = notifier.send.call_args
    assert ticker in call_args[0][0]


# ---------------------------------------------------------------------------
# 4. 히스토리 추적 — 크기 성장 및 유지
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tracks_history():
    """캔들을 처리할수록 히스토리가 쌓이고, 지정된 크기 이내로 유지되어야 한다."""
    scanner, _ = _make_scanner()
    ticker = "035420"

    # 3개 처리 → 히스토리에 3개 쌓임 (아직 급등 판정 불가)
    for i in range(3):
        await scanner.on_candle(_make_candle("5m", ticker, 500))

    assert len(scanner._volume_history[ticker]) == 3

    # 4번째 처리 → 이제 급등 판정 가능 상태
    await scanner.on_candle(_make_candle("5m", ticker, 500))
    assert len(scanner._volume_history[ticker]) == 4

    # 20개 초과 적재 시에도 최대 20개 유지
    for i in range(20):
        await scanner.on_candle(_make_candle("5m", ticker, 500))

    assert len(scanner._volume_history[ticker]) <= 20

    # reset() 후 히스토리 초기화 확인
    scanner.reset()
    assert scanner._volume_history == {}
