"""MetricCard — KPI 카드 (타이틀 / 값 / 서브타이틀)."""
from __future__ import annotations

from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel

from gui.design_tokens import Colors, Border, Spacing


class MetricCard(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            f"MetricCard {{ background: {Colors.surface}; border-radius: {Border.radius_md}px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            Spacing.padding_card, Spacing.padding_card,
            Spacing.padding_card, Spacing.padding_card,
        )
        layout.setSpacing(Spacing.gap_xs)

        self._title_lbl = QLabel(title)
        self._title_lbl.setStyleSheet(
            f"color: {Colors.text_muted}; font-size: 10px; background: transparent;"
        )
        self._value_lbl = QLabel("—")
        self._value_lbl.setStyleSheet(
            f"color: {Colors.text_primary}; font-size: 18px; font-weight: bold; background: transparent;"
        )
        self._sub_lbl = QLabel("")
        self._sub_lbl.setStyleSheet(
            f"color: {Colors.text_muted}; font-size: 10px; background: transparent;"
        )
        layout.addWidget(self._title_lbl)
        layout.addWidget(self._value_lbl)
        layout.addWidget(self._sub_lbl)

    def set_value(self, text: str, color: str = "") -> None:
        self._value_lbl.setText(text)
        c = color or Colors.text_primary
        self._value_lbl.setStyleSheet(
            f"color: {c}; font-size: 18px; font-weight: bold; background: transparent;"
        )

    def set_subtitle(self, text: str, color: str = "") -> None:
        self._sub_lbl.setText(text)
        c = color or Colors.text_muted
        self._sub_lbl.setStyleSheet(f"color: {c}; font-size: 10px; background: transparent;")
