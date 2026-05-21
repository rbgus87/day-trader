"""시그널 히스토리 타임라인 — 당일 진입/청산/차단 시그널을 시간순으로 표시."""
from __future__ import annotations

import re
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from gui.design_tokens import Colors
from gui.widgets.card import Card

_BLOCKED_PATTERNS = re.compile(
    r"매수 차단|VI 활성|MARKET|장중 필터|intraday.block|signal_blocked|약세.*차단|차단.*약세",
    re.IGNORECASE,
)

_REASON_MAP = {
    "limit_up_exit": "상한가",
    "stop_loss": "손절",
    "trailing_stop": "트레일",
    "breakeven_stop": "BE",
    "momentum_fade": "FADE",
    "forced_close": "강제청산",
    "stale_exit": "횡보",
    "entry": "진입",
    "tp1_hit": "TP1",
    "ws_filled": "체결확인",
}


class SignalTimeline(QWidget):
    """당일 시그널 히스토리 패널."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._card = Card(title="시그널 히스토리")
        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{ background: transparent; border: none; outline: none; }}"
            f"QListWidget::item {{ "
            f"  padding: 3px 6px; "
            f"  border-bottom: 1px solid {Colors.surface}; "
            f"}}"
            f"QListWidget::item:selected {{ background: {Colors.surface_elevated}; }}"
        )
        self._list.setFont(QFont("Malgun Gothic", 10))
        self._list.setAlternatingRowColors(False)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._card.addWidget(self._list, stretch=1)
        layout.addWidget(self._card)

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def add_trade(self, trade: dict):
        """trade_executed 시그널에서 호출 — 체결 이벤트 추가."""
        side = (trade.get("side", "") or "").lower()
        ticker = trade.get("ticker", "")
        price = int(trade.get("price", 0) or 0)
        pnl = trade.get("pnl")
        reason_raw = trade.get("reason", "") or ""
        ts = trade.get("time", "") or datetime.now().strftime("%H:%M:%S")

        reason = _REASON_MAP.get(reason_raw.lower(), reason_raw[:6] or "?")

        if side == "buy":
            strat = (trade.get("strategy", "") or "").lower()
            strat_tag = "[ORB] " if strat == "orb" else "[MOM] " if strat == "momentum" else ""
            text = f"{ts}  📈 {strat_tag}{ticker}  매수  {price:,}원  [{reason}]"
            color = Colors.accent_blue
        else:
            pnl_v = int(pnl or 0)
            sign = "+" if pnl_v >= 0 else ""
            emoji = "✅" if pnl_v >= 0 else "❌"
            text = (
                f"{ts}  {emoji} {ticker}  매도  {price:,}원  "
                f"{sign}{pnl_v:,}원  [{reason}]"
            )
            color = Colors.accent_green if pnl_v >= 0 else Colors.accent_red

        self._prepend(text, color)

    def add_log(self, text: str, level: str):
        """_log_signal에서 필터링 후 차단 이벤트 추가."""
        if not _BLOCKED_PATTERNS.search(text):
            return

        m = re.search(r'\[(\d{2}:\d{2}:\d{2})\]', text)
        ts = m.group(1) if m else datetime.now().strftime("%H:%M:%S")

        # 로그 메시지에서 핵심 부분 추출 (마지막 '] ' 이후)
        parts = text.split("] ")
        msg = parts[-1].strip() if len(parts) > 1 else text.strip()
        msg = msg[:72]

        self._prepend(f"{ts}  🚫 {msg}", Colors.text_muted)

    def clear_today(self):
        self._list.clear()

    # ── 내부 ──────────────────────────────────────────────────────────────────

    def _prepend(self, text: str, color: str):
        item = QListWidgetItem(text)
        item.setForeground(QColor(color))
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._list.insertItem(0, item)
        if self._list.count() > 150:
            self._list.takeItem(self._list.count() - 1)
