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
    QListWidget,
    QLineEdit,
    QStackedWidget,
    QScrollArea,
    QSizePolicy,
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

        label = QLabel("활성 전략:")
        label.setStyleSheet("font-weight: bold;")
        layout.addWidget(label)

        self._combo_active_strategy = QComboBox()
        self._combo_active_strategy.addItems(["ORB", "VWAP", "Momentum", "Pullback"])
        layout.addWidget(self._combo_active_strategy)
        layout.addStretch()

        return layout

    def _build_parameter_editor(self) -> QStackedWidget:
        self._param_stack = QStackedWidget()

        # Connect selector -> stack
        self._combo_active_strategy.currentIndexChanged.connect(
            self._param_stack.setCurrentIndex
        )

        self._param_stack.addWidget(self._build_orb_page())
        self._param_stack.addWidget(self._build_vwap_page())
        self._param_stack.addWidget(self._build_momentum_page())
        self._param_stack.addWidget(self._build_pullback_page())

        return self._param_stack

    def _build_orb_page(self) -> QGroupBox:
        group = QGroupBox("ORB 파라미터")
        form = QFormLayout(group)
        form.setContentsMargins(10, 16, 10, 10)
        form.setSpacing(8)

        self._orb_range_start = QTimeEdit()
        self._orb_range_start.setTime(QTime(9, 5))
        form.addRow("range_start:", self._orb_range_start)

        self._orb_range_end = QTimeEdit()
        self._orb_range_end.setTime(QTime(9, 15))
        form.addRow("range_end:", self._orb_range_end)

        self._orb_min_range_pct = QDoubleSpinBox()
        self._orb_min_range_pct.setRange(0.0, 5.0)
        self._orb_min_range_pct.setValue(0.8)
        self._orb_min_range_pct.setSuffix(" %")
        self._orb_min_range_pct.setDecimals(1)
        self._orb_min_range_pct.setSingleStep(0.1)
        form.addRow("min_range_pct:", self._orb_min_range_pct)

        self._orb_volume_ratio = QDoubleSpinBox()
        self._orb_volume_ratio.setRange(0.0, 10.0)
        self._orb_volume_ratio.setValue(0.0)
        self._orb_volume_ratio.setDecimals(1)
        self._orb_volume_ratio.setSingleStep(0.1)
        form.addRow("volume_ratio:", self._orb_volume_ratio)

        return group

    def _build_vwap_page(self) -> QGroupBox:
        group = QGroupBox("VWAP 파라미터")
        form = QFormLayout(group)
        form.setContentsMargins(10, 16, 10, 10)
        form.setSpacing(8)

        self._vwap_rsi_low = QDoubleSpinBox()
        self._vwap_rsi_low.setRange(0.0, 100.0)
        self._vwap_rsi_low.setValue(40.0)
        self._vwap_rsi_low.setDecimals(1)
        form.addRow("rsi_low:", self._vwap_rsi_low)

        self._vwap_rsi_high = QDoubleSpinBox()
        self._vwap_rsi_high.setRange(0.0, 100.0)
        self._vwap_rsi_high.setValue(60.0)
        self._vwap_rsi_high.setDecimals(1)
        form.addRow("rsi_high:", self._vwap_rsi_high)

        return group

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
        form.addRow("momentum_volume_ratio:", self._mom_volume_ratio)

        return group

    def _build_pullback_page(self) -> QGroupBox:
        group = QGroupBox("Pullback 파라미터")
        form = QFormLayout(group)
        form.setContentsMargins(10, 16, 10, 10)
        form.setSpacing(8)

        self._pullback_min_gain_pct = QDoubleSpinBox()
        self._pullback_min_gain_pct.setRange(0.0, 10.0)
        self._pullback_min_gain_pct.setValue(3.0)
        self._pullback_min_gain_pct.setSuffix(" %")
        self._pullback_min_gain_pct.setDecimals(1)
        form.addRow("min_gain_pct:", self._pullback_min_gain_pct)

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
        self._risk_tp1.setValue(2.0)
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
        self._risk_max_trades.setValue(5)
        form.addRow("max_trades_per_day:", self._risk_max_trades)

        self._risk_cooldown = QSpinBox()
        self._risk_cooldown.setRange(0, 60)
        self._risk_cooldown.setValue(10)
        self._risk_cooldown.setSuffix(" 분")
        form.addRow("cooldown_minutes:", self._risk_cooldown)

        self._risk_first_leg = QDoubleSpinBox()
        self._risk_first_leg.setRange(0.0, 1.0)
        self._risk_first_leg.setValue(0.55)
        self._risk_first_leg.setDecimals(2)
        self._risk_first_leg.setSingleStep(0.05)
        form.addRow("first_leg_ratio:", self._risk_first_leg)

        return group

    def _build_universe_editor(self) -> QGroupBox:
        group = QGroupBox("종목 유니버스")
        vbox = QVBoxLayout(group)
        vbox.setContentsMargins(10, 16, 10, 10)
        vbox.setSpacing(8)

        self._universe_list = QListWidget()
        vbox.addWidget(self._universe_list)

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
            self._universe_list.addItem(text)
            self._universe_input.clear()

    def _on_remove_ticker(self) -> None:
        for item in self._universe_list.selectedItems():
            self._universe_list.takeItem(self._universe_list.row(item))

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def load_config(self, config: dict) -> None:
        """Load values from config dict into all fields.
        config structure mirrors config.yaml: strategy.orb.*, strategy.vwap.*,
        trading.stop_loss_pct, etc.
        """
        strategy_cfg = config.get("strategy", {})
        trading_cfg = config.get("trading", {})

        # ORB
        orb = strategy_cfg.get("orb", {})
        if "range_start" in orb:
            t = QTime.fromString(str(orb["range_start"]), "hh:mm")
            if t.isValid():
                self._orb_range_start.setTime(t)
        if "range_end" in orb:
            t = QTime.fromString(str(orb["range_end"]), "hh:mm")
            if t.isValid():
                self._orb_range_end.setTime(t)
        if "min_range_pct" in orb:
            self._orb_min_range_pct.setValue(float(orb["min_range_pct"]))
        if "volume_ratio" in orb:
            self._orb_volume_ratio.setValue(float(orb["volume_ratio"]))

        # VWAP
        vwap = strategy_cfg.get("vwap", {})
        if "rsi_low" in vwap:
            self._vwap_rsi_low.setValue(float(vwap["rsi_low"]))
        if "rsi_high" in vwap:
            self._vwap_rsi_high.setValue(float(vwap["rsi_high"]))

        # Momentum
        momentum = strategy_cfg.get("momentum", {})
        if "momentum_volume_ratio" in momentum:
            self._mom_volume_ratio.setValue(float(momentum["momentum_volume_ratio"]))

        # Pullback
        pullback = strategy_cfg.get("pullback", {})
        if "min_gain_pct" in pullback:
            self._pullback_min_gain_pct.setValue(float(pullback["min_gain_pct"]))

        # Risk / trading
        if "stop_loss_pct" in trading_cfg:
            self._risk_stop_loss.setValue(float(trading_cfg["stop_loss_pct"]))
        if "tp1_pct" in trading_cfg:
            self._risk_tp1.setValue(float(trading_cfg["tp1_pct"]))
        if "max_daily_loss_pct" in trading_cfg:
            self._risk_max_daily_loss.setValue(float(trading_cfg["max_daily_loss_pct"]))
        if "max_trades_per_day" in trading_cfg:
            self._risk_max_trades.setValue(int(trading_cfg["max_trades_per_day"]))
        if "cooldown_minutes" in trading_cfg:
            self._risk_cooldown.setValue(int(trading_cfg["cooldown_minutes"]))
        if "first_leg_ratio" in trading_cfg:
            self._risk_first_leg.setValue(float(trading_cfg["first_leg_ratio"]))

    def get_config(self) -> dict:
        """Gather all field values into config dict matching config.yaml structure."""
        return {
            "strategy": {
                "orb": {
                    "range_start": self._orb_range_start.time().toString("hh:mm"),
                    "range_end": self._orb_range_end.time().toString("hh:mm"),
                    "min_range_pct": self._orb_min_range_pct.value(),
                    "volume_ratio": self._orb_volume_ratio.value(),
                },
                "vwap": {
                    "rsi_low": self._vwap_rsi_low.value(),
                    "rsi_high": self._vwap_rsi_high.value(),
                },
                "momentum": {
                    "momentum_volume_ratio": self._mom_volume_ratio.value(),
                },
                "pullback": {
                    "min_gain_pct": self._pullback_min_gain_pct.value(),
                },
            },
            "trading": {
                "stop_loss_pct": self._risk_stop_loss.value(),
                "tp1_pct": self._risk_tp1.value(),
                "max_daily_loss_pct": self._risk_max_daily_loss.value(),
                "max_trades_per_day": self._risk_max_trades.value(),
                "cooldown_minutes": self._risk_cooldown.value(),
                "first_leg_ratio": self._risk_first_leg.value(),
            },
        }

    def load_universe(self, tickers: list[str]) -> None:
        """Populate the universe list widget."""
        self._universe_list.clear()
        self._universe_list.addItems(tickers)

    def get_universe(self) -> list[str]:
        """Return current universe ticker list."""
        return [
            self._universe_list.item(i).text()
            for i in range(self._universe_list.count())
        ]
