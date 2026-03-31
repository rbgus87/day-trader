"""Strategy Tab — Strategy parameter editor, risk settings, and universe manager."""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QFormLayout,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QTimeEdit,
    QLabel,
    QPushButton,
    QLineEdit,
    QStackedWidget,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt, QTime, pyqtSignal


class StrategyTab(QWidget):
    """Strategy tab for editing strategy parameters, risk settings, and universe."""

    settings_saved = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Outer layout holds a scroll area
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

        # Strategy selector row
        root.addLayout(self._build_strategy_selector())

        # Parameter stacked widget
        root.addWidget(self._build_parameter_editor())

        # Risk settings
        root.addWidget(self._build_risk_settings())

        # Universe editor
        root.addWidget(self._build_universe_editor())

        # Save button
        root.addLayout(self._build_save_button())

        root.addStretch()

        scroll.setWidget(container)
        outer.addWidget(scroll)

    def _build_strategy_selector(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(8)

        label = QLabel("파라미터 편집:")
        label.setStyleSheet("font-weight: bold;")
        layout.addWidget(label)

        self._combo_active_strategy = QComboBox()
        self._combo_active_strategy.addItems(["Momentum", "Pullback", "Flow", "Gap", "OpenBreak", "BigCandle"])
        layout.addWidget(self._combo_active_strategy)
        layout.addStretch()

        return layout

    def _build_parameter_editor(self) -> QStackedWidget:
        self._param_stack = QStackedWidget()

        # Connect selector -> stack
        self._combo_active_strategy.currentIndexChanged.connect(
            self._param_stack.setCurrentIndex
        )

        self._param_stack.addWidget(self._build_momentum_page())
        self._param_stack.addWidget(self._build_pullback_page())
        self._param_stack.addWidget(self._build_flow_page())
        self._param_stack.addWidget(self._build_gap_page())
        self._param_stack.addWidget(self._build_open_break_page())
        self._param_stack.addWidget(self._build_big_candle_page())

        return self._param_stack

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

    def _build_pullback_page(self) -> QGroupBox:
        group = QGroupBox("Pullback 파라미터")
        form = QFormLayout(group)
        form.setContentsMargins(10, 16, 10, 10)
        form.setSpacing(8)

        self._pullback_min_gain_pct = QDoubleSpinBox()
        self._pullback_min_gain_pct.setRange(0.0, 10.0)
        self._pullback_min_gain_pct.setValue(4.0)
        self._pullback_min_gain_pct.setSuffix(" %")
        self._pullback_min_gain_pct.setDecimals(1)
        form.addRow("min_gain_pct:", self._pullback_min_gain_pct)

        self._pullback_stop_loss = QDoubleSpinBox()
        self._pullback_stop_loss.setRange(0.0, 5.0)
        self._pullback_stop_loss.setValue(1.8)
        self._pullback_stop_loss.setSuffix(" %")
        self._pullback_stop_loss.setDecimals(1)
        self._pullback_stop_loss.setSingleStep(0.1)
        form.addRow("stop_loss_pct:", self._pullback_stop_loss)

        return group

    def _build_flow_page(self) -> QGroupBox:
        group = QGroupBox("Flow 파라미터")
        form = QFormLayout(group)
        form.setContentsMargins(10, 16, 10, 10)
        form.setSpacing(8)

        self._flow_volume_surge = QDoubleSpinBox()
        self._flow_volume_surge.setRange(0.0, 10.0)
        self._flow_volume_surge.setValue(2.5)
        self._flow_volume_surge.setSuffix(" 배")
        self._flow_volume_surge.setDecimals(1)
        form.addRow("volume_surge_ratio:", self._flow_volume_surge)

        self._flow_stop_loss = QDoubleSpinBox()
        self._flow_stop_loss.setRange(0.0, 5.0)
        self._flow_stop_loss.setValue(1.5)
        self._flow_stop_loss.setSuffix(" %")
        self._flow_stop_loss.setDecimals(1)
        self._flow_stop_loss.setSingleStep(0.1)
        form.addRow("stop_loss_pct:", self._flow_stop_loss)

        self._flow_trailing_stop = QDoubleSpinBox()
        self._flow_trailing_stop.setRange(0.0, 5.0)
        self._flow_trailing_stop.setValue(1.5)
        self._flow_trailing_stop.setSuffix(" %")
        self._flow_trailing_stop.setDecimals(1)
        self._flow_trailing_stop.setSingleStep(0.1)
        form.addRow("trailing_stop_pct:", self._flow_trailing_stop)

        self._flow_signal_start = QTimeEdit()
        self._flow_signal_start.setTime(QTime(9, 30))
        form.addRow("signal_start:", self._flow_signal_start)

        self._flow_signal_end = QTimeEdit()
        self._flow_signal_end.setTime(QTime(14, 30))
        form.addRow("signal_end:", self._flow_signal_end)

        return group

    def _build_gap_page(self) -> QGroupBox:
        group = QGroupBox("Gap 파라미터")
        form = QFormLayout(group)
        form.setContentsMargins(10, 16, 10, 10)
        form.setSpacing(8)

        self._gap_min_gap = QDoubleSpinBox()
        self._gap_min_gap.setRange(0.0, 10.0)
        self._gap_min_gap.setValue(1.5)
        self._gap_min_gap.setSuffix(" %")
        self._gap_min_gap.setDecimals(1)
        self._gap_min_gap.setSingleStep(0.1)
        form.addRow("min_gap_pct:", self._gap_min_gap)

        self._gap_stop_loss = QDoubleSpinBox()
        self._gap_stop_loss.setRange(0.0, 5.0)
        self._gap_stop_loss.setValue(1.0)
        self._gap_stop_loss.setSuffix(" %")
        self._gap_stop_loss.setDecimals(1)
        self._gap_stop_loss.setSingleStep(0.1)
        form.addRow("stop_loss_pct:", self._gap_stop_loss)

        return group

    def _build_open_break_page(self) -> QGroupBox:
        group = QGroupBox("OpenBreak 파라미터")
        form = QFormLayout(group)
        form.setContentsMargins(10, 16, 10, 10)
        form.setSpacing(8)

        self._ob_break_pct = QDoubleSpinBox()
        self._ob_break_pct.setRange(0.0, 5.0)
        self._ob_break_pct.setValue(0.5)
        self._ob_break_pct.setSuffix(" %")
        self._ob_break_pct.setDecimals(1)
        self._ob_break_pct.setSingleStep(0.1)
        form.addRow("break_pct:", self._ob_break_pct)

        self._ob_volume_ratio = QDoubleSpinBox()
        self._ob_volume_ratio.setRange(0.0, 5.0)
        self._ob_volume_ratio.setValue(0.3)
        self._ob_volume_ratio.setSuffix(" 배")
        self._ob_volume_ratio.setDecimals(1)
        self._ob_volume_ratio.setSingleStep(0.1)
        form.addRow("volume_ratio:", self._ob_volume_ratio)

        self._ob_stop_loss = QDoubleSpinBox()
        self._ob_stop_loss.setRange(0.0, 5.0)
        self._ob_stop_loss.setValue(0.5)
        self._ob_stop_loss.setSuffix(" %")
        self._ob_stop_loss.setDecimals(1)
        self._ob_stop_loss.setSingleStep(0.1)
        form.addRow("stop_loss_pct:", self._ob_stop_loss)

        self._ob_signal_start = QTimeEdit()
        self._ob_signal_start.setTime(QTime(9, 15))
        form.addRow("signal_start:", self._ob_signal_start)

        return group

    def _build_big_candle_page(self) -> QGroupBox:
        group = QGroupBox("BigCandle 파라미터")
        form = QFormLayout(group)
        form.setContentsMargins(10, 16, 10, 10)
        form.setSpacing(8)

        self._bc_atr_multiplier = QDoubleSpinBox()
        self._bc_atr_multiplier.setRange(0.5, 5.0)
        self._bc_atr_multiplier.setValue(1.5)
        self._bc_atr_multiplier.setSuffix(" 배")
        self._bc_atr_multiplier.setDecimals(1)
        self._bc_atr_multiplier.setSingleStep(0.1)
        form.addRow("atr_multiplier:", self._bc_atr_multiplier)

        self._bc_timeout = QSpinBox()
        self._bc_timeout.setRange(5, 120)
        self._bc_timeout.setValue(30)
        self._bc_timeout.setSuffix(" 분")
        form.addRow("timeout_minutes:", self._bc_timeout)

        self._bc_stop_loss = QDoubleSpinBox()
        self._bc_stop_loss.setRange(0.0, 5.0)
        self._bc_stop_loss.setValue(1.0)
        self._bc_stop_loss.setSuffix(" %")
        self._bc_stop_loss.setDecimals(1)
        self._bc_stop_loss.setSingleStep(0.1)
        form.addRow("stop_loss_pct:", self._bc_stop_loss)

        return group

    def _build_risk_settings(self) -> QGroupBox:
        group = QGroupBox("리스크 설정")
        form = QFormLayout(group)
        form.setContentsMargins(10, 16, 10, 10)
        form.setSpacing(8)

        self._risk_stop_loss = QDoubleSpinBox()
        self._risk_stop_loss.setRange(0.0, 10.0)
        self._risk_stop_loss.setValue(1.5)
        self._risk_stop_loss.setSuffix(" %")
        self._risk_stop_loss.setDecimals(1)
        self._risk_stop_loss.setSingleStep(0.1)
        form.addRow("stop_loss_pct:", self._risk_stop_loss)

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

        # 멀티 종목 설정
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

        self._risk_time_stop_minutes = QSpinBox()
        self._risk_time_stop_minutes.setRange(0, 300)
        self._risk_time_stop_minutes.setValue(60)
        self._risk_time_stop_minutes.setSuffix(" 분")
        form.addRow("time_stop_minutes:", self._risk_time_stop_minutes)

        self._risk_time_stop_min_profit = QDoubleSpinBox()
        self._risk_time_stop_min_profit.setRange(0.0, 5.0)
        self._risk_time_stop_min_profit.setValue(0.5)
        self._risk_time_stop_min_profit.setSuffix(" %")
        self._risk_time_stop_min_profit.setDecimals(1)
        self._risk_time_stop_min_profit.setSingleStep(0.1)
        form.addRow("time_stop_min_profit:", self._risk_time_stop_min_profit)

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

        # Momentum
        momentum = strategy_cfg.get("momentum", {})
        if "volume_ratio" in momentum:
            self._mom_volume_ratio.setValue(float(momentum["volume_ratio"]))
        if "stop_loss_pct" in momentum:
            self._mom_stop_loss.setValue(abs(float(momentum["stop_loss_pct"])) * 100)

        # Pullback
        pullback = strategy_cfg.get("pullback", {})
        if "min_gain_pct" in pullback:
            self._pullback_min_gain_pct.setValue(float(pullback["min_gain_pct"]) * 100)
        if "stop_loss_pct" in pullback:
            self._pullback_stop_loss.setValue(abs(float(pullback["stop_loss_pct"])) * 100)

        # Flow
        flow = strategy_cfg.get("flow", {})
        if "volume_surge_ratio" in flow:
            self._flow_volume_surge.setValue(float(flow["volume_surge_ratio"]))
        if "stop_loss_pct" in flow:
            self._flow_stop_loss.setValue(abs(float(flow["stop_loss_pct"])) * 100)
        if "trailing_stop_pct" in flow:
            self._flow_trailing_stop.setValue(float(flow["trailing_stop_pct"]) * 100)
        if "signal_start" in flow:
            t = QTime.fromString(str(flow["signal_start"]), "hh:mm")
            if t.isValid():
                self._flow_signal_start.setTime(t)
        if "signal_end" in flow:
            t = QTime.fromString(str(flow["signal_end"]), "hh:mm")
            if t.isValid():
                self._flow_signal_end.setTime(t)

        # Gap
        gap = strategy_cfg.get("gap", {})
        if "min_gap_pct" in gap:
            self._gap_min_gap.setValue(float(gap["min_gap_pct"]) * 100)
        if "stop_loss_pct" in gap:
            self._gap_stop_loss.setValue(abs(float(gap["stop_loss_pct"])) * 100)

        # OpenBreak
        ob = strategy_cfg.get("open_break", {})
        if "break_pct" in ob:
            self._ob_break_pct.setValue(float(ob["break_pct"]) * 100)
        if "volume_ratio" in ob:
            self._ob_volume_ratio.setValue(float(ob["volume_ratio"]))
        if "stop_loss_pct" in ob:
            self._ob_stop_loss.setValue(abs(float(ob["stop_loss_pct"])) * 100)
        if "signal_start" in ob:
            t = QTime.fromString(str(ob["signal_start"]), "hh:mm")
            if t.isValid():
                self._ob_signal_start.setTime(t)

        # BigCandle
        bc = strategy_cfg.get("big_candle", {})
        if "atr_multiplier" in bc:
            self._bc_atr_multiplier.setValue(float(bc["atr_multiplier"]))
        if "timeout_minutes" in bc:
            self._bc_timeout.setValue(int(bc["timeout_minutes"]))
        if "stop_loss_pct" in bc:
            self._bc_stop_loss.setValue(abs(float(bc["stop_loss_pct"])) * 100)

        # Risk / trading
        if "stop_loss_pct" in trading_cfg:
            self._risk_stop_loss.setValue(abs(float(trading_cfg["stop_loss_pct"])) * 100)
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
        if "time_stop_minutes" in trading_cfg:
            self._risk_time_stop_minutes.setValue(int(trading_cfg["time_stop_minutes"]))
        if "time_stop_min_profit" in trading_cfg:
            self._risk_time_stop_min_profit.setValue(float(trading_cfg["time_stop_min_profit"]) * 100)

    def get_config(self) -> dict:
        """Gather all field values into config dict matching config.yaml structure."""
        return {
            "strategy": {
                "momentum": {
                    "volume_ratio": self._mom_volume_ratio.value(),
                    "stop_loss_pct": -self._mom_stop_loss.value() / 100,
                },
                "pullback": {
                    "min_gain_pct": self._pullback_min_gain_pct.value() / 100,
                    "stop_loss_pct": -self._pullback_stop_loss.value() / 100,
                },
                "flow": {
                    "volume_surge_ratio": self._flow_volume_surge.value(),
                    "stop_loss_pct": -self._flow_stop_loss.value() / 100,
                    "trailing_stop_pct": self._flow_trailing_stop.value() / 100,
                    "signal_start": self._flow_signal_start.time().toString("hh:mm"),
                    "signal_end": self._flow_signal_end.time().toString("hh:mm"),
                },
                "gap": {
                    "min_gap_pct": self._gap_min_gap.value() / 100,
                    "stop_loss_pct": -self._gap_stop_loss.value() / 100,
                },
                "open_break": {
                    "break_pct": self._ob_break_pct.value() / 100,
                    "volume_ratio": self._ob_volume_ratio.value(),
                    "stop_loss_pct": -self._ob_stop_loss.value() / 100,
                    "signal_start": self._ob_signal_start.time().toString("hh:mm"),
                },
                "big_candle": {
                    "atr_multiplier": self._bc_atr_multiplier.value(),
                    "timeout_minutes": self._bc_timeout.value(),
                    "stop_loss_pct": -self._bc_stop_loss.value() / 100,
                },
            },
            "trading": {
                "stop_loss_pct": -self._risk_stop_loss.value() / 100,
                "tp1_pct": self._risk_tp1.value() / 100,
                "daily_max_loss_pct": -self._risk_max_daily_loss.value() / 100,
                "max_trades_per_day": self._risk_max_trades.value(),
                "cooldown_minutes": self._risk_cooldown.value(),
                "entry_1st_ratio": self._risk_first_leg.value(),
                "max_positions": self._risk_max_positions.value(),
                "screening_top_n": self._risk_screening_top_n.value(),
                "time_stop_minutes": self._risk_time_stop_minutes.value(),
                "time_stop_min_profit": self._risk_time_stop_min_profit.value() / 100,
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
