"""Backtest Tab — Strategy backtesting with parameter selection and results display."""

import numpy as np
import pyqtgraph as pg

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QFormLayout,
    QComboBox,
    QDateEdit,
    QPushButton,
    QLabel,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt, QDate, pyqtSignal
from PyQt6.QtGui import QColor, QFont


class BacktestTab(QWidget):
    """Backtest tab for running strategy backtests and viewing KPI results."""

    run_backtest_clicked = pyqtSignal(dict)
    run_compare_clicked = pyqtSignal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        root.addWidget(self._build_parameter_panel())
        root.addLayout(self._build_action_bar())

        self._results_panel = self._build_results_panel()
        self._results_panel.setVisible(False)
        root.addWidget(self._results_panel, stretch=1)

        self._charts_panel = self._build_charts_panel()
        root.addWidget(self._charts_panel)

    def _build_parameter_panel(self) -> QGroupBox:
        group = QGroupBox("백테스트 설정")
        form = QFormLayout(group)
        form.setContentsMargins(10, 16, 10, 10)
        form.setSpacing(8)

        # 전략
        self.combo_strategy = QComboBox()
        self.combo_strategy.addItems(["Momentum", "Pullback", "Flow", "Gap", "OpenBreak", "BigCandle"])
        form.addRow("전략:", self.combo_strategy)

        # 시작일
        self.date_start = QDateEdit()
        self.date_start.setCalendarPopup(True)
        self.date_start.setDate(QDate.currentDate().addDays(-30))
        form.addRow("시작일:", self.date_start)

        # 종료일
        self.date_end = QDateEdit()
        self.date_end.setCalendarPopup(True)
        self.date_end.setDate(QDate.currentDate())
        form.addRow("종료일:", self.date_end)

        # 종목
        self.combo_ticker = QComboBox()
        form.addRow("종목:", self.combo_ticker)

        return group

    def _build_action_bar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(8)

        btn_run = QPushButton("백테스트 실행")
        btn_run.setObjectName("startBtn")
        btn_run.clicked.connect(self._on_run_clicked)
        layout.addWidget(btn_run)

        btn_compare = QPushButton("전략 비교")
        btn_compare.setObjectName("manualBtn")
        btn_compare.setToolTip("선택 종목에 대해 6전략 비교 백테스트")
        btn_compare.clicked.connect(self._on_compare_clicked)
        layout.addWidget(btn_compare)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar, stretch=1)

        return layout

    def _build_charts_panel(self) -> QWidget:
        panel = QWidget()
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 청산 사유 바 차트
        self._chart_exit = pg.PlotWidget(title="청산 사유 분포")
        self._chart_exit.setBackground("#2a2a3d")
        self._chart_exit.setMaximumHeight(180)
        self._chart_exit.getAxis("bottom").setStyle(tickFont=QFont("Malgun Gothic", 8))
        layout.addWidget(self._chart_exit)

        # PnL 분포 히스토그램
        self._chart_pnl = pg.PlotWidget(title="PnL 분포")
        self._chart_pnl.setBackground("#2a2a3d")
        self._chart_pnl.setMaximumHeight(180)
        layout.addWidget(self._chart_pnl)

        panel.setVisible(False)
        return panel

    def _build_results_panel(self) -> QGroupBox:
        group = QGroupBox("결과")
        vbox = QVBoxLayout(group)
        vbox.setContentsMargins(10, 16, 10, 10)
        vbox.setSpacing(8)

        # KPI summary — two columns
        kpi_layout = QHBoxLayout()
        kpi_layout.setSpacing(16)

        # Left column
        left_form = QFormLayout()
        left_form.setSpacing(6)

        self._lbl_total_trades = QLabel("—")
        self._lbl_win_rate = QLabel("—")
        self._lbl_profit_factor = QLabel("—")
        left_form.addRow("Total Trades:", self._lbl_total_trades)
        left_form.addRow("Win Rate:", self._lbl_win_rate)
        left_form.addRow("Profit Factor:", self._lbl_profit_factor)

        # Right column
        right_form = QFormLayout()
        right_form.setSpacing(6)

        self._lbl_sharpe = QLabel("—")
        self._lbl_max_drawdown = QLabel("—")
        self._lbl_total_return = QLabel("—")
        right_form.addRow("Sharpe Ratio:", self._lbl_sharpe)
        right_form.addRow("Max Drawdown:", self._lbl_max_drawdown)
        right_form.addRow("Total Return:", self._lbl_total_return)

        kpi_layout.addLayout(left_form)
        kpi_layout.addLayout(right_form)
        kpi_layout.addStretch()
        vbox.addLayout(kpi_layout)

        # Trades table
        columns = ["날짜", "시간", "Side", "가격", "수량", "P&L", "누적P&L"]
        self._trades_table = QTableWidget()
        self._trades_table.setColumnCount(len(columns))
        self._trades_table.setHorizontalHeaderLabels(columns)
        self._trades_table.setAlternatingRowColors(True)
        self._trades_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._trades_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._trades_table.verticalHeader().setVisible(False)
        self._trades_table.horizontalHeader().setStretchLastSection(True)
        vbox.addWidget(self._trades_table)

        return group

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _on_run_clicked(self) -> None:
        start = self.date_start.date()
        end = self.date_end.date()
        if start >= end:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "날짜 오류", "시작일이 종료일보다 앞서야 합니다.")
            return
        ticker = self.combo_ticker.currentText().strip()
        if not ticker:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "종목 미선택", "백테스트할 종목을 선택하세요.")
            return
        params = {
            "strategy": self.combo_strategy.currentText().lower(),
            "start_date": start.toString("yyyy-MM-dd"),
            "end_date": end.toString("yyyy-MM-dd"),
            "ticker": ticker,
        }
        self.run_backtest_clicked.emit(params)

    def _on_compare_clicked(self) -> None:
        start = self.date_start.date()
        end = self.date_end.date()
        if start >= end:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "날짜 오류", "시작일이 종료일보다 앞서야 합니다.")
            return
        ticker = self.combo_ticker.currentText().strip()
        if not ticker:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "종목 미선택", "종목을 선택하세요.")
            return
        self.run_compare_clicked.emit({
            "start_date": start.toString("yyyy-MM-dd"),
            "end_date": end.toString("yyyy-MM-dd"),
            "ticker": ticker,
        })

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def set_tickers(self, tickers: list[str]) -> None:
        """Populate the ticker combo box."""
        self.combo_ticker.clear()
        self.combo_ticker.addItems(tickers)

    def set_progress(self, value: int) -> None:
        """Update progress bar. Show if value > 0, hide if 0 or 100."""
        self._progress_bar.setValue(value)
        self._progress_bar.setVisible(0 < value < 100)

    def show_results(self, kpi: dict, trades: list[dict]) -> None:
        """Show results panel.
        kpi keys: total_trades, win_rate, profit_factor, sharpe, max_drawdown, total_return.
        trades: list of dict with date, time, side, price, qty, pnl, cumulative_pnl.
        Format: win_rate/max_drawdown/total_return as %, sharpe 2 decimals, profit_factor 1 decimal.
        Color: win_rate green if >50%, pnl green/red, total_return green/red.
        """
        # KPI labels
        total_trades = kpi.get("total_trades", 0)
        win_rate = kpi.get("win_rate", 0.0)
        profit_factor = kpi.get("profit_factor", 0.0)
        sharpe = kpi.get("sharpe", 0.0)
        max_drawdown = kpi.get("max_drawdown", 0.0)
        total_return = kpi.get("total_return", 0.0)

        self._lbl_total_trades.setText(str(total_trades))

        win_rate_color = "#a6e3a1" if win_rate > 50 else "#f38ba8"
        self._lbl_win_rate.setText(f"{win_rate:.1f}%")
        self._lbl_win_rate.setStyleSheet(f"color: {win_rate_color}; font-weight: bold;")

        self._lbl_profit_factor.setText(f"{profit_factor:.1f}")

        self._lbl_sharpe.setText(f"{sharpe:.2f}")

        self._lbl_max_drawdown.setText(f"{max_drawdown:.1f}%")

        return_color = "#a6e3a1" if total_return >= 0 else "#f38ba8"
        sign = "+" if total_return >= 0 else ""
        self._lbl_total_return.setText(f"{sign}{total_return:.1f}%")
        self._lbl_total_return.setStyleSheet(
            f"color: {return_color}; font-weight: bold;"
        )

        # Trades table
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

            cum_pnl = row_data.get("cumulative_pnl", 0.0)
            cum_color = QColor("#a6e3a1") if cum_pnl >= 0 else QColor("#f38ba8")
            cum_sign = "+" if cum_pnl >= 0 else ""

            cells = [
                (row_data.get("date", ""), None),
                (row_data.get("time", ""), None),
                (side, side_color),
                (f"{row_data.get('price', 0):,.0f}", None),
                (str(row_data.get("qty", 0)), None),
                (f"{pnl_sign}{pnl:,.0f}", pnl_color),
                (f"{cum_sign}{cum_pnl:,.0f}", cum_color),
            ]

            for col, (text, color) in enumerate(cells):
                cell = QTableWidgetItem(text)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if color is not None:
                    cell.setForeground(color)
                table.setItem(row, col, cell)

        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)

        self._results_panel.setVisible(True)
        self._update_charts(trades)

    def show_compare_results(self, results: list[dict]) -> None:
        """6전략 비교 결과 표시."""
        self._results_panel.setVisible(True)
        # KPI 라벨 초기화
        self._lbl_total_trades.setText("비교 모드")
        self._lbl_win_rate.setText("—")
        self._lbl_profit_factor.setText("—")
        self._lbl_sharpe.setText("—")
        self._lbl_max_drawdown.setText("—")
        self._lbl_total_return.setText("—")

        table = self._trades_table
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels(["전략", "거래", "승률", "PF", "총PnL", "판정"])
        table.setRowCount(0)

        for r in results:
            row = table.rowCount()
            table.insertRow(row)
            pf = r.get("profit_factor", 0)
            pnl = r.get("total_pnl", 0)
            pf_text = f"{pf:.2f}" if pf < 100 else "INF"
            pnl_color = QColor("#a6e3a1") if pnl >= 0 else QColor("#f38ba8")
            pf_color = QColor("#a6e3a1") if pf >= 1.0 else QColor("#f38ba8")

            cells = [
                (r.get("strategy", ""), None),
                (str(r.get("total_trades", 0)), None),
                (f"{r.get('win_rate', 0)*100:.1f}%", None),
                (pf_text, pf_color),
                (f"{pnl:+,.0f}", pnl_color),
                (r.get("verdict", ""), None),
            ]
            for col, (text, color) in enumerate(cells):
                cell = QTableWidgetItem(text)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if color:
                    cell.setForeground(color)
                table.setItem(row, col, cell)

        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)

    def _update_charts(self, trades: list[dict]):
        if not trades:
            self._charts_panel.setVisible(False)
            return

        # 청산 사유 분포
        reason_counts: dict[str, int] = {}
        for t in trades:
            reason = t.get("side", "unknown") or "unknown"
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

        self._chart_exit.clear()
        reasons = list(reason_counts.keys())
        counts = [reason_counts[r] for r in reasons]
        colors = {
            "forced_close": "#f9e2af", "stop_loss": "#f38ba8", "trailing_stop": "#89dceb",
            "breakeven_stop": "#a6e3a1", "momentum_fade": "#cba6f7", "limit_up_exit": "#89b4fa",
            "manual_close": "#fab387",
        }
        bar_item = pg.BarGraphItem(
            x=list(range(len(reasons))), height=counts, width=0.7,
            brushes=[colors.get(r, "#6c7086") for r in reasons],
        )
        self._chart_exit.addItem(bar_item)
        ax = self._chart_exit.getAxis("bottom")
        ax.setTicks([[(i, r[:6]) for i, r in enumerate(reasons)]])
        ax.setPen(pg.mkPen("#585b70"))
        ax.setTextPen(pg.mkPen("#a6adc8"))
        self._chart_exit.getAxis("left").setPen(pg.mkPen("#585b70"))
        self._chart_exit.getAxis("left").setTextPen(pg.mkPen("#a6adc8"))

        # PnL 분포 히스토그램
        self._chart_pnl.clear()
        pnls = [t.get("pnl", 0) for t in trades if t.get("pnl") is not None]
        if pnls:
            arr = np.array(pnls, dtype=float)
            y, x = np.histogram(arr, bins=20)
            step = pg.PlotCurveItem(
                x, np.append(y, 0), stepMode="center",
                fillLevel=0,
                brush=pg.mkBrush(166, 227, 161, 80),
                pen=pg.mkPen("#a6e3a1", width=1),
            )
            self._chart_pnl.addItem(step)
            # 0 기준선
            zero_line = pg.InfiniteLine(
                pos=0, angle=90,
                pen=pg.mkPen("#585b70", width=1, style=Qt.PenStyle.DashLine),
            )
            self._chart_pnl.addItem(zero_line)
        self._chart_pnl.getAxis("bottom").setPen(pg.mkPen("#585b70"))
        self._chart_pnl.getAxis("bottom").setTextPen(pg.mkPen("#a6adc8"))
        self._chart_pnl.getAxis("left").setPen(pg.mkPen("#585b70"))
        self._chart_pnl.getAxis("left").setTextPen(pg.mkPen("#a6adc8"))

        self._charts_panel.setVisible(True)
