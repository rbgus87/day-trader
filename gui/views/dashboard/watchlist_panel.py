"""gui/views/dashboard/watchlist_panel.py — 감시 종목 테이블 (dashboard_tab.py에서 분리)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from gui.widgets.card import Card

_COLUMNS = ["종목", "시장", "현재가", "등락%", "전일고가", "돌파%", "ATR%"]


class WatchlistPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._market_map = self._load_market_map()
        self._atr_cache = self._load_atr_cache()
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._card = Card(title="감시 종목  0종목")
        self._table = QTableWidget()
        self._table.setColumnCount(len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(
            "QTableWidget {"
            "  background-color: transparent;"
            "  alternate-background-color: #2f2f45;"
            "  gridline-color: #313244;"
            "  border: none;"
            "}"
            "QTableWidget::item { padding: 3px 6px; border: none; }"
            "QTableWidget::item:hover { background-color: #2f2f45; }"
            "QTableWidget::item:selected { background-color: #45475a; }"
            "QHeaderView::section {"
            "  background-color: #2a2a3d;"
            "  color: #a6adc8;"
            "  font-size: 11px;"
            "  font-weight: bold;"
            "  padding: 4px 6px;"
            "  border: none;"
            "  border-bottom: 1px solid #313244;"
            "  border-right: 1px solid #313244;"
            "}"
        )

        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, len(_COLUMNS)):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setStretchLastSection(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)

        self._card.addWidget(self._table, stretch=1)
        layout.addWidget(self._card)

    @staticmethod
    def _load_market_map() -> dict[str, str]:
        try:
            path = Path("config/universe.yaml")
            if not path.exists():
                return {}
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return {s["ticker"]: s.get("market", "") for s in data.get("stocks", []) if s.get("ticker")}
        except Exception:
            return {}

    @staticmethod
    def _load_atr_cache(db_path: str = "daytrader.db") -> dict[str, float]:
        try:
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT ticker, atr_pct FROM ticker_atr t "
                "WHERE dt=(SELECT MAX(dt) FROM ticker_atr WHERE ticker=t.ticker)"
            ).fetchall()
            conn.close()
            return {t: p for t, p in rows if p is not None}
        except Exception:
            return {}

    def update_watchlist(self, items: list[dict]) -> None:
        table = self._table
        vsb = table.verticalScrollBar()
        saved_scroll = vsb.value()

        table.setRowCount(0)
        self._card.setTitle(f"감시 종목  {len(items)}종목")

        for item in items:
            row = table.rowCount()
            table.insertRow(row)

            ticker = item.get("ticker", "")
            name = item.get("name", ticker)
            current = item.get("current_price", 0)
            change_pct = item.get("change_pct", 0)
            prev_high = item.get("prev_high", 0)
            breakout_pct = item.get("breakout_pct", -999)
            has_pos = item.get("has_position", False)

            change_color = QColor("#a6e3a1") if change_pct >= 0 else QColor("#f38ba8")

            if breakout_pct >= 3:
                bp_color, bp_text = QColor("#a6e3a1"), f"+{breakout_pct:.2f}% ✓"
            elif breakout_pct >= 0:
                bp_color, bp_text = QColor("#6c7086"), f"+{breakout_pct:.2f}%"
            elif breakout_pct >= -1:
                bp_color, bp_text = QColor("#f9e2af"), f"{breakout_pct:.2f}%"
            else:
                bp_color = QColor("#6c7086")
                bp_text = f"{breakout_pct:.2f}%" if breakout_pct > -100 else "—"

            ticker_text = f"⭐ {name}" if has_pos else name
            ticker_color = QColor("#f9e2af") if has_pos else QColor("#89b4fa")

            market = item.get("market") or self._market_map.get(ticker, "")
            market_text = "K" if market == "kospi" else ("Q" if market == "kosdaq" else "-")
            market_color = (QColor("#89b4fa") if market == "kospi"
                            else QColor("#f9e2af") if market == "kosdaq"
                            else QColor("#6c7086"))

            atr_pct = item.get("atr_pct") or self._atr_cache.get(ticker)
            if atr_pct is not None:
                atr_text = f"{atr_pct:.1f}%"
                atr_color = (QColor("#f38ba8") if atr_pct >= 10
                             else QColor("#f9e2af") if atr_pct >= 5
                             else QColor("#a6e3a1"))
            else:
                atr_text, atr_color = "—", QColor("#6c7086")

            cells = [
                (ticker_text, ticker_color),
                (market_text, market_color),
                (f"{int(current):,}" if current > 0 else "—", None),
                (f"{'+' if change_pct >= 0 else ''}{change_pct:.2f}%", change_color),
                (f"{int(prev_high):,}" if prev_high > 0 else "—", QColor("#6c7086")),
                (bp_text, bp_color),
                (atr_text, atr_color),
            ]
            for col, (text, color) in enumerate(cells):
                cell = QTableWidgetItem(str(text))
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if color:
                    cell.setForeground(color)
                table.setItem(row, col, cell)

        vsb.setValue(min(saved_scroll, vsb.maximum()))
