"""notification/telegram_bot.py — 텔레그램 알림 (requests + ThreadPool, swing-trader 패턴).

aiohttp 싱글턴 세션의 stale keep-alive 문제를 회피하기 위해 매 호출마다
새 TCP 커넥션을 사용하는 requests로 전환. asyncio 이벤트 루프 블로킹을
피하려고 ThreadPoolExecutor(max_workers=1)에 fire-and-forget으로 위임한다.

호출부는 `await` 없이 직접 호출 — 결과(성공/실패)는 워커 스레드 로그로만 확인.
"""

import concurrent.futures
import time

import requests
from loguru import logger

from config.settings import TelegramConfig


class TelegramNotifier:
    """텔레그램 봇 알림 — requests + ThreadPool fire-and-forget."""

    def __init__(self, config: TelegramConfig):
        self._token = config.bot_token
        self._chat_id = config.chat_id
        self._api_url = f"https://api.telegram.org/bot{self._token}"
        self._cooldowns: dict[str, float] = {}
        self._closed = False
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="telegram",
        )

    def __repr__(self) -> str:
        return f"TelegramNotifier(chat_id={self._chat_id!r})"

    def send(
        self,
        message: str,
        parse_mode: str = "HTML",
        retries: int = 2,
        retry_sleep_sec: int = 30,
    ) -> None:
        """메시지 발송을 ThreadPool에 위임 — fire-and-forget.

        호출 즉시 반환되며 메인 이벤트 루프를 블로킹하지 않는다.
        실패는 워커 스레드 로그로만 노출된다 (반환값 없음).
        """
        if self._closed:
            return
        try:
            self._executor.submit(
                self._send_sync, message, parse_mode, retries, retry_sleep_sec,
            )
        except RuntimeError:
            # executor가 이미 shutdown된 경우 — 종료 시점 race
            logger.debug("텔레그램 전송 스킵 — executor 종료됨")

    def _send_sync(
        self,
        message: str,
        parse_mode: str,
        retries: int,
        retry_sleep_sec: int,
    ) -> bool:
        """실제 HTTP 발송 — 워커 스레드에서 실행 (블로킹 I/O 격리)."""
        last_err = ""
        for attempt in range(retries):
            try:
                resp = requests.post(
                    f"{self._api_url}/sendMessage",
                    json={
                        "chat_id": self._chat_id,
                        "text": message,
                        "parse_mode": parse_mode,
                    },
                    timeout=30,
                )
                if resp.status_code == 200:
                    return True
                last_err = f"status={resp.status_code}"
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"

            is_last = attempt == retries - 1
            if is_last:
                logger.warning(f"텔레그램 발송 최종 실패 (무시): {last_err}")
            else:
                logger.warning(
                    f"텔레그램 발송 실패, {retry_sleep_sec}초 후 재시도: {last_err}"
                )
                time.sleep(retry_sleep_sec)
        return False

    def send_with_cooldown(
        self, key: str, message: str, cooldown_sec: float = 60.0,
    ) -> None:
        now = time.monotonic()
        if key in self._cooldowns and now - self._cooldowns[key] < cooldown_sec:
            return
        self._cooldowns[key] = now
        self.send(message)

    def aclose(self) -> None:
        """ThreadPool 정리 — 대기 중 task 취소, 실행 중 task는 완료까지 대기."""
        if self._closed:
            return
        self._closed = True
        try:
            self._executor.shutdown(wait=True, cancel_futures=True)
        except TypeError:
            # Python < 3.9
            self._executor.shutdown(wait=True)

    def send_buy_signal(
        self, ticker: str, name: str, strategy: str, price: int, reason: str,
    ) -> None:
        msg = (
            f"🟢 <b>매수 신호</b>\n"
            f"종목: {name} ({ticker})\n"
            f"전략: {strategy.upper()}\n"
            f"가격: {price:,}원\n"
            f"사유: {reason}"
        )
        self.send(msg)

    def send_execution(
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
    ) -> None:
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
        self.send("\n".join(lines))

    def send_stop_loss(
        self, ticker: str, name: str, entry_price: int, exit_price: int, pnl_pct: float,
    ) -> None:
        msg = (
            f"🛑 <b>손절 실행</b>\n"
            f"종목: {name} ({ticker})\n"
            f"진입가: {entry_price:,} → 청산가: {exit_price:,}\n"
            f"손익: {pnl_pct:+.2%}"
        )
        self.send(msg)

    def send_daily_report(
        self,
        date: str,
        total_trades: int,
        wins: int,
        losses: int,
        total_pnl: int,
        win_rate: float,
        strategy: str,
        max_drawdown: float = 0.0,
    ) -> None:
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
        self.send(msg)

    def send_urgent(self, message: str) -> None:
        msg = f"🚨 <b>긴급</b>\n{message}"
        self.send(msg, retries=3)

    def send_no_trade(self, reason: str) -> None:
        msg = f"⏸️ <b>당일 매매 없음</b>\n사유: {reason}"
        self.send(msg)

    def send_system_start(self) -> None:
        self.send("🚀 <b>단타 매매 시스템 시작</b>")

    def send_system_stop(self, reason: str = "정상 종료") -> None:
        self.send(f"⏹️ <b>시스템 종료</b>\n사유: {reason}")
