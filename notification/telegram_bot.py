"""notification/telegram_bot.py — 비동기 텔레그램 알림."""

import time

import aiohttp
from loguru import logger

from config.settings import TelegramConfig


class TelegramNotifier:
    """aiohttp 기반 비동기 텔레그램 알림."""

    def __init__(self, config: TelegramConfig):
        self._token = config.bot_token
        self._chat_id = config.chat_id
        self._api_url = f"https://api.telegram.org/bot{self._token}"
        self._cooldowns: dict[str, float] = {}

    def __repr__(self) -> str:
        return f"TelegramNotifier(chat_id={self._chat_id!r})"

    async def send(
        self,
        message: str,
        parse_mode: str = "HTML",
        retries: int = 1,
    ) -> bool:
        payload = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": parse_mode,
        }
        for attempt in range(retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{self._api_url}/sendMessage", json=payload,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status == 200:
                            return True
                        logger.warning(f"텔레그램 발송 실패: status={resp.status}")
            except Exception as e:
                logger.error(f"텔레그램 발송 오류 (시도 {attempt + 1}): {e}")
        return False

    async def send_with_cooldown(
        self, key: str, message: str, cooldown_sec: float = 60.0,
    ) -> bool:
        now = time.monotonic()
        if key in self._cooldowns and now - self._cooldowns[key] < cooldown_sec:
            return False
        self._cooldowns[key] = now
        return await self.send(message)

    async def send_buy_signal(
        self, ticker: str, name: str, strategy: str, price: int, reason: str,
    ) -> bool:
        msg = (
            f"🟢 <b>매수 신호</b>\n"
            f"종목: {name} ({ticker})\n"
            f"전략: {strategy.upper()}\n"
            f"가격: {price:,}원\n"
            f"사유: {reason}"
        )
        return await self.send(msg)

    async def send_execution(
        self, ticker: str, name: str, side: str, price: int, qty: int, amount: int,
    ) -> bool:
        emoji = "🔵" if side == "buy" else "🔴"
        label = "매수" if side == "buy" else "매도"
        msg = (
            f"{emoji} <b>{label} 체결</b>\n"
            f"종목: {name} ({ticker})\n"
            f"가격: {price:,}원 × {qty}주\n"
            f"금액: {amount:,}원"
        )
        return await self.send(msg)

    async def send_stop_loss(
        self, ticker: str, name: str, entry_price: int, exit_price: int, pnl_pct: float,
    ) -> bool:
        msg = (
            f"🛑 <b>손절 실행</b>\n"
            f"종목: {name} ({ticker})\n"
            f"진입가: {entry_price:,} → 청산가: {exit_price:,}\n"
            f"손익: {pnl_pct:+.2%}"
        )
        return await self.send(msg)

    async def send_daily_report(
        self, date: str, total_trades: int, wins: int, total_pnl: int, win_rate: float, strategy: str,
    ) -> bool:
        msg = (
            f"📊 <b>일일 성과 보고서</b>\n"
            f"날짜: {date}\n"
            f"전략: {strategy}\n"
            f"매매: {total_trades}건 (승: {wins})\n"
            f"승률: {win_rate:.1%}\n"
            f"손익: {total_pnl:+,}원"
        )
        return await self.send(msg)

    async def send_urgent(self, message: str) -> bool:
        msg = f"🚨 <b>긴급</b>\n{message}"
        return await self.send(msg, retries=3)

    async def send_no_trade(self, reason: str) -> bool:
        msg = f"⏸️ <b>당일 매매 없음</b>\n사유: {reason}"
        return await self.send(msg)

    async def send_system_start(self) -> bool:
        return await self.send("🚀 <b>단타 매매 시스템 시작</b>")

    async def send_system_stop(self, reason: str = "정상 종료") -> bool:
        return await self.send(f"⏹️ <b>시스템 종료</b>\n사유: {reason}")
