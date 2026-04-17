"""Strategy Tab — 현재 운영 파라미터 전체 + 편집 가능 필드 구분.

섹션 구성 (ADR-010~016 반영):
  1) 진입 조건   — volume_ratio, min_breakout_pct, buy_time_end, adx_*
  2) 청산 조건   — stop_loss, atr_trail_*
  3) 리스크 관리 — max_positions, daily_max_loss, max_trades, cooldown
  4) 자본 설정   — initial_capital, entry_1st_ratio
  5) 시장 필터   — market_filter, ma_length
  6) 알림 정책   — 12종 토글 (ADR-008, 012, 014)
  7) 종목 유니버스 — 41종목 + 주간 갱신 상태

값의 실제 영향 범위에 따라:
  - 편집 가능(SpinBox/CheckBox): 운영 중 조정 가능한 값
  - 표시 전용(Label): 코드 변경 또는 재백테스트 필요
"""

from pathlib import Path

import yaml
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


# 읽기 전용 라벨 스타일 (코드 변경 필요 필드)
_RO_STYLE = (
    "color: #a6adc8; padding: 4px 8px; background-color: #181825; "
    "border: 1px solid #313244; border-radius: 3px;"
)
_RO_TOOLTIP = "코드/알고리즘 변경이 필요한 파라미터 (재백테스트 검증 후 반영)"


def _ro_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(_RO_STYLE)
    lbl.setToolTip(_RO_TOOLTIP)
    return lbl


class StrategyTab(QWidget):
    """전략 탭 — 현재 시스템 설정 전체 표시 + 편집 가능 필드 구분."""

    settings_saved = pyqtSignal()

    # ADR-008/012/014: 알림 정책 토글 12종
    _NOTIFICATION_FIELDS = [
        ("daily_reset", "일일 리셋 (00:01)"),
        ("ohlcv_refresh", "OHLCV 갱신 (08:05)"),
        ("token_refresh_failure", "토큰 갱신 실패"),
        ("trade_execution", "매수/매도 체결"),
        ("daily_report", "일일 보고 (15:30)"),
        ("system_start", "시스템 시작"),
        ("system_stop", "시스템 종료"),
        ("uptime_sanity", "24시간 가동 안내"),
        ("ws_critical_failure", "WS 긴급 실패 (3회)"),
        ("ws_auto_recovery", "WS 자동 재연결 성공"),
        ("universe_refresh", "유니버스 갱신 (ADR-012)"),
        ("candle_collection", "분봉 수집 (ADR-014)"),
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # 읽기 전용 라벨 핸들 (load_config에서 갱신)
        self._lbl_volume_ratio: QLabel | None = None
        self._lbl_min_breakout_pct: QLabel | None = None
        self._lbl_adx_min: QLabel | None = None
        self._lbl_adx_length: QLabel | None = None
        self._lbl_stop_loss_pct: QLabel | None = None
        self._lbl_atr_trail_multiplier: QLabel | None = None
        self._lbl_atr_trail_min_pct: QLabel | None = None
        self._lbl_atr_trail_max_pct: QLabel | None = None
        self._lbl_initial_capital: QLabel | None = None
        self._lbl_entry_1st_ratio: QLabel | None = None
        self._lbl_market_ma_length: QLabel | None = None
        self._lbl_universe_refresh_status: QLabel | None = None
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
        root.setSpacing(10)

        root.addWidget(self._build_entry_section())
        root.addWidget(self._build_exit_section())
        root.addWidget(self._build_risk_section())
        root.addWidget(self._build_capital_section())
        root.addWidget(self._build_market_section())
        root.addWidget(self._build_notifications_section())
        root.addWidget(self._build_universe_editor())
        root.addLayout(self._build_save_button())

        root.addStretch()

        scroll.setWidget(container)
        outer.addWidget(scroll)

    # ---- 1) 진입 조건 -------------------------------------------------

    def _build_entry_section(self) -> QGroupBox:
        group = QGroupBox("진입 조건")
        form = QFormLayout(group)
        form.setContentsMargins(10, 16, 10, 10)
        form.setSpacing(8)

        self._lbl_volume_ratio = _ro_label("2.0 배")
        form.addRow("volume_ratio:", self._lbl_volume_ratio)

        self._lbl_min_breakout_pct = _ro_label("3.0 % (ADR-016)")
        form.addRow("min_breakout_pct:", self._lbl_min_breakout_pct)

        self._risk_buy_time_end = QLineEdit("12:00")
        self._risk_buy_time_end.setMaxLength(5)
        self._risk_buy_time_end.setFixedWidth(80)
        self._risk_buy_time_end.setToolTip("매수 차단 시각 (HH:MM)")
        form.addRow("buy_time_end:", self._risk_buy_time_end)

        self._lbl_adx_min = _ro_label("20")
        form.addRow("adx_min:", self._lbl_adx_min)

        self._lbl_adx_length = _ro_label("14")
        form.addRow("adx_length:", self._lbl_adx_length)

        return group

    # ---- 2) 청산 조건 -------------------------------------------------

    def _build_exit_section(self) -> QGroupBox:
        group = QGroupBox("청산 조건")
        form = QFormLayout(group)
        form.setContentsMargins(10, 16, 10, 10)
        form.setSpacing(8)

        self._lbl_stop_loss_pct = _ro_label("-8.0 % (ADR-010 고정 손절)")
        form.addRow("stop_loss_pct:", self._lbl_stop_loss_pct)

        self._lbl_atr_trail_multiplier = _ro_label("1.0")
        form.addRow("atr_trail_multiplier:", self._lbl_atr_trail_multiplier)

        self._lbl_atr_trail_min_pct = _ro_label("2.0 %")
        form.addRow("atr_trail_min_pct:", self._lbl_atr_trail_min_pct)

        self._lbl_atr_trail_max_pct = _ro_label("10.0 %")
        form.addRow("atr_trail_max_pct:", self._lbl_atr_trail_max_pct)

        force_close_info = QLabel("15:10 강제 청산 (고정)")
        force_close_info.setStyleSheet("color: #6c7086; font-size: 11px;")
        form.addRow("force_close_time:", force_close_info)

        return group

    # ---- 3) 리스크 관리 -----------------------------------------------

    def _build_risk_section(self) -> QGroupBox:
        group = QGroupBox("리스크 관리")
        form = QFormLayout(group)
        form.setContentsMargins(10, 16, 10, 10)
        form.setSpacing(8)

        self._risk_max_positions = QSpinBox()
        self._risk_max_positions.setRange(1, 10)
        self._risk_max_positions.setValue(3)
        form.addRow("max_positions:", self._risk_max_positions)

        self._risk_max_daily_loss = QDoubleSpinBox()
        self._risk_max_daily_loss.setRange(0.0, 10.0)
        self._risk_max_daily_loss.setValue(1.5)
        self._risk_max_daily_loss.setSuffix(" %")
        self._risk_max_daily_loss.setDecimals(1)
        self._risk_max_daily_loss.setSingleStep(0.1)
        form.addRow("max_daily_loss_pct:", self._risk_max_daily_loss)

        self._risk_max_trades = QSpinBox()
        self._risk_max_trades.setRange(1, 20)
        self._risk_max_trades.setValue(2)
        form.addRow("max_trades_per_day:", self._risk_max_trades)

        self._risk_cooldown = QSpinBox()
        self._risk_cooldown.setRange(0, 999)
        self._risk_cooldown.setValue(120)
        self._risk_cooldown.setSuffix(" 분")
        form.addRow("cooldown_minutes:", self._risk_cooldown)

        return group

    # ---- 4) 자본 설정 -------------------------------------------------

    def _build_capital_section(self) -> QGroupBox:
        group = QGroupBox("자본 설정")
        form = QFormLayout(group)
        form.setContentsMargins(10, 16, 10, 10)
        form.setSpacing(8)

        self._lbl_initial_capital = _ro_label("3,000,000 원 (ADR-013)")
        form.addRow("initial_capital:", self._lbl_initial_capital)

        self._lbl_entry_1st_ratio = _ro_label("1.00 (전량 매수)")
        form.addRow("entry_1st_ratio:", self._lbl_entry_1st_ratio)

        return group

    # ---- 5) 시장 필터 -------------------------------------------------

    def _build_market_section(self) -> QGroupBox:
        group = QGroupBox("시장 필터")
        form = QFormLayout(group)
        form.setContentsMargins(10, 16, 10, 10)
        form.setSpacing(8)

        self._risk_market_filter = QCheckBox("시장 필터 활성화")
        self._risk_market_filter.setChecked(True)
        self._risk_market_filter.setToolTip(
            "활성 시: KOSPI 종목은 KOSPI MA5, KOSDAQ 종목은 KOSDAQ MA5 이상일 때만 매수"
        )
        form.addRow("market_filter_enabled:", self._risk_market_filter)

        self._lbl_market_ma_length = _ro_label("5 (MA5)")
        form.addRow("market_ma_length:", self._lbl_market_ma_length)

        return group

    # ---- 6) 알림 정책 -------------------------------------------------

    def _build_notifications_section(self) -> QGroupBox:
        group = QGroupBox("알림 정책 (ADR-008 / 012 / 014)")
        form = QFormLayout(group)
        form.setContentsMargins(10, 16, 10, 10)
        form.setSpacing(6)
        self._notif_checkboxes: dict = {}
        for key, label in self._NOTIFICATION_FIELDS:
            cb = QCheckBox(label)
            cb.setChecked(True)
            self._notif_checkboxes[key] = cb
            form.addRow(cb)
        return group

    # ---- 7) 유니버스 --------------------------------------------------

    def _build_universe_editor(self) -> QGroupBox:
        group = QGroupBox("종목 유니버스")
        vbox = QVBoxLayout(group)
        vbox.setContentsMargins(10, 16, 10, 10)
        vbox.setSpacing(6)

        self._universe_count_label = QLabel("0종목")
        self._universe_count_label.setStyleSheet("font-size: 11px; color: #6c7086;")
        vbox.addWidget(self._universe_count_label)

        self._lbl_universe_refresh_status = QLabel("주간 자동 갱신: 확인 중…")
        self._lbl_universe_refresh_status.setStyleSheet(
            "font-size: 11px; color: #f9e2af;"
        )
        vbox.addWidget(self._lbl_universe_refresh_status)
        self._update_universe_refresh_status()

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

    def _update_universe_refresh_status(self) -> None:
        """engine_worker._safe_refresh_universe 소스에서 early return 여부 감지."""
        try:
            src = Path("gui/workers/engine_worker.py").read_text(encoding="utf-8")
            # d6426b3 이후 early return으로 임시 비활성 상태
            disabled = "주간 자동 갱신 건너뜀 — 추세 필터 구현/검증 대기" in src
        except Exception:
            disabled = False
        if self._lbl_universe_refresh_status is None:
            return
        if disabled:
            self._lbl_universe_refresh_status.setText(
                "주간 자동 갱신: 비활성 (PF 검증 대기)"
            )
            self._lbl_universe_refresh_status.setStyleSheet(
                "font-size: 11px; color: #f38ba8;"
            )
        else:
            self._lbl_universe_refresh_status.setText(
                "주간 자동 갱신: 활성 (월 07:30, ADR-012)"
            )
            self._lbl_universe_refresh_status.setStyleSheet(
                "font-size: 11px; color: #a6e3a1;"
            )

    def _build_save_button(self) -> QHBoxLayout:
        layout = QHBoxLayout()

        note = QLabel(
            "※ 회색 필드는 코드 변경 필요 (재백테스트 검증 후 반영). "
            "편집 가능 필드만 config.yaml에 저장됩니다."
        )
        note.setStyleSheet("color: #6c7086; font-size: 10px;")
        note.setWordWrap(True)
        layout.addWidget(note, stretch=1)

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
            self._universe_count_label.setText(
                f"{self._universe_table.rowCount()}종목"
            )

    def _on_remove_ticker(self) -> None:
        rows = sorted(
            set(idx.row() for idx in self._universe_table.selectedIndexes()),
            reverse=True,
        )
        for row in rows:
            self._universe_table.removeRow(row)
        self._universe_count_label.setText(
            f"{self._universe_table.rowCount()}종목"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_config(self, config: dict) -> None:
        """config.yaml 값을 UI에 반영.

        읽기 전용 라벨은 표시만, 편집 가능 필드는 초기값 주입.
        """
        strategy_cfg = config.get("strategy", {}) or {}
        trading_cfg = config.get("trading", {}) or {}
        momentum = strategy_cfg.get("momentum", {}) or {}

        # 진입 조건 (읽기 전용)
        if self._lbl_volume_ratio is not None:
            self._lbl_volume_ratio.setText(
                f"{float(momentum.get('volume_ratio', 2.0)):.1f} 배"
            )
        if self._lbl_min_breakout_pct is not None:
            self._lbl_min_breakout_pct.setText(
                f"{float(momentum.get('min_breakout_pct', 0.03)) * 100:.1f} % (ADR-016)"
            )
        if self._lbl_adx_min is not None:
            self._lbl_adx_min.setText(str(int(momentum.get("adx_min", 20))))
        if self._lbl_adx_length is not None:
            self._lbl_adx_length.setText(str(int(momentum.get("adx_length", 14))))

        # 진입 조건 (편집 가능)
        self._risk_buy_time_end.setText(str(momentum.get("buy_time_end", "12:00")))

        # 청산 조건 (읽기 전용)
        if self._lbl_stop_loss_pct is not None:
            self._lbl_stop_loss_pct.setText(
                f"{float(momentum.get('stop_loss_pct', -0.08)) * 100:.1f} % (ADR-010 고정 손절)"
            )
        if self._lbl_atr_trail_multiplier is not None:
            self._lbl_atr_trail_multiplier.setText(
                f"{float(momentum.get('atr_trail_multiplier', 1.0)):.1f}"
            )
        if self._lbl_atr_trail_min_pct is not None:
            self._lbl_atr_trail_min_pct.setText(
                f"{float(momentum.get('atr_trail_min_pct', 0.02)) * 100:.1f} %"
            )
        if self._lbl_atr_trail_max_pct is not None:
            self._lbl_atr_trail_max_pct.setText(
                f"{float(momentum.get('atr_trail_max_pct', 0.10)) * 100:.1f} %"
            )

        # 리스크 관리 (편집 가능)
        if "max_positions" in trading_cfg:
            self._risk_max_positions.setValue(int(trading_cfg["max_positions"]))
        if "daily_max_loss_pct" in trading_cfg:
            self._risk_max_daily_loss.setValue(
                abs(float(trading_cfg["daily_max_loss_pct"])) * 100
            )
        if "max_trades_per_day" in trading_cfg:
            self._risk_max_trades.setValue(int(trading_cfg["max_trades_per_day"]))
        if "cooldown_minutes" in trading_cfg:
            self._risk_cooldown.setValue(int(trading_cfg["cooldown_minutes"]))

        # 자본 설정 (읽기 전용)
        if self._lbl_initial_capital is not None:
            cap = int(trading_cfg.get("initial_capital", 3_000_000))
            self._lbl_initial_capital.setText(f"{cap:,} 원 (ADR-013)")
        if self._lbl_entry_1st_ratio is not None:
            ratio = float(trading_cfg.get("entry_1st_ratio", 1.0))
            self._lbl_entry_1st_ratio.setText(
                f"{ratio:.2f} ({'전량 매수' if ratio >= 1.0 else '분할 매수'})"
            )

        # 시장 필터
        if "market_filter_enabled" in trading_cfg:
            self._risk_market_filter.setChecked(bool(trading_cfg["market_filter_enabled"]))
        if self._lbl_market_ma_length is not None:
            ma = int(trading_cfg.get("market_ma_length", 5))
            self._lbl_market_ma_length.setText(f"{ma} (MA{ma})")

        # 알림 정책
        notif_cfg = config.get("notifications", {}) or {}
        for key, _ in self._NOTIFICATION_FIELDS:
            if key in notif_cfg:
                self._notif_checkboxes[key].setChecked(bool(notif_cfg[key]))

        # 주간 갱신 상태 재검사
        self._update_universe_refresh_status()

    def get_config(self) -> dict:
        """편집 가능 필드만 반환 (main_window가 기존 config에 merge).

        읽기 전용 필드는 포함하지 않음 → config.yaml의 기존 값이 보존됨.
        """
        return {
            "strategy": {
                "momentum": {
                    "buy_time_end": self._risk_buy_time_end.text().strip() or "12:00",
                },
            },
            "trading": {
                "max_positions": self._risk_max_positions.value(),
                "daily_max_loss_pct": -self._risk_max_daily_loss.value() / 100,
                "max_trades_per_day": self._risk_max_trades.value(),
                "cooldown_minutes": self._risk_cooldown.value(),
                "market_filter_enabled": self._risk_market_filter.isChecked(),
            },
            "notifications": {
                key: self._notif_checkboxes[key].isChecked()
                for key, _ in self._NOTIFICATION_FIELDS
            },
        }

    def load_universe(self, stocks: list[dict]) -> None:
        """유니버스 테이블 갱신."""
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
        n = self._universe_table.rowCount()
        self._universe_count_label.setText(
            f"{n}종목 (ATR ≥ 6%, SMA — generate_universe.py)"
        )

    def get_universe(self) -> list[str]:
        """현재 유니버스 티커 목록."""
        tickers = []
        for i in range(self._universe_table.rowCount()):
            item = self._universe_table.item(i, 0)
            if item and item.text().strip():
                tickers.append(item.text().strip())
        return tickers
