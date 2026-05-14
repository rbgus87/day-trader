"""ProgressRing — 원형 진행률 위젯 (슬롯 사용률 등)."""
from __future__ import annotations

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor, QPen

from gui.design_tokens import Colors


class ProgressRing(QWidget):
    def __init__(self, size: int = 48, parent=None):
        super().__init__(parent)
        self._size = size
        self._value = 0.0
        self._color = Colors.accent_mauve
        self.setFixedSize(size, size)

    def set_value(self, value: float, color: str = "") -> None:
        self._value = max(0.0, min(1.0, value))
        if color:
            self._color = color
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pw = max(4, self._size // 10)
        r = (self._size - pw) / 2

        pen = QPen(QColor(Colors.surface_elevated), pw)
        p.setPen(pen)
        p.drawEllipse(int(pw / 2), int(pw / 2), int(r * 2), int(r * 2))

        if self._value > 0:
            pen.setColor(QColor(self._color))
            p.setPen(pen)
            span = int(-self._value * 360 * 16)
            p.drawArc(int(pw / 2), int(pw / 2), int(r * 2), int(r * 2), 90 * 16, span)

        p.setPen(QColor(Colors.text_primary))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, f"{int(self._value * 100)}%")
