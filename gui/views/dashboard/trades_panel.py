"""gui/views/dashboard/trades_panel.py — 당일 체결 테이블 (dashboard_tab.py에서 분리)."""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QLabel, QHBoxLayout,
)
from PyQt6.QtCore import Qt, QTimer, QEvent
from PyQt6.QtGui import QColor

from gui.widgets.card import Card

_COLUMN_RATIOS = [9, 18, 5, 11, 4, 12, 8, 6]
_COLUMNS = ["시간", "종목", "매매", "가격", "수량", "투입액", "손익", "사유"]

_REASON_CODES = {
    "force_close": "FC", "forced_close": "FC",
    "stop_loss": "SL",
    "trailing_stop": "TRL", "trailing": "TRL",
    "breakeven_stop": "BE",
    "limit_up_exit": "상한",
    "momentum_fade": "FADE",
    "momentum": "MOM",
    "paper": "?", "unknown": "?", "": "?",
}


class TradesPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._card = Card(title="당일 체결")
        self._table = QTableWidget()
        self._table.setColumnCount(len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.setAlternatingRowColors(True)

        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setWordWrap(True)
        self._table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._table.verticalHeader().setDefaultSectionSize(44)
        self._table.setMinimumWidth(500)

        self._table.installEventFilter(self)
        QTimer.singleShot(0, self._apply_column_widths)

        self._card.addWidget(self._table, stretch=1)
        layout.addWidget(self._card)

    def eventFilter(self, obj, event):
        if obj is self._table and event.type() == QEvent.Type.Resize:
            new_w = event.size().width() - self._table.frameWidth() * 2
            sb = self._table.verticalScrollBar()
            if sb.isVisible():
                new_w -= sb.width()
            self._apply_column_widths(new_w)
        return super().eventFilter(obj, event)

    def _apply_column_widths(self, total: int | None = None) -> None:
        if total is None:
            total = self._table.viewport().width()
        if total <= 0:
            return
        s = sum(_COLUMN_RATIOS)
        used = 0
        for i, r in enumerate(_COLUMN_RATIOS[:-1]):
            w = max(1, int(total * r / s))
            self._table.setColumnWidth(i, w)
            used += w
        self._table.setColumnWidth(len(_COLUMN_RATIOS) - 1, max(1, total - used))

    @staticmethod
    def _make_side_chip(side: str) -> QWidget:
        s = (side or "").lower()
        if s == "buy":
            text, bg = "매수", "#dc2626"
        elif s == "sell":
            text, bg = "매도", "#2563eb"
        else:
            text, bg = side or "?", "#6c7086"
        chip = QLabel(text)
        chip.setStyleSheet(
            f"QLabel {{ background-color: {bg}; color: white; "
            f"border-radius: 4px; padding: 2px 8px; font-weight: bold; font-size: 11px; }}"
        )
        chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chip.setToolTip(side or "")
        container = QWidget()
        h = QHBoxLayout(container)
        h.setContentsMargins(0, 0, 0, 0)
        h.addStretch()
        h.addWidget(chip)
        h.addStretch()
        return container

    def update_trades(self, trades: list[dict]) -> None:
        table = self._table
        table.setRowCount(0)

        if not trades:
            table.setRowCount(1)
            item = QTableWidgetItem("오늘 체결 없음")
            item.setForeground(QColor("#6c7086"))
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(0, 0, item)
            table.setSpan(0, 0, 1, table.columnCount())
            return

        align_right = {3, 4, 5, 6}
        for row_data in trades:
            row = table.rowCount()
            table.insertRow(row)

            raw_time = str(row_data.get("time", "") or row_data.get("traded_at", "") or "")
            if "T" in raw_time:
                time_text = raw_time.split("T")[1][:8]
            elif len(raw_time) > 8:
                time_text = raw_time[-8:]
            else:
                time_text = raw_time

            side = str(row_data.get("side", "")).lower()

            pnl = row_data.get("pnl")
            if side == "buy" or pnl is None:
                pnl_text, pnl_color = "—", QColor("#6c7086")
            else:
                pnl = int(pnl)
                pnl_text = f"{pnl:+,}"
                pnl_color = QColor("#a6e3a1") if pnl >= 0 else QColor("#f38ba8")

            if side == "sell":
                raw_reason = str(row_data.get("exit_reason", "") or row_data.get("reason", "") or "")
            else:
                raw_reason = str(row_data.get("strategy", "") or "")
            reason_code = _REASON_CODES.get(raw_reason.lower(), raw_reason[:4].upper() or "?")

            ticker = str(row_data.get("ticker", ""))
            name = str(row_data.get("name", ""))
            ticker_text = f"{name}\n({ticker})" if name else ticker

            price_int = int(row_data.get("price", 0) or 0)
            qty_int = int(row_data.get("qty", 0) or 0)
            cost_amt = price_int * qty_int

            cells = [
                (time_text, None, None),
                (ticker_text, QColor("#89b4fa"), None),
                ("", None, None),
                (f"{price_int:,}", None, None),
                (str(qty_int), None, None),
                (f"{cost_amt:,}", None, None),
                (pnl_text, pnl_color, None),
                (reason_code, None, raw_reason or "—"),
            ]
            for col, (text, color, tooltip) in enumerate(cells):
                cell_item = QTableWidgetItem(str(text))
                if col in align_right:
                    cell_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                else:
                    cell_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if color is not None:
                    cell_item.setForeground(color)
                if tooltip:
                    cell_item.setToolTip(tooltip)
                table.setItem(row, col, cell_item)

            table.setCellWidget(row, 2, self._make_side_chip(side))
