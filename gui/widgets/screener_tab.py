"""Screener Tab — Candidate screening with filter controls and results table."""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QFormLayout,
    QSpinBox,
    QDoubleSpinBox,
    QPushButton,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor


class ScreenerTab(QWidget):
    """Screener tab for running candidate stock screening with filter controls."""

    run_screening_clicked = pyqtSignal()

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

        root.addWidget(self._build_filter_panel())
        root.addLayout(self._build_action_bar())
        root.addWidget(self._build_candidates_table(), stretch=1)

    def _build_filter_panel(self) -> QGroupBox:
        group = QGroupBox("필터 설정")
        form = QFormLayout(group)
        form.setContentsMargins(10, 16, 10, 10)
        form.setSpacing(8)

        # 최소 시가총액
        self._spin_market_cap = QSpinBox()
        self._spin_market_cap.setRange(0, 100000)
        self._spin_market_cap.setValue(3000)
        self._spin_market_cap.setSuffix(" 억원")
        form.addRow("최소 시가총액:", self._spin_market_cap)

        # 최소 거래대금
        self._spin_volume_amount = QSpinBox()
        self._spin_volume_amount.setRange(0, 10000)
        self._spin_volume_amount.setValue(50)
        self._spin_volume_amount.setSuffix(" 억원")
        form.addRow("최소 거래대금:", self._spin_volume_amount)

        # ATR 하한
        self._spin_atr = QDoubleSpinBox()
        self._spin_atr.setRange(0.0, 10.0)
        self._spin_atr.setValue(2.0)
        self._spin_atr.setSuffix(" %")
        self._spin_atr.setDecimals(1)
        self._spin_atr.setSingleStep(0.1)
        form.addRow("ATR 하한:", self._spin_atr)

        # 거래량 서지
        self._spin_volume_surge = QDoubleSpinBox()
        self._spin_volume_surge.setRange(0.0, 10.0)
        self._spin_volume_surge.setValue(1.5)
        self._spin_volume_surge.setSuffix(" 배")
        self._spin_volume_surge.setDecimals(1)
        self._spin_volume_surge.setSingleStep(0.1)
        form.addRow("거래량 서지:", self._spin_volume_surge)

        return group

    def _build_action_bar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(8)

        btn_run = QPushButton("스크리닝 실행")
        btn_run.setObjectName("startBtn")
        btn_run.clicked.connect(self.run_screening_clicked)
        layout.addWidget(btn_run)

        self._label_last_run = QLabel("마지막 실행: —")
        self._label_last_run.setStyleSheet("font-size: 11px; color: #6c7086;")
        layout.addWidget(self._label_last_run)

        layout.addStretch()

        label_auto = QLabel("자동: 08:30")
        label_auto.setStyleSheet("font-size: 11px; color: #6c7086;")
        layout.addWidget(label_auto)

        return layout

    def _build_candidates_table(self) -> QTableWidget:
        columns = ["#", "Ticker", "종목명", "시가총액", "거래대금", "서지비율", "ATR%", "MA20", "점수"]
        self._candidates_table = QTableWidget()
        self._candidates_table.setColumnCount(len(columns))
        self._candidates_table.setHorizontalHeaderLabels(columns)
        self._candidates_table.setAlternatingRowColors(True)
        self._candidates_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._candidates_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._candidates_table.verticalHeader().setVisible(False)
        self._candidates_table.horizontalHeader().setStretchLastSection(True)
        return self._candidates_table

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def update_candidates(self, candidates: list[dict]) -> None:
        """Update table. Each dict: ticker, name, market_cap, avg_volume_amount,
        volume_surge, atr_pct, ma20_trend, score, selected(bool).
        selected=True -> row background highlighted with mauve(#cba6f7) at 20% opacity.
        Format: market_cap in 억원, volume_amount in 억원, atr_pct as %, score 1 decimal.
        """
        table = self._candidates_table
        table.setRowCount(0)

        highlight_color = QColor("#cba6f7")
        highlight_color.setAlphaF(0.20)

        for idx, item in enumerate(candidates):
            row = table.rowCount()
            table.insertRow(row)

            selected = item.get("selected", False)

            market_cap = item.get("market_cap", 0)
            volume_amount = item.get("avg_volume_amount", 0)
            atr_pct = item.get("atr_pct", 0.0)
            surge = item.get("volume_surge", 0.0)
            score = item.get("score", 0.0)
            ma20 = item.get("ma20_trend", "")

            cells = [
                str(idx + 1),
                item.get("ticker", ""),
                item.get("name", ""),
                f"{market_cap:,.0f} 억",
                f"{volume_amount:,.0f} 억",
                f"{surge:.1f} 배",
                f"{atr_pct:.1f}%",
                str(ma20),
                f"{score:.1f}",
            ]

            for col, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if selected:
                    cell.setBackground(highlight_color)
                table.setItem(row, col, cell)

        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)

    def set_last_run_time(self, time_str: str) -> None:
        """Update the '마지막 실행' label."""
        self._label_last_run.setText(f"마지막 실행: {time_str}")

    def get_filter_values(self) -> dict:
        """Return dict: min_market_cap, min_volume_amount, min_atr_pct, min_volume_surge."""
        return {
            "min_market_cap": self._spin_market_cap.value(),
            "min_volume_amount": self._spin_volume_amount.value(),
            "min_atr_pct": self._spin_atr.value(),
            "min_volume_surge": self._spin_volume_surge.value(),
        }
