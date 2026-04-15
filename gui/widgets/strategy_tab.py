"""Strategy Tab — Strategy parameter editor, risk settings, and universe manager."""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QFormLayout,
    QSpinBox,
    QDoubleSpinBox,
    QLabel,
    QPushButton,
    QLineEdit,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt, pyqtSignal


class StrategyTab(QWidget):
    """Strategy tab for editing momentum parameters, risk settings, and universe."""

    settings_saved = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        root.addWidget(self._build_momentum_page())
        root.addWidget(self._build_risk_settings())
        root.addWidget(self._build_universe_editor())
        root.addLayout(self._build_save_button())

        root.addStretch()

        scroll.setWidget(container)
        outer.addWidget(scroll)

    def _build_momentum_page(self) -> QGroupBox:
        group = QGroupBox("Momentum 파라미터")
        form = QFormLayout(group)
        form.setContentsMargins(10, 16, 10, 10)
        form.setSpacing(8)

        self._mom_volume_ratio = QDoubleSpinBox()
        self._mom_volume_ratio.setRange(0.0, 10.0)
        self._mom_volume_ratio.setValue(2.0)
        self._mom_volume_ratio.setSuffix(" 배")
        self._mom_volume_ratio.setDecimals(1)
        form.addRow("volume_ratio:", self._mom_volume_ratio)

        self._mom_stop_loss = QDoubleSpinBox()
        self._mom_stop_loss.setRange(0.0, 5.0)
        self._mom_stop_loss.setValue(0.8)
        self._mom_stop_loss.setSuffix(" %")
        self._mom_stop_loss.setDecimals(1)
        self._mom_stop_loss.setSingleStep(0.1)
        form.addRow("stop_loss_pct:", self._mom_stop_loss)

        return group

    def _build_risk_settings(self) -> QGroupBox:
        group = QGroupBox("리스크 설정")
        form = QFormLayout(group)
        form.setContentsMargins(10, 16, 10, 10)
        form.setSpacing(8)

        self._risk_tp1 = QDoubleSpinBox()
        self._risk_tp1.setRange(0.0, 10.0)
        self._risk_tp1.setValue(3.0)
        self._risk_tp1.setSuffix(" %")
        self._risk_tp1.setDecimals(1)
        self._risk_tp1.setSingleStep(0.1)
        form.addRow("tp1_pct:", self._risk_tp1)

        self._risk_max_daily_loss = QDoubleSpinBox()
        self._risk_max_daily_loss.setRange(0.0, 10.0)
        self._risk_max_daily_loss.setValue(2.0)
        self._risk_max_daily_loss.setSuffix(" %")
        self._risk_max_daily_loss.setDecimals(1)
        self._risk_max_daily_loss.setSingleStep(0.1)
        form.addRow("max_daily_loss_pct:", self._risk_max_daily_loss)

        self._risk_max_trades = QSpinBox()
        self._risk_max_trades.setRange(1, 20)
        self._risk_max_trades.setValue(3)
        form.addRow("max_trades_per_day:", self._risk_max_trades)

        self._risk_cooldown = QSpinBox()
        self._risk_cooldown.setRange(0, 60)
        self._risk_cooldown.setValue(15)
        self._risk_cooldown.setSuffix(" 분")
        form.addRow("cooldown_minutes:", self._risk_cooldown)

        self._risk_first_leg = QDoubleSpinBox()
        self._risk_first_leg.setRange(0.0, 1.0)
        self._risk_first_leg.setValue(0.55)
        self._risk_first_leg.setDecimals(2)
        self._risk_first_leg.setSingleStep(0.05)
        form.addRow("first_leg_ratio:", self._risk_first_leg)

        form.addRow(QLabel(""))
        sep = QLabel("── 멀티 종목 설정 ──")
        sep.setStyleSheet("color: #6c7086; font-size: 10px;")
        form.addRow(sep)

        self._risk_max_positions = QSpinBox()
        self._risk_max_positions.setRange(1, 10)
        self._risk_max_positions.setValue(3)
        form.addRow("max_positions:", self._risk_max_positions)

        self._risk_screening_top_n = QSpinBox()
        self._risk_screening_top_n.setRange(1, 20)
        self._risk_screening_top_n.setValue(5)
        form.addRow("screening_top_n:", self._risk_screening_top_n)

        return group

    def _build_universe_editor(self) -> QGroupBox:
        group = QGroupBox("종목 유니버스")
        vbox = QVBoxLayout(group)
        vbox.setContentsMargins(10, 16, 10, 10)
        vbox.setSpacing(8)

        self._universe_count_label = QLabel("0종목")
        self._universe_count_label.setStyleSheet("font-size: 11px; color: #6c7086;")
        vbox.addWidget(self._universe_count_label)

        self._universe_table = QTableWidget()
        columns = ["코드", "종목명", "시장"]
        self._universe_table.setColumnCount(len(columns))
        self._universe_table.setHorizontalHeaderLabels(columns)
        self._universe_table.setAlternatingRowColors(True)
        self._universe_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._universe_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._universe_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._universe_table.verticalHeader().setVisible(False)
        self._universe_table.setMaximumHeight(200)
        vbox.addWidget(self._universe_table)

        controls = QHBoxLayout()
        controls.setSpacing(8)

        self._universe_input = QLineEdit()
        self._universe_input.setPlaceholderText("종목코드 입력")
        controls.addWidget(self._universe_input)

        btn_add = QPushButton("추가")
        btn_add.clicked.connect(self._on_add_ticker)
        controls.addWidget(btn_add)

        btn_del = QPushButton("삭제")
        btn_del.clicked.connect(self._on_remove_ticker)
        controls.addWidget(btn_del)

        vbox.addLayout(controls)
        return group

    def _build_save_button(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.addStretch()

        btn_save = QPushButton("설정 저장")
        btn_save.setObjectName("startBtn")
        btn_save.clicked.connect(self.settings_saved)
        layout.addWidget(btn_save)

        return layout

    # ------------------------------------------------------------------
    # Internal handlers
    # ------------------------------------------------------------------

    def _on_add_ticker(self) -> None:
        text = self._universe_input.text().strip()
        if text:
            row = self._universe_table.rowCount()
            self._universe_table.insertRow(row)
            for col, val in enumerate([text, "", ""]):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._universe_table.setItem(row, col, item)
            self._universe_input.clear()
            self._universe_count_label.setText(f"{self._universe_table.rowCount()}종목")

    def _on_remove_ticker(self) -> None:
        rows = sorted(set(idx.row() for idx in self._universe_table.selectedIndexes()), reverse=True)
        for row in rows:
            self._universe_table.removeRow(row)
        self._universe_count_label.setText(f"{self._universe_table.rowCount()}종목")

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def load_config(self, config: dict) -> None:
        """Load values from config dict into all fields."""
        strategy_cfg = config.get("strategy", {})
        trading_cfg = config.get("trading", {})

        momentum = strategy_cfg.get("momentum", {})
        if "volume_ratio" in momentum:
            self._mom_volume_ratio.setValue(float(momentum["volume_ratio"]))
        if "stop_loss_pct" in momentum:
            self._mom_stop_loss.setValue(abs(float(momentum["stop_loss_pct"])) * 100)

        if "tp1_pct" in trading_cfg:
            self._risk_tp1.setValue(float(trading_cfg["tp1_pct"]) * 100)
        if "daily_max_loss_pct" in trading_cfg:
            self._risk_max_daily_loss.setValue(abs(float(trading_cfg["daily_max_loss_pct"])) * 100)
        if "max_trades_per_day" in trading_cfg:
            self._risk_max_trades.setValue(int(trading_cfg["max_trades_per_day"]))
        if "cooldown_minutes" in trading_cfg:
            self._risk_cooldown.setValue(int(trading_cfg["cooldown_minutes"]))
        if "entry_1st_ratio" in trading_cfg:
            self._risk_first_leg.setValue(float(trading_cfg["entry_1st_ratio"]))
        if "max_positions" in trading_cfg:
            self._risk_max_positions.setValue(int(trading_cfg["max_positions"]))
        if "screening_top_n" in trading_cfg:
            self._risk_screening_top_n.setValue(int(trading_cfg["screening_top_n"]))

    def get_config(self) -> dict:
        """Gather all field values into config dict matching config.yaml structure."""
        return {
            "strategy": {
                "momentum": {
                    "volume_ratio": self._mom_volume_ratio.value(),
                    "stop_loss_pct": -self._mom_stop_loss.value() / 100,
                },
            },
            "trading": {
                "tp1_pct": self._risk_tp1.value() / 100,
                "daily_max_loss_pct": -self._risk_max_daily_loss.value() / 100,
                "max_trades_per_day": self._risk_max_trades.value(),
                "cooldown_minutes": self._risk_cooldown.value(),
                "entry_1st_ratio": self._risk_first_leg.value(),
                "max_positions": self._risk_max_positions.value(),
                "screening_top_n": self._risk_screening_top_n.value(),
            },
        }

    def load_universe(self, stocks: list[dict]) -> None:
        """Populate universe table. stocks: [{"ticker": ..., "name": ..., "market": ...}, ...]"""
        self._universe_table.setRowCount(0)
        for s in stocks:
            row = self._universe_table.rowCount()
            self._universe_table.insertRow(row)
            ticker = s.get("ticker", "") if isinstance(s, dict) else str(s)
            name = s.get("name", "") if isinstance(s, dict) else ""
            market = s.get("market", "") if isinstance(s, dict) else ""
            for col, text in enumerate([ticker, name, market]):
                item = QTableWidgetItem(str(text))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._universe_table.setItem(row, col, item)
        self._universe_count_label.setText(f"{self._universe_table.rowCount()}종목")

    def get_universe(self) -> list[str]:
        """Return current universe ticker list."""
        tickers = []
        for i in range(self._universe_table.rowCount()):
            item = self._universe_table.item(i, 0)
            if item and item.text().strip():
                tickers.append(item.text().strip())
        return tickers
