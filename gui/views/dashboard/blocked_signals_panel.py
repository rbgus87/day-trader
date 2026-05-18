"""차단된 시그널 실시간 패널 — shadow_tracker 섀도우 포지션 표시."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from gui.design_tokens import Colors
from gui.widgets.card import Card


class BlockedSignalsPanel(QWidget):
    """시장 필터 차단 시그널의 가상 PnL 패널.

    shadow_tracker.get_summary()["positions"] 리스트를 받아
    각 차단 종목의 현재가·PnL을 실시간으로 표시한다.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._card = Card(title="차단된 시그널")

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{ background: transparent; border: none; outline: none; }}"
            f"QListWidget::item {{"
            f"  padding: 4px 6px;"
            f"  border-bottom: 1px solid {Colors.surface};"
            f"}}"
            f"QListWidget::item:selected {{ background: {Colors.surface_elevated}; }}"
        )
        self._list.setFont(QFont("Malgun Gothic", 10))
        self._list.setAlternatingRowColors(False)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._card.addWidget(self._list, stretch=1)

        self._summary_lbl = QLabel("차단 없음")
        self._summary_lbl.setStyleSheet(
            f"color: {Colors.text_muted}; font-size: 11px; padding: 2px 4px;"
        )
        self._card.addWidget(self._summary_lbl)

        layout.addWidget(self._card)

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def update_shadow(self, positions: list[dict]) -> None:
        """shadow_tracker positions 리스트로 패널 갱신.

        Args:
            positions: shadow_tracker.get_summary()["positions"] 반환값
        """
        self._list.clear()

        if not positions:
            self._summary_lbl.setText("차단 없음")
            return

        for pos in positions:
            ticker = pos.get("ticker", "")
            reason = pos.get("reason", "")
            signal_price = pos.get("signal_price", 0)
            current_price = pos.get("current_price", 0)
            pnl_pct = pos.get("realistic_pnl_pct", 0.0)
            signal_time = pos.get("signal_time", "--:--:--")
            stopped_out = pos.get("stopped_out", False)

            reason_short = _reason_label(reason)
            sign = "+" if pnl_pct >= 0 else ""
            stop_mark = " [손절]" if stopped_out else ""
            pnl_emoji = "📈" if pnl_pct > 0 else "📉"

            text = (
                f"{signal_time}  {ticker}  차단({reason_short})\n"
                f"   차단가: {signal_price:,}  현재가: {current_price:,}  "
                f"{sign}{pnl_pct:.1f}%{stop_mark}  {pnl_emoji}"
            )

            item = QListWidgetItem(text)
            color = Colors.accent_green if pnl_pct > 0 else Colors.text_muted
            item.setForeground(QColor(color))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._list.addItem(item)

        total = len(positions)
        profit_count = sum(1 for p in positions if p.get("realistic_pnl_pct", 0.0) > 0)
        avg_pnl = sum(p.get("realistic_pnl_pct", 0.0) for p in positions) / total
        sign = "+" if avg_pnl >= 0 else ""
        pct_str = f"{profit_count / total * 100:.0f}%" if total > 0 else "0%"
        self._summary_lbl.setText(
            f"합계: 차단 {total}건 | 수익이었을 {profit_count}건 ({pct_str}) | 평균 {sign}{avg_pnl:.1f}%"
        )


def _reason_label(reason: str) -> str:
    """차단 사유 코드를 한국어 짧은 레이블로 변환."""
    r = reason.lower()
    if "intraday" in r:
        if "kosdaq" in r:
            return "KOSDAQ장중"
        if "kospi" in r:
            return "KOSPI장중"
        return "장중필터"
    if "kosdaq" in r:
        return "KOSDAQ약세"
    if "kospi" in r:
        return "KOSPI약세"
    return reason[:8] or "차단"
