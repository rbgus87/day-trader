"""notification/telegram_bot.py — 비동기 텔레그램 알림."""

import time

import aiohttp
from loguru import logger

from config.settings import TelegramConfig


class TelegramNotifier:
    """aiohttp 기반 비동기 텔레그램 알림 (세션 재사용)."""

    def __init__(self, config: TelegramConfig):
        self._token = config.bot_token
        self._chat_id = config.chat_id
        self._api_url = f"https://api.telegram.org/bot{self._token}"
        self._cooldowns: dict[str, float] = {}
        self._session: aiohttp.ClientSession | None = None

    def __repr__(self) -> str:
        return f"TelegramNotifier(chat_id={self._chat_id!r})"

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
            )
        return self._session

    async def aclose(self) -> None:
        """세션 정리."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

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
        session = await self._get_session()
        for attempt in range(retries):
            try:
                async with session.post(
                    f"{self._api_url}/sendMessage", json=payload,
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
        self,
        ticker: str,
        name: str,
        side: str,
        price: int,
        qty: int,
        amount: int,
        *,
        mode: str = "live",
        reason: str = "",
        pnl: int | None = None,
        pnl_pct: float | None = None,
    ) -> bool:
        """체결 알림 통일 포맷 (ADR-008).

        mode: 'live' / 'paper'. paper면 제목에 [PAPER] 태그.
        reason: sell 시 'stop_loss'/'tp1_hit' 등 청산 사유 (선택).
        pnl, pnl_pct: sell 시 손익 (선택).
        """
        reason_map = {
            "stop_loss": "손절",
            "tp1_hit": "1차 익절",
            "trailing_stop": "트레일링",
            "forced_close": "강제 청산",
            "rebuild_stop": "재조립 청산",
        }
        reason_label = reason_map.get(reason, reason or "")
        emoji = "🔵" if side == "buy" else "🔴"
        label = "매수" if side == "buy" else "매도"
        tag = "[PAPER] " if mode == "paper" else ""
        title_suffix = f" ({reason_label})" if reason_label else ""
        lines = [
            f"{emoji} <b>{tag}{label} 체결{title_suffix}</b>",
            f"종목: {name} ({ticker})",
            f"가격: {price:,}원 × {qty}주",
            f"금액: {amount:,}원",
        ]
        if pnl is not None and side == "sell":
            pct_str = f" ({pnl_pct:+.2f}%)" if pnl_pct is not None else ""
            lines.append(f"손익: {pnl:+,}원{pct_str}")
        return await self.send("\n".join(lines))

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
        self,
        date: str,
        total_trades: int,
        wins: int,
        losses: int,
        total_pnl: int,
        win_rate: float,
        strategy: str,
        max_drawdown: float = 0.0,
    ) -> bool:
        pnl_emoji = "📈" if total_pnl >= 0 else "📉"
        msg = (
            f"📊 <b>일일 성과 보고서</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"날짜: {date}\n"
            f"전략: {strategy}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"매매: {total_trades}건 (승 {wins} / 패 {losses})\n"
            f"승률: {win_rate:.1%}\n"
            f"{pnl_emoji} 손익: {total_pnl:+,}원\n"
            f"최대낙폭: {max_drawdown:,.0f}원"
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
