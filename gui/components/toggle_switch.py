"""ToggleSwitch — 커스텀 on/off 슬라이더 토글."""
from __future__ import annotations

from PyQt6.QtWidgets import QAbstractButton
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QBrush

from gui.design_tokens import Colors


class ToggleSwitch(QAbstractButton):
    toggled_state = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(44, 22)
        self.clicked.connect(lambda: self.toggled_state.emit(self.isChecked()))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track_color = QColor(Colors.accent_mauve if self.isChecked() else Colors.surface_border)
        p.setBrush(QBrush(track_color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 4, 44, 14, 7, 7)
        knob_x = 24 if self.isChecked() else 2
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(knob_x, 1, 20, 20)
