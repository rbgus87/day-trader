"""StatusBadge — 상태 표시 뱃지 (색상 + 텍스트)."""
from __future__ import annotations

from PyQt6.QtWidgets import QLabel
from PyQt6.QtCore import Qt

from gui.design_tokens import Colors

_PRESETS: dict[str, tuple[str, str]] = {
    "매수가능": (Colors.accent_green, "#1e3a2f"),
    "정상": (Colors.accent_green, "#1e3a2f"),
    "차단": (Colors.accent_red, "#3a1e2f"),
    "VI": (Colors.accent_yellow, "#3a341e"),
    "경고": (Colors.accent_yellow, "#3a341e"),
    "정보": (Colors.accent_blue, "#1e2a3a"),
    "PAPER": (Colors.accent_blue, "#1e2a3a"),
    "LIVE": (Colors.accent_red, "#3a1e2f"),
    "실행중": (Colors.accent_green, "#1e3a2f"),
    "정지됨": (Colors.text_muted, Colors.surface),
}


class StatusBadge(QLabel):
    def __init__(self, text: str = "", preset: str = "", parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fg, bg = _PRESETS.get(preset, (Colors.text_secondary, Colors.surface))
        self._apply(fg, bg)

    def set_status(self, text: str, preset: str = "", fg: str = "", bg: str = "") -> None:
        self.setText(text)
        if preset in _PRESETS:
            _fg, _bg = _PRESETS[preset]
        else:
            _fg = fg or Colors.text_secondary
            _bg = bg or Colors.surface
        self._apply(_fg, _bg)

    def _apply(self, fg: str, bg: str) -> None:
        self.setStyleSheet(
            f"QLabel {{"
            f" color: {fg}; background: {bg};"
            f" border: 1px solid {fg}55;"
            f" border-radius: 4px;"
            f" padding: 1px 8px;"
            f" font-size: 11px; font-weight: bold;"
            f"}}"
        )
