"""Dashboard Tab — Active Positions + Today's Trades + PnL + Summary Bar."""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QSplitter,
    QHeaderView,
    QAbstractItemView,
    QSizePolicy,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor


class DashboardTab(QWidget):
    """Dashboard tab showing summary cards, active positions, trades, and PnL."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pnl_timestamps: list[float] = []
        self._pnl_values: list[float] = []
        self._pnl_high: float = 0.0
        self._pnl_low: float = 0.0
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        root.addLayout(self._build_summary_bar())
        root.addWidget(self._build_daily_history())
        root.addWidget(self._build_pnl_chart())

        # 2-column: left (positions+trades) | right (watchlist)
        h_splitter = QSplitter(Qt.Orientation.Horizontal)

        left_splitter = QSplitter(Qt.Orientation.Vertical)
        left_splitter.addWidget(self._build_positions_panel())
        left_splitter.addWidget(self._build_trades_panel())
        left_splitter.setSizes([360, 240])
        h_splitter.addWidget(left_splitter)

        watchlist = self._build_watchlist_panel()
        watchlist.setMinimumWidth(250)
        h_splitter.addWidget(watchlist)

        h_splitter.setSizes([700, 300])
        root.addWidget(h_splitter, stretch=1)

    def _build_summary_bar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(8)

        frame, value, subtitle = self._make_summary_card("일일 손익")
        self._pnl_value = value
        self._pnl_subtitle = subtitle
        layout.addWidget(frame)

        frame, value, subtitle = self._make_summary_card("당일 거래")
        self._trades_value = value
        self._trades_subtitle = subtitle
        layout.addWidget(frame)

        frame, value, subtitle = self._make_summary_card("승률")
        self._winrate_value = value
        self._winrate_subtitle = subtitle
        layout.addWidget(frame)

        frame, value, subtitle = self._make_summary_card("리스크")
        self._risk_value = value
        self._risk_subtitle = subtitle
        layout.addWidget(frame)

        return layout

    def _make_summary_card(self, title: str) -> tuple["QFrame", "QLabel", "QLabel"]:
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame { background-color: #313244; border-radius: 6px; padding: 10px; }"
        )
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        vbox = QVBoxLayout(frame)
        vbox.setContentsMargins(10, 10, 10, 10)
        vbox.setSpacing(4)

        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 10px; color: #6c7086;")
        value_label = QLabel("—")
        value_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #cdd6f4;")
        subtitle_label = QLabel("")
        subtitle_label.setStyleSheet("font-size: 10px; color: #6c7086;")

        vbox.addWidget(title_label)
        vbox.addWidget(value_label)
        vbox.addWidget(subtitle_label)

        return frame, value_label, subtitle_label

    def _build_positions_panel(self) -> QWidget:
        panel = QWidget()
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(4)

        title = QLabel("보유 포지션")
        title.setStyleSheet("font-size: 12px; font-weight: bold; color: #cdd6f4;")
        vbox.addWidget(title)

        self._positions_table = QTableWidget()
        columns = ["종목코드", "종목명", "전략", "진입가", "현재가", "수익률", "경과", "손절가", "TP1", "상태"]
        self._positions_table.setColumnCount(len(columns))
        self._positions_table.setHorizontalHeaderLabels(columns)
        self._positions_table.setAlternatingRowColors(True)
        self._positions_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._positions_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._positions_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._positions_table.verticalHeader().setVisible(False)
        vbox.addWidget(self._positions_table)
        return panel

    def _build_trades_panel(self) -> QWidget:
        panel = QWidget()
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(4)

        title = QLabel("당일 체결")
        title.setStyleSheet("font-size: 12px; font-weight: bold; color: #cdd6f4;")
        vbox.addWidget(title)

        self._trades_table = QTableWidget()
        columns = ["시간", "종목코드", "매매", "가격", "수량", "손익", "사유"]
        self._trades_table.setColumnCount(len(columns))
        self._trades_table.setHorizontalHeaderLabels(columns)
        self._trades_table.setAlternatingRowColors(True)
        self._trades_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._trades_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._trades_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._trades_table.verticalHeader().setVisible(False)
        vbox.addWidget(self._trades_table)
        return panel

    def _build_daily_history(self) -> QWidget:
        """최근 5일 일일 PnL — 순수 Qt 텍스트."""
        frame = QFrame()
        frame.setFixedHeight(40)
        frame.setStyleSheet("background-color: #313244; border-radius: 6px;")

        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 4, 12, 4)

        title = QLabel("최근 성과:")
        title.setStyleSheet("color: #6c7086; font-size: 10px;")
        layout.addWidget(title)

        self._daily_history_labels: list[QLabel] = []
        for _ in range(5):
            lbl = QLabel("—")
            lbl.setStyleSheet("color: #6c7086; font-size: 11px; font-weight: bold;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFixedWidth(80)
            layout.addWidget(lbl)
            self._daily_history_labels.append(lbl)

        layout.addStretch()
        self._daily_bar_plot = None
        self._daily_bar_item = None
        return frame

    def _build_pnl_chart(self) -> QWidget:
        """일중 PnL — 순수 Qt 텍스트 기반."""
        frame = QFrame()
        frame.setFixedHeight(60)
        frame.setStyleSheet("background-color: #313244; border-radius: 6px;")

        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)

        self._pnl_chart_label = QLabel("일중 PnL: —")
        self._pnl_chart_label.setStyleSheet(
            "color: #cdd6f4; font-size: 14px; font-weight: bold;"
        )
        layout.addWidget(self._pnl_chart_label)

        layout.addStretch()

        self._pnl_range_label = QLabel("고: — / 저: —")
        self._pnl_range_label.setStyleSheet("color: #6c7086; font-size: 11px;")
        layout.addWidget(self._pnl_range_label)

        self._pnl_plot = None
        self._pnl_curve = None
        self._pnl_fill_pos = None
        self._pnl_fill_neg = None
        self._pnl_empty_label = None
        return frame

    def _build_watchlist_panel(self) -> QWidget:
        panel = QWidget()
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(4)

        title = QLabel("감시 종목")
        title.setStyleSheet("font-size: 12px; font-weight: bold; color: #cdd6f4;")
        vbox.addWidget(title)

        self._watchlist_table = QTableWidget()
        columns = ["종목코드", "종목명", "현재가", "등락%", "ATR%", "점수"]
        self._watchlist_table.setColumnCount(len(columns))
        self._watchlist_table.setHorizontalHeaderLabels(columns)
        self._watchlist_table.setAlternatingRowColors(True)
        self._watchlist_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._watchlist_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._watchlist_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._watchlist_table.verticalHeader().setVisible(False)
        vbox.addWidget(self._watchlist_table)
        return panel

    # ------------------------------------------------------------------
    # Public update methods
    # ------------------------------------------------------------------

    def update_daily_history(self, daily_data: list[dict]) -> None:
        """최근 N일 PnL 텍스트 업데이트."""
        if not hasattr(self, "_daily_history_labels"):
            return
        for i, lbl in enumerate(self._daily_history_labels):
            if i < len(daily_data):
                d = daily_data[i]
                pnl = d.get("pnl", 0)
                date = d.get("date", "")
                sign = "+" if pnl >= 0 else ""
                color = "#a6e3a1" if pnl >= 0 else "#f38ba8"
                lbl.setText(f"{date}\n{sign}{pnl/1000:.0f}K")
                lbl.setStyleSheet(f"color: {color}; font-size: 10px; font-weight: bold;")
            else:
                lbl.setText("—")
                lbl.setStyleSheet("color: #6c7086; font-size: 10px;")

    def update_pnl_chart(self, timestamp: float, value: float) -> None:
        """PnL 텍스트 업데이트."""
        if not hasattr(self, "_pnl_chart_label") or self._pnl_chart_label is None:
            return

        self._pnl_timestamps.append(timestamp)
        self._pnl_values.append(value)

        self._pnl_high = max(self._pnl_high, value)
        self._pnl_low = min(self._pnl_low, value)

        color = "#a6e3a1" if value >= 0 else "#f38ba8"
        sign = "+" if value >= 0 else ""

        self._pnl_chart_label.setText(f"일중 PnL: {sign}{value:,.0f}원")
        self._pnl_chart_label.setStyleSheet(
            f"color: {color}; font-size: 14px; font-weight: bold;"
        )
        self._pnl_range_label.setText(
            f"고: +{self._pnl_high:,.0f} / 저: {self._pnl_low:,.0f}"
        )

    def update_watchlist(self, candidates: list[dict]) -> None:
        """감시 종목 테이블 업데이트 (최대 5종목)."""
        table = self._watchlist_table
        table.setRowCount(0)

        for row_data in candidates[:5]:
            row = table.rowCount()
            table.insertRow(row)

            name_color = QColor("#89b4fa")
            score = row_data.get("score", 0)
            score_color = (
                QColor("#a6e3a1") if score >= 7
                else QColor("#f9e2af") if score >= 5
                else QColor("#6c7086")
            )

            change_pct = row_data.get("change_pct", 0)
            change_color = QColor("#a6e3a1") if change_pct >= 0 else QColor("#f38ba8")
            cells = [
                (row_data.get("ticker", ""), None),
                (row_data.get("name", ""), name_color),
                (f"{row_data.get('current_price', 0):,.0f}", None),
                (f"{change_pct:+.2f}%", change_color),
                (f"{row_data.get('atr_pct', 0):.1%}", None),
                (f"{score:.1f}", score_color),
            ]

            for col, (text, color) in enumerate(cells):
                item = QTableWidgetItem(str(text))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if color is not None:
                    item.setForeground(color)
                table.setItem(row, col, item)

    def update_summary(self, data: dict) -> None:
        """Update summary bar."""
        pnl = data.get("daily_pnl", 0.0)
        pnl_pct = data.get("daily_pnl_pct", 0.0)
        pnl_color = "#a6e3a1" if pnl >= 0 else "#f38ba8"
        sign = "+" if pnl >= 0 else ""
        self._pnl_value.setText(f"{sign}{pnl:,.0f}")
        self._pnl_value.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {pnl_color};"
        )
        self._pnl_subtitle.setText(f"{sign}{pnl_pct:.2f}%")

        count = data.get("trades_count", 0)
        max_t = data.get("max_trades", 0)
        wins = data.get("wins", 0)
        losses = data.get("losses", 0)
        self._trades_value.setText(f"{count} / {max_t}")
        self._trades_subtitle.setText(f"Win {wins} / Loss {losses}")

        win_rate = data.get("win_rate", 0.0)
        avg = data.get("avg_win_rate", 0.0)
        self._winrate_value.setText(f"{win_rate:.1f}%")
        self._winrate_value.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #f9e2af;"
        )
        self._winrate_subtitle.setText(f"Avg: {avg:.1f}%")

        status = data.get("risk_status", "Normal")
        dd = data.get("dd_pct", 0.0)
        status_color = {
            "Normal": "#a6e3a1", "Warning": "#f9e2af", "Halted": "#f38ba8",
        }.get(status, "#a6e3a1")
        self._risk_value.setText(status)
        self._risk_value.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {status_color};"
        )
        self._risk_subtitle.setText(f"DD: -{abs(dd):.2f}%")

    def update_positions(self, positions: list[dict]) -> None:
        """Rebuild the active positions table."""
        table = self._positions_table
        table.setRowCount(0)

        for row_data in positions:
            row = table.rowCount()
            table.insertRow(row)

            pnl_pct = row_data.get("pnl_pct", 0.0)
            pnl_color = QColor("#a6e3a1") if pnl_pct >= 0 else QColor("#f38ba8")

            entry_time = row_data.get("entry_time")
            if entry_time:
                from datetime import datetime as _dt
                if isinstance(entry_time, str):
                    entry_time = _dt.fromisoformat(entry_time)
                elapsed_min = int((_dt.now() - entry_time).total_seconds() / 60)
                time_limit = row_data.get("time_stop_minutes", 60)
                remaining = max(0, time_limit - elapsed_min)
                elapsed_text = f"{elapsed_min}분/{time_limit}분"
                elapsed_color = QColor("#f9e2af") if remaining <= 10 else None
            else:
                elapsed_text = "—"
                elapsed_color = None

            name_color = QColor("#89b4fa")
            cells = [
                (row_data.get("ticker", ""), None),
                (row_data.get("name", ""), name_color),
                (row_data.get("strategy", ""), None),
                (f"{row_data.get('entry_price', 0):,.0f}", None),
                (f"{row_data.get('current_price', 0):,.0f}", None),
                (f"{'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}%", pnl_color),
                (elapsed_text, elapsed_color),
                (f"{row_data.get('stop_loss', 0):,.0f}", None),
                (f"{row_data.get('tp1_price', 0):,.0f}", None),
                (row_data.get("status", ""), None),
            ]

            for col, (text, color) in enumerate(cells):
                item = QTableWidgetItem(str(text))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if color is not None:
                    item.setForeground(color)
                table.setItem(row, col, item)

    def update_trades(self, trades: list[dict]) -> None:
        """Rebuild today's trades table."""
        table = self._trades_table
        table.setRowCount(0)

        for row_data in trades:
            row = table.rowCount()
            table.insertRow(row)

            side = row_data.get("side", "")
            side_color = QColor("#89b4fa") if side.upper() == "BUY" else QColor("#f38ba8")

            pnl = row_data.get("pnl", 0.0)
            pnl_color = QColor("#a6e3a1") if pnl >= 0 else QColor("#f38ba8")
            pnl_sign = "+" if pnl >= 0 else ""

            cells = [
                (row_data.get("time", ""), None),
                (row_data.get("ticker", ""), None),
                (side, side_color),
                (f"{row_data.get('price', 0):,.0f}", None),
                (str(row_data.get("qty", 0)), None),
                (f"{pnl_sign}{pnl:,.0f}", pnl_color),
                (row_data.get("reason", ""), None),
            ]

            for col, (text, color) in enumerate(cells):
                item = QTableWidgetItem(str(text))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if color is not None:
                    item.setForeground(color)
                table.setItem(row, col, item)
