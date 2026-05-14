"""gui/views/dashboard/positions_panel.py — 보유 포지션 카드 (dashboard_tab.py에서 분리)."""
from __future__ import annotations

from datetime import datetime as _dt

from PyQt6.QtWidgets import (
    QWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer, QEvent
from PyQt6.QtGui import QColor

from gui.widgets.card import Card

_COLUMN_RATIOS = [14, 9, 6, 11, 9, 8, 12, 8, 11, 12]
_COLUMNS = ["종목", "진입가", "수량", "투입액", "현재가", "수익률", "미실현PnL", "경과", "손절/트레일", "상태"]


class PositionsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._max_positions = 3
        self._build_ui()

    def _build_ui(self) -> None:
        from PyQt6.QtWidgets import QVBoxLayout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._card = Card(title="보유 포지션  0 / 3")

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

        row_h = max(self._table.verticalHeader().defaultSectionSize(), 44)
        self._table.verticalHeader().setDefaultSectionSize(row_h)
        header_h = self._table.horizontalHeader().sizeHint().height()
        visible_rows = max(self._max_positions, 3)
        self._table.setFixedHeight(header_h + row_h * visible_rows + 10)

        self._table.installEventFilter(self)
        QTimer.singleShot(0, self._apply_column_widths)

        self._card.addWidget(self._table)
        self._card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
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

    def set_max_positions(self, n: int) -> None:
        self._max_positions = n

    def update_positions(self, positions: list[dict]) -> None:
        table = self._table
        table.setRowCount(0)
        self._card.setTitle(f"보유 포지션  {len(positions)} / {self._max_positions}")

        if not positions:
            table.setRowCount(1)
            item = QTableWidgetItem("보유 종목 없음 — 신호 대기 중")
            item.setForeground(QColor("#6c7086"))
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(0, 0, item)
            table.setSpan(0, 0, 1, table.columnCount())
            return

        for row_data in positions:
            row = table.rowCount()
            table.insertRow(row)

            pnl_pct = row_data.get("pnl_pct", 0.0)
            pnl_color = QColor("#a6e3a1") if pnl_pct >= 0 else QColor("#f38ba8")

            ticker = row_data.get("ticker", "")
            name = row_data.get("name", "")
            ticker_text = f"{name}\n({ticker})" if name and name != ticker else ticker

            entry_time = row_data.get("entry_time")
            if isinstance(entry_time, str):
                try:
                    entry_time = _dt.fromisoformat(entry_time)
                except ValueError:
                    entry_time = None
            if entry_time:
                elapsed_min = int((_dt.now() - entry_time).total_seconds() / 60)
                elapsed_text = f"{elapsed_min}m"
            else:
                elapsed_text = "—"

            qty_total = int(row_data.get("qty", 0) or 0)
            qty_remaining = int(row_data.get("remaining_qty", qty_total) or qty_total)
            qty_text = (f"{qty_remaining}/{qty_total}"
                        if qty_remaining and qty_remaining != qty_total else str(qty_total))

            entry_price = float(row_data.get("entry_price", 0) or 0)
            current_price = float(row_data.get("current_price", 0) or 0)
            qty_calc = qty_remaining if qty_remaining else qty_total
            cost_amount = entry_price * qty_calc
            unrealized_pnl = (current_price - entry_price) * qty_calc if current_price > 0 else 0.0
            unrealized_color = QColor("#a6e3a1") if unrealized_pnl >= 0 else QColor("#f38ba8")
            unrealized_text = (
                f"{'+' if unrealized_pnl >= 0 else ''}{unrealized_pnl:,.0f}"
                if current_price > 0 else "—"
            )

            stop_loss_val = float(row_data.get("stop_loss", 0) or 0)
            be_active = bool(row_data.get("breakeven_active", False))
            if be_active and entry_price > 0:
                stop_text, stop_color = f"{stop_loss_val:,.0f} BE↑", QColor("#a6e3a1")
            elif stop_loss_val > entry_price * 0.93 and entry_price > 0:
                stop_text, stop_color = f"{stop_loss_val:,.0f} ↑", QColor("#f9e2af")
            else:
                stop_text, stop_color = f"{stop_loss_val:,.0f}", None

            cells = [
                (ticker_text, QColor("#89b4fa")),
                (f"{entry_price:,.0f}", None),
                (qty_text, None),
                (f"{cost_amount:,.0f}", None),
                (f"{current_price:,.0f}", None),
                (f"{'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}%", pnl_color),
                (unrealized_text, unrealized_color),
                (elapsed_text, QColor("#6c7086")),
                (stop_text, stop_color),
                (row_data.get("status", ""), None),
            ]
            for col, (text, color) in enumerate(cells):
                item = QTableWidgetItem(str(text))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if color is not None:
                    item.setForeground(color)
                table.setItem(row, col, item)
