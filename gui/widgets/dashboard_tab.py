"""Dashboard Tab — Active Positions + Today's Trades + PnL Chart."""

import time as _time

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

# pyqtgraph graceful fallback
try:
    import pyqtgraph as pg

    _HAS_PYQTGRAPH = True
except ImportError:
    _HAS_PYQTGRAPH = False


class DashboardTab(QWidget):
    """Dashboard tab showing summary cards, active positions, trades, and PnL chart."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pnl_timestamps: list[float] = []
        self._pnl_values: list[float] = []
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # Summary bar
        root.addLayout(self._build_summary_bar())

        # 2-column: left (positions+trades) | right (chart + placeholder)
        h_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: positions + trades
        left_splitter = QSplitter(Qt.Orientation.Vertical)
        left_splitter.addWidget(self._build_positions_panel())
        left_splitter.addWidget(self._build_trades_panel())
        left_splitter.setStretchFactor(0, 1)
        left_splitter.setStretchFactor(1, 1)
        h_splitter.addWidget(left_splitter)

        # Right: PnL chart + placeholder
        right_panel = QWidget()
        right_vbox = QVBoxLayout(right_panel)
        right_vbox.setContentsMargins(0, 0, 0, 0)
        right_vbox.setSpacing(8)
        right_vbox.addWidget(self._build_pnl_chart())
        # placeholder for future watchlist
        placeholder = QFrame()
        placeholder.setStyleSheet("background-color: #313244; border-radius: 6px;")
        right_vbox.addWidget(placeholder, stretch=1)
        h_splitter.addWidget(right_panel)

        h_splitter.setStretchFactor(0, 3)
        h_splitter.setStretchFactor(1, 1)

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
            "QFrame {"
            "  background-color: #313244;"
            "  border-radius: 6px;"
            "  padding: 10px;"
            "}"
        )
        frame.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

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
        columns = ["종목코드", "종목명", "전략", "진입가", "현재가", "수익률", "손절가", "TP1", "상태"]
        self._positions_table.setColumnCount(len(columns))
        self._positions_table.setHorizontalHeaderLabels(columns)
        self._positions_table.setAlternatingRowColors(True)
        self._positions_table.horizontalHeader().setStretchLastSection(True)
        self._positions_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._positions_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
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
        self._trades_table.horizontalHeader().setStretchLastSection(True)
        self._trades_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._trades_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._trades_table.verticalHeader().setVisible(False)
        vbox.addWidget(self._trades_table)

        return panel

    def _build_pnl_chart(self) -> QWidget:
        """PnL 미니 차트. pyqtgraph 없으면 빈 프레임."""
        if not _HAS_PYQTGRAPH:
            fallback = QFrame()
            fallback.setFixedHeight(120)
            fallback.setStyleSheet("background-color: #313244; border-radius: 6px;")
            lbl = QLabel("PnL 차트 (pyqtgraph 미설치)", fallback)
            lbl.setStyleSheet("color: #6c7086; font-size: 10px; padding: 8px;")
            self._pnl_plot = None
            self._pnl_curve = None
            self._pnl_fill_pos = None
            self._pnl_fill_neg = None
            return fallback

        pg.setConfigOptions(antialias=True)
        plot_widget = pg.PlotWidget()
        plot_widget.setFixedHeight(120)
        plot_widget.setBackground("#313244")
        plot_widget.setTitle("일일 PnL", color="#6c7086", size="9pt")
        plot_widget.showGrid(x=False, y=True, alpha=0.15)
        plot_widget.getAxis("left").setPen(pg.mkPen("#6c7086"))
        plot_widget.getAxis("bottom").setPen(pg.mkPen("#6c7086"))
        plot_widget.getAxis("left").setTextPen(pg.mkPen("#6c7086"))
        plot_widget.getAxis("bottom").setTextPen(pg.mkPen("#6c7086"))
        plot_widget.setMouseEnabled(x=False, y=False)
        plot_widget.hideButtons()

        # 0선
        zero_line = pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen("#585b70", width=1, style=Qt.PenStyle.DashLine))
        plot_widget.addItem(zero_line)

        # 곡선
        self._pnl_curve = plot_widget.plot(pen=pg.mkPen("#cdd6f4", width=1.5))
        # fill은 데이터 추가 시 동적으로 업데이트
        self._pnl_fill_pos = None
        self._pnl_fill_neg = None
        self._pnl_plot = plot_widget

        return plot_widget

    # ------------------------------------------------------------------
    # Public update methods
    # ------------------------------------------------------------------

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
            "Normal": "#a6e3a1",
            "Warning": "#f9e2af",
            "Halted": "#f38ba8",
        }.get(status, "#a6e3a1")
        self._risk_value.setText(status)
        self._risk_value.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {status_color};"
        )
        self._risk_subtitle.setText(f"DD: -{abs(dd):.2f}%")

    def update_pnl_chart(self, timestamp: float, value: float) -> None:
        """PnL 데이터 포인트 추가 및 차트 업데이트."""
        if self._pnl_curve is None:
            return

        self._pnl_timestamps.append(timestamp)
        self._pnl_values.append(value)

        import numpy as np

        xs = np.array(self._pnl_timestamps)
        ys = np.array(self._pnl_values)

        # normalize x to minutes from start
        if len(xs) > 1:
            xs_min = (xs - xs[0]) / 60.0
        else:
            xs_min = np.array([0.0])

        self._pnl_curve.setData(xs_min, ys)

        # fill: green above 0, red below 0
        zeros = np.zeros_like(ys)
        ys_pos = np.maximum(ys, 0)
        ys_neg = np.minimum(ys, 0)

        plot = self._pnl_plot
        if self._pnl_fill_pos is not None:
            plot.removeItem(self._pnl_fill_pos)
        if self._pnl_fill_neg is not None:
            plot.removeItem(self._pnl_fill_neg)

        curve_zero = pg.PlotDataItem(xs_min, zeros)
        curve_pos = pg.PlotDataItem(xs_min, ys_pos)
        curve_neg = pg.PlotDataItem(xs_min, ys_neg)

        self._pnl_fill_pos = pg.FillBetweenItem(curve_zero, curve_pos, brush=pg.mkBrush(166, 227, 161, 40))
        self._pnl_fill_neg = pg.FillBetweenItem(curve_neg, curve_zero, brush=pg.mkBrush(243, 139, 168, 40))

        plot.addItem(self._pnl_fill_pos)
        plot.addItem(self._pnl_fill_neg)

    def update_positions(self, positions: list[dict]) -> None:
        """Rebuild the active positions table."""
        table = self._positions_table
        table.setRowCount(0)

        for row_data in positions:
            row = table.rowCount()
            table.insertRow(row)

            pnl_pct = row_data.get("pnl_pct", 0.0)
            pnl_color = QColor("#a6e3a1") if pnl_pct >= 0 else QColor("#f38ba8")

            name_color = QColor("#89b4fa")
            cells = [
                (row_data.get("ticker", ""), None),
                (row_data.get("name", ""), name_color),
                (row_data.get("strategy", ""), None),
                (f"{row_data.get('entry_price', 0):,.0f}", None),
                (f"{row_data.get('current_price', 0):,.0f}", None),
                (f"{'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}%", pnl_color),
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

        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)

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

        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
