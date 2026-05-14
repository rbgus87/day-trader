"""AlertBanner — 상단 알림 배너 (VI 발동, 시장 약세 등)."""
from __future__ import annotations

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import pyqtSignal

from gui.design_tokens import Colors

_LEVELS: dict[str, tuple[str, str, str]] = {
    "info": (Colors.accent_blue, "#1e2a3a", "ℹ"),
    "warning": (Colors.accent_yellow, "#3a341e", "⚠"),
    "error": (Colors.accent_red, "#3a1e2f", "✖"),
}


class AlertBanner(QFrame):
    dismissed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVisible(False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(8)

        self._icon = QLabel()
        self._msg = QLabel()
        self._msg.setWordWrap(True)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setFlat(True)
        close_btn.setStyleSheet(
            f"QPushButton {{ color: {Colors.text_muted}; background: transparent; font-size: 12px; }}"
        )
        close_btn.clicked.connect(self._dismiss)

        layout.addWidget(self._icon)
        layout.addWidget(self._msg, 1)
        layout.addWidget(close_btn)

    def show_alert(self, message: str, level: str = "info") -> None:
        fg, bg, icon = _LEVELS.get(level, _LEVELS["info"])
        self._icon.setText(icon)
        self._icon.setStyleSheet(f"color: {fg}; font-size: 14px; background: transparent;")
        self._msg.setText(message)
        self._msg.setStyleSheet(f"color: {fg}; font-size: 12px; background: transparent;")
        self.setStyleSheet(
            f"QFrame {{ background: {bg}; border-bottom: 1px solid {fg}33; }}"
        )
        self.setVisible(True)

    def _dismiss(self) -> None:
        self.setVisible(False)
        self.dismissed.emit()
