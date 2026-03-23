"""screener/realtime_scanner.py — 장 중 거래량 급등 모니터링 (PRD F-SCR-03)."""

from notification.telegram_bot import TelegramNotifier


class RealtimeScanner:
    """5분 거래량이 20분 평균의 3배 이상 시 알림을 발송하는 실시간 스캐너."""

    def __init__(self, notifier: TelegramNotifier) -> None:
        self._notifier = notifier
        self._volume_history: dict[str, list[int]] = {}

    async def on_candle(self, candle: dict) -> bool:
        """5분봉 캔들을 처리하고 거래량 급등 여부를 반환한다.

        Args:
            candle: 캔들 데이터. 필수 키: "tf", "ticker", "volume"

        Returns:
            거래량 급등 알림이 발송된 경우 True, 아니면 False.
        """
        if candle.get("tf") != "5m":
            return False

        ticker: str = candle["ticker"]
        volume: int = int(candle["volume"])

        history = self._volume_history.setdefault(ticker, [])

        if len(history) >= 4:
            avg_volume = sum(history[-4:]) / 4
            if avg_volume > 0 and volume >= avg_volume * 3:
                msg = (
                    f"📈 <b>거래량 급등</b>\n"
                    f"종목: {ticker}\n"
                    f"현재 5분 거래량: {volume:,}\n"
                    f"20분 평균: {avg_volume:,.0f}\n"
                    f"배율: {volume / avg_volume:.1f}배"
                )
                await self._notifier.send(msg)
                history.append(volume)
                # 최대 20개 항목 유지 (불필요한 메모리 누적 방지)
                if len(history) > 20:
                    self._volume_history[ticker] = history[-20:]
                return True

        history.append(volume)
        if len(history) > 20:
            self._volume_history[ticker] = history[-20:]
        return False

    def reset(self) -> None:
        """거래량 히스토리를 초기화한다."""
        self._volume_history.clear()
