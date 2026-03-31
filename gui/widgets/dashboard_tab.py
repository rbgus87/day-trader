"""Dashboard Tab — Active Positions + Today's Trades + PnL Chart + Summary Bar."""

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

        # PnL chart (전체 폭, 고정 높이)
        root.addWidget(self._build_pnl_chart())

        # 2-column: left (positions+trades) | right (watchlist)
        h_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: positions + trades
        left_splitter = QSplitter(Qt.Orientation.Vertical)
        left_splitter.addWidget(self._build_positions_panel())
        left_splitter.addWidget(self._build_trades_panel())
        left_splitter.setSizes([360, 240])
        h_splitter.addWidget(left_splitter)

        # Right: watchlist
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
        """Create a summary card frame. Returns (frame, value_label, subtitle_label)."""
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
        self._positions_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
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
        self._trades_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
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
            fallback.setFixedHeight(80)
            fallback.setStyleSheet("background-color: #313244; border-radius: 6px;")
            lbl = QLabel("PnL 차트 (pyqtgraph 미설치)", fallback)
            lbl.setStyleSheet("color: #6c7086; font-size: 10px; padding: 8px;")
            self._pnl_plot = None
            self._pnl_curve = None
            self._pnl_fill_pos = None
            self._pnl_fill_neg = None
            self._pnl_empty_label = None
            return fallback

        from PyQt6.QtGui import QFont

        pg.setConfigOptions(antialias=True)

        # 커스텀 시간 축
        class _TimeAxisItem(pg.AxisItem):
            def tickStrings(self, values, scale, spacing):
                from datetime import datetime
                result = []
                for v in values:
                    try:
                        result.append(datetime.fromtimestamp(v).strftime("%H:%M"))
                    except (OSError, ValueError):
                        result.append("")
                return result

        time_axis = _TimeAxisItem(orientation="bottom")
        time_axis.setPen(pg.mkPen("#6c7086"))
        time_axis.setTextPen(pg.mkPen("#6c7086"))
        time_axis.setStyle(maxTickLevel=2)

        plot_widget = pg.PlotWidget(axisItems={"bottom": time_axis})
        plot_widget.setFixedHeight(80)
        plot_widget.setBackground("#313244")
        plot_widget.showGrid(x=False, y=True, alpha=0.15)
        plot_widget.setMouseEnabled(x=False, y=False)
        plot_widget.hideButtons()

        tick_font = QFont()
        tick_font.setPointSize(8)

        left_axis = plot_widget.getAxis("left")
        left_axis.setPen(pg.mkPen("#6c7086"))
        left_axis.setTextPen(pg.mkPen("#6c7086"))
        left_axis.enableAutoSIPrefix(False)
        left_axis.setTickFont(tick_font)
        left_axis.setWidth(40)

        time_axis.setTickFont(tick_font)

        zero_line = pg.InfiniteLine(
            pos=0, angle=0,
            pen=pg.mkPen("#585b70", width=1, style=Qt.PenStyle.DashLine),
        )
        plot_widget.addItem(zero_line)

        # 빈 데이터 텍스트
        self._pnl_empty_label = pg.TextItem("데이터 없음", color="#6c7086", anchor=(0.5, 0.5))
        plot_widget.addItem(self._pnl_empty_label)
        self._pnl_empty_label.setPos(0, 0)

        self._pnl_curve = plot_widget.plot(pen=pg.mkPen("#cdd6f4", width=1.5))
        self._pnl_fill_pos = None
        self._pnl_fill_neg = None
        self._pnl_plot = plot_widget
        return plot_widget

    def _build_watchlist_panel(self) -> QWidget:
        panel = QWidget()
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(4)

        title = QLabel("감시 종목")
        title.setStyleSheet("font-size: 12px; font-weight: bold; color: #cdd6f4;")
        vbox.addWidget(title)

        self._watchlist_table = QTableWidget()
        columns = ["종목코드", "종목명", "ATR%", "서지", "점수"]
        self._watchlist_table.setColumnCount(len(columns))
        self._watchlist_table.setHorizontalHeaderLabels(columns)
        self._watchlist_table.setAlternatingRowColors(True)
        self._watchlist_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._watchlist_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._watchlist_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._watchlist_table.verticalHeader().setVisible(False)
        vbox.addWidget(self._watchlist_table)

        return panel

    # ------------------------------------------------------------------
    # Public update methods
    # ------------------------------------------------------------------

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

            cells = [
                (row_data.get("ticker", ""), None),
                (row_data.get("name", ""), name_color),
                (f"{row_data.get('atr_pct', 0):.1%}", None),
                (f"{row_data.get('volume_surge', 0):.1f}x", None),
                (f"{score:.1f}", score_color),
            ]

            for col, (text, color) in enumerate(cells):
                item = QTableWidgetItem(str(text))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if color is not None:
                    item.setForeground(color)
                table.setItem(row, col, item)

        # Stretch 모드에서 자동 균등 분배

    def update_pnl_chart(self, timestamp: float, value: float) -> None:
        """PnL 데이터 포인트 추가 및 차트 업데이트."""
        if self._pnl_curve is None:
            return

        self._pnl_timestamps.append(timestamp)
        self._pnl_values.append(value)

        # 빈 데이터 라벨 숨기기
        if self._pnl_empty_label is not None:
            self._pnl_plot.removeItem(self._pnl_empty_label)
            self._pnl_empty_label = None

        import numpy as np

        xs = np.array(self._pnl_timestamps)
        ys = np.array(self._pnl_values) / 1000.0  # 천원 단위

        self._pnl_curve.setData(xs, ys)

        zeros = np.zeros_like(ys)
        ys_pos = np.maximum(ys, 0)
        ys_neg = np.minimum(ys, 0)

        plot = self._pnl_plot
        if self._pnl_fill_pos is not None:
            plot.removeItem(self._pnl_fill_pos)
        if self._pnl_fill_neg is not None:
            plot.removeItem(self._pnl_fill_neg)

        curve_zero = pg.PlotDataItem(xs, zeros)
        curve_pos = pg.PlotDataItem(xs, ys_pos)
        curve_neg = pg.PlotDataItem(xs, ys_neg)

        self._pnl_fill_pos = pg.FillBetweenItem(
            curve_zero, curve_pos, brush=pg.mkBrush(166, 227, 161, 40),
        )
        self._pnl_fill_neg = pg.FillBetweenItem(
            curve_neg, curve_zero, brush=pg.mkBrush(243, 139, 168, 40),
        )
        plot.addItem(self._pnl_fill_pos)
        plot.addItem(self._pnl_fill_neg)

    def update_summary(self, data: dict) -> None:
        """Update summary bar.

        Expected keys:
            daily_pnl (float), daily_pnl_pct (float),
            trades_count (int), max_trades (int),
            wins (int), losses (int),
            win_rate (float), avg_win_rate (float),
            risk_status (str), dd_pct (float)
        """
        # Daily P&L
        pnl = data.get("daily_pnl", 0.0)
        pnl_pct = data.get("daily_pnl_pct", 0.0)
        pnl_color = "#a6e3a1" if pnl >= 0 else "#f38ba8"
        sign = "+" if pnl >= 0 else ""
        self._pnl_value.setText(f"{sign}{pnl:,.0f}")
        self._pnl_value.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {pnl_color};"
        )
        self._pnl_subtitle.setText(f"{sign}{pnl_pct:.2f}%")

        # Trades Today
        count = data.get("trades_count", 0)
        max_t = data.get("max_trades", 0)
        wins = data.get("wins", 0)
        losses = data.get("losses", 0)
        self._trades_value.setText(f"{count} / {max_t}")
        self._trades_subtitle.setText(f"Win {wins} / Loss {losses}")

        # Win Rate
        win_rate = data.get("win_rate", 0.0)
        avg = data.get("avg_win_rate", 0.0)
        self._winrate_value.setText(f"{win_rate:.1f}%")
        self._winrate_value.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #f9e2af;"
        )
        self._winrate_subtitle.setText(f"Avg: {avg:.1f}%")

        # Risk Status
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

    def update_positions(self, positions: list[dict]) -> None:
        """Rebuild the active positions table from a list of position dicts.

        Expected keys per dict:
            ticker, name, strategy, entry_price, current_price,
            pnl_pct, stop_loss, tp1_price, status
        """
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

        # Stretch 모드에서 자동 균등 분배

    def update_trades(self, trades: list[dict]) -> None:
        """Rebuild today's trades table from a list of trade dicts.

        Expected keys per dict:
            time, ticker, side, price, qty, pnl, reason
        """
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

        # Stretch 모드에서 자동 균등 분배
