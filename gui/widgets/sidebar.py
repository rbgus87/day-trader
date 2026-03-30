"""사이드바 위젯 — 왼쪽 컨트롤 패널 (220px 고정 너비).

레이아웃 (위→아래):
    1. 앱 타이틀 (DayTrader + 버전)
    2. 모드 선택기 (PAPER / LIVE 토글)
    3. 엔진 상태 표시
    4. 제어 버튼 (시작 / 정지 / 긴급 정지)
    5. 수동 실행 버튼 그리드 (2×2)
    6. 스페이서
    7. 연결 상태 (REST / WS)
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class Sidebar(QFrame):
    """좌측 사이드바 컨트롤 패널.

    Signals:
        start_clicked: 시작 버튼 클릭.
        stop_clicked: 정지 버튼 클릭.
        halt_clicked: 긴급 정지 버튼 클릭.
        screening_clicked: 스크리닝 버튼 클릭.
        force_close_clicked: 강제청산 버튼 클릭.
        report_clicked: 리포트 버튼 클릭.
        reconnect_clicked: WS 재연결 버튼 클릭.
        mode_changed: 모드 변경 시 "paper" 또는 "live" 전달.
    """

    start_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()
    halt_clicked = pyqtSignal()
    screening_clicked = pyqtSignal()
    force_close_clicked = pyqtSignal()
    report_clicked = pyqtSignal()
    reconnect_clicked = pyqtSignal()
    mode_changed = pyqtSignal(str)  # "paper" or "live"
    strategy_changed = pyqtSignal(str)  # 전략명 ("" = auto)

    # ── 색상 상수 ────────────────────────────────────────────────────────────
    _COLOR_MAUVE = "#cba6f7"
    _COLOR_OVERLAY0 = "#6c7086"
    _COLOR_SURFACE0 = "#313244"
    _COLOR_GREEN = "#a6e3a1"
    _COLOR_RED = "#f38ba8"
    _COLOR_YELLOW = "#f9e2af"
    _COLOR_BASE = "#1e1e2e"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(220)

        self._mode = "paper"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self._build_title(layout)
        layout.addWidget(self._hline())
        self._build_mode_selector(layout)
        layout.addWidget(self._hline())
        self._build_engine_status(layout)
        layout.addWidget(self._hline())
        self._build_control_buttons(layout)
        layout.addWidget(self._hline())
        self._build_manual_actions(layout)
        layout.addStretch()
        layout.addWidget(self._hline())
        self._build_connection_status(layout)

    # ── 빌더 메서드 ──────────────────────────────────────────────────────────

    def _build_title(self, parent_layout: QVBoxLayout) -> None:
        """앱 타이틀 섹션."""
        title_label = QLabel("DayTrader")
        title_label.setStyleSheet(
            f"color: {self._COLOR_MAUVE}; font-size: 16px; font-weight: bold;"
        )

        version_label = QLabel("v0.1.0")
        version_label.setStyleSheet(
            f"color: {self._COLOR_OVERLAY0}; font-size: 11px;"
        )

        parent_layout.addWidget(title_label)
        parent_layout.addWidget(version_label)

    def _build_mode_selector(self, parent_layout: QVBoxLayout) -> None:
        """PAPER / LIVE 모드 선택기 섹션."""
        parent_layout.addWidget(self._section_label("TRADING MODE"))

        # 토글 버튼 행
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        self._paper_btn = QPushButton("PAPER")
        self._paper_btn.setCheckable(True)
        self._paper_btn.setChecked(True)

        self._live_btn = QPushButton("LIVE")
        self._live_btn.setCheckable(True)
        self._live_btn.setChecked(False)

        self._mode_btn_group = QButtonGroup(self)
        self._mode_btn_group.setExclusive(True)
        self._mode_btn_group.addButton(self._paper_btn)
        self._mode_btn_group.addButton(self._live_btn)

        btn_row.addWidget(self._paper_btn)
        btn_row.addWidget(self._live_btn)
        parent_layout.addLayout(btn_row)

        # 모드 배지
        self._mode_badge = QLabel("모의투자")
        self._mode_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._mode_badge.setFixedHeight(22)
        self._apply_mode_badge_style(paper=True)
        parent_layout.addWidget(self._mode_badge)

        # 시그널 연결
        self._paper_btn.clicked.connect(self._on_paper_clicked)
        self._live_btn.clicked.connect(self._on_live_clicked)

        self._apply_mode_btn_styles()

    def _build_engine_status(self, parent_layout: QVBoxLayout) -> None:
        """엔진 상태 섹션."""
        parent_layout.addWidget(self._section_label("ENGINE"))

        # 상태 행 (● + 텍스트)
        status_row = QHBoxLayout()
        status_row.setSpacing(6)

        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(
            f"color: {self._COLOR_OVERLAY0}; font-size: 10px;"
        )
        self._status_dot.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )

        self._status_label = QLabel("대기 중")
        self._status_label.setStyleSheet(
            f"color: {self._COLOR_OVERLAY0}; font-size: 12px;"
        )

        status_row.addWidget(self._status_dot)
        status_row.addWidget(self._status_label)
        status_row.addStretch()
        parent_layout.addLayout(status_row)

        # 전략 선택
        strategy_row = QHBoxLayout()
        strategy_row.setSpacing(6)
        strat_lbl = QLabel("전략:")
        strat_lbl.setStyleSheet(
            f"color: {self._COLOR_OVERLAY0}; font-size: 10px;"
        )
        strat_lbl.setFixedWidth(32)
        strategy_row.addWidget(strat_lbl)

        self._strategy_combo = QComboBox()
        self._strategy_combo.addItems([
            "Auto", "Momentum", "Pullback", "Flow",
            "Gap", "OpenBreak", "BigCandle",
        ])
        self._strategy_combo.setFixedHeight(24)
        self._strategy_combo.currentTextChanged.connect(self._on_strategy_changed)
        strategy_row.addWidget(self._strategy_combo)
        parent_layout.addLayout(strategy_row)

        # 전략 / 타겟 표시
        self._strategy_label = QLabel("Strategy: —")
        self._strategy_label.setStyleSheet(
            f"color: {self._COLOR_OVERLAY0}; font-size: 10px;"
        )
        parent_layout.addWidget(self._strategy_label)

        self._target_label = QLabel("Target: —")
        self._target_label.setStyleSheet(
            f"color: {self._COLOR_OVERLAY0}; font-size: 10px;"
        )
        parent_layout.addWidget(self._target_label)

        # 일일 PnL + 거래 정보
        self._pnl_label = QLabel("PnL: —")
        self._pnl_label.setStyleSheet(
            f"color: {self._COLOR_OVERLAY0}; font-size: 11px; font-weight: bold;"
        )
        parent_layout.addWidget(self._pnl_label)

        self._trades_label = QLabel("거래: 0 / 3")
        self._trades_label.setStyleSheet(
            f"color: {self._COLOR_OVERLAY0}; font-size: 10px;"
        )
        parent_layout.addWidget(self._trades_label)

    def _build_control_buttons(self, parent_layout: QVBoxLayout) -> None:
        """시작 / 정지 / 긴급 정지 버튼 섹션."""
        self._start_btn = QPushButton("▶  시작")
        self._start_btn.setObjectName("startBtn")

        self._stop_btn = QPushButton("■  정지")
        self._stop_btn.setObjectName("stopBtn")
        self._stop_btn.setEnabled(False)

        self._halt_btn = QPushButton("⚠  Halt 긴급")
        self._halt_btn.setObjectName("haltBtn")
        self._halt_btn.setEnabled(False)

        for btn in (self._start_btn, self._stop_btn, self._halt_btn):
            btn.setFixedHeight(32)
            parent_layout.addWidget(btn)

        self._start_btn.clicked.connect(self.start_clicked)
        self._stop_btn.clicked.connect(self.stop_clicked)
        self._halt_btn.clicked.connect(self.halt_clicked)

    def _build_manual_actions(self, parent_layout: QVBoxLayout) -> None:
        """수동 실행 2×2 그리드 섹션."""
        parent_layout.addWidget(self._section_label("수동 실행"))

        grid = QGridLayout()
        grid.setSpacing(6)

        actions = [
            ("스크리닝", "후보 종목 수동 스크리닝", self.screening_clicked),
            ("강제청산", "전체 포지션 즉시 청산", self.force_close_clicked),
            ("리포트", "일일 매매 리포트 발송", self.report_clicked),
            ("WS 재연결", "WebSocket 연결 재시도", self.reconnect_clicked),
        ]

        self._manual_btns = []
        for idx, (label, tooltip, signal) in enumerate(actions):
            btn = QPushButton(label)
            btn.setObjectName("manualBtn")
            btn.setToolTip(tooltip)
            btn.setEnabled(False)
            btn.setFixedHeight(28)
            btn.clicked.connect(signal)
            self._manual_btns.append(btn)
            row, col = divmod(idx, 2)
            grid.addWidget(btn, row, col)

        parent_layout.addLayout(grid)

    def _build_connection_status(self, parent_layout: QVBoxLayout) -> None:
        """REST / WS 연결 상태 섹션."""
        # REST 행
        rest_row = QHBoxLayout()
        rest_row.setSpacing(6)

        self._rest_dot = QLabel("●")
        self._rest_dot.setStyleSheet(
            f"color: {self._COLOR_RED}; font-size: 6px;"
        )
        self._rest_dot.setFixedWidth(10)

        self._rest_label = QLabel("REST 미연결")
        self._rest_label.setStyleSheet(
            f"color: {self._COLOR_OVERLAY0}; font-size: 11px;"
        )

        rest_row.addWidget(self._rest_dot)
        rest_row.addWidget(self._rest_label)
        rest_row.addStretch()
        parent_layout.addLayout(rest_row)

        # WS 행
        ws_row = QHBoxLayout()
        ws_row.setSpacing(6)

        self._ws_dot = QLabel("●")
        self._ws_dot.setStyleSheet(
            f"color: {self._COLOR_RED}; font-size: 6px;"
        )
        self._ws_dot.setFixedWidth(10)

        self._ws_label = QLabel("WS 미연결")
        self._ws_label.setStyleSheet(
            f"color: {self._COLOR_OVERLAY0}; font-size: 11px;"
        )

        ws_row.addWidget(self._ws_dot)
        ws_row.addWidget(self._ws_label)
        ws_row.addStretch()
        parent_layout.addLayout(ws_row)

    # ── 헬퍼 메서드 ──────────────────────────────────────────────────────────

    def _section_label(self, text: str) -> QLabel:
        """섹션 구분 레이블."""
        label = QLabel(text)
        label.setStyleSheet(
            f"color: {self._COLOR_OVERLAY0}; "
            "font-size: 10px; "
            "font-weight: bold; "
            "letter-spacing: 2px; "
            f"border-bottom: 1px solid {self._COLOR_SURFACE0}; "
            "padding: 4px 0 0 0;"
        )
        return label

    def _hline(self) -> QFrame:
        """가로 구분선."""
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: {self._COLOR_SURFACE0};")
        line.setFixedHeight(1)
        return line

    def _apply_mode_btn_styles(self) -> None:
        """모드 버튼 선택 상태에 따라 스타일 적용."""
        if self._mode == "paper":
            self._paper_btn.setStyleSheet(
                f"color: {self._COLOR_GREEN}; font-weight: bold;"
            )
            self._live_btn.setStyleSheet(
                f"color: {self._COLOR_OVERLAY0}; font-weight: normal;"
            )
        else:
            self._paper_btn.setStyleSheet(
                f"color: {self._COLOR_OVERLAY0}; font-weight: normal;"
            )
            self._live_btn.setStyleSheet(
                f"color: {self._COLOR_RED}; font-weight: bold;"
            )

    def _apply_mode_badge_style(self, paper: bool) -> None:
        """모드 배지 스타일 적용."""
        if paper:
            self._mode_badge.setText("모의투자")
            self._mode_badge.setStyleSheet(
                f"background-color: {self._COLOR_GREEN}; "
                f"color: {self._COLOR_BASE}; "
                "font-size: 11px; font-weight: bold; "
                "border-radius: 3px; padding: 2px 6px;"
            )
        else:
            self._mode_badge.setText("실거래")
            self._mode_badge.setStyleSheet(
                f"background-color: {self._COLOR_RED}; "
                f"color: {self._COLOR_BASE}; "
                "font-size: 11px; font-weight: bold; "
                "border-radius: 3px; padding: 2px 6px;"
            )

    # ── 슬롯 (내부) ──────────────────────────────────────────────────────────

    def _on_strategy_changed(self, text: str) -> None:
        """전략 콤보 변경 → signal emit."""
        value = "" if text == "Auto" else text.lower()
        self.strategy_changed.emit(value)

    def _on_paper_clicked(self) -> None:
        self._mode = "paper"
        self._apply_mode_btn_styles()
        self._apply_mode_badge_style(paper=True)
        self.mode_changed.emit("paper")

    def _on_live_clicked(self) -> None:
        # MainWindow가 확인 다이얼로그 처리 후 필요 시 되돌림
        self._mode = "live"
        self._apply_mode_btn_styles()
        self._apply_mode_badge_style(paper=False)
        self.mode_changed.emit("live")

    # ── 공개 메서드 ──────────────────────────────────────────────────────────

    def get_mode(self) -> str:
        """현재 선택된 모드 반환 ('paper' or 'live')."""
        return self._mode

    def set_engine_running(self, running: bool) -> None:
        """엔진 상태에 따라 버튼 활성화/비활성화.

        Args:
            running: True → 엔진 실행 중 (시작 비활성, 정지/긴급/수동 활성, 모드 비활성)
                     False → 엔진 대기 중 (시작 활성, 정지/긴급/수동 비활성, 모드 활성)
        """
        self._start_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)
        self._halt_btn.setEnabled(running)

        for btn in self._manual_btns:
            btn.setEnabled(running)

        self._paper_btn.setEnabled(not running)
        self._live_btn.setEnabled(not running)

    def update_status(self, status: dict) -> None:
        """엔진 상태 dict를 UI에 반영.

        Args:
            status: {
                running (bool): 엔진 실행 여부,
                halted (bool): 매매 중단 여부,
                strategy (str): 현재 전략명,
                target (str): 종목 코드,
                target_name (str): 종목명,
            }
        """
        running = status.get("running", False)
        halted = status.get("halted", False)
        strategy = status.get("strategy", "")
        target = status.get("target", "")
        target_name = status.get("target_name", "")

        # 상태 dot + 텍스트
        if running and not halted:
            dot_color = self._COLOR_GREEN
            status_text = "실행 중"
        elif running and halted:
            dot_color = self._COLOR_YELLOW
            status_text = "매매 중단됨"
        else:
            dot_color = self._COLOR_OVERLAY0
            status_text = "대기 중"

        self._status_dot.setStyleSheet(
            f"color: {dot_color}; font-size: 10px;"
        )
        self._status_label.setText(status_text)
        self._status_label.setStyleSheet(
            f"color: {dot_color}; font-size: 12px;"
        )

        # 전략명
        strategy_text = f"Strategy: {strategy}" if strategy else "Strategy: —"
        self._strategy_label.setText(strategy_text)

        # 타겟 종목
        if target and target_name:
            target_text = f"Target: {target_name}({target})"
        elif target:
            target_text = f"Target: {target}"
        else:
            target_text = "Target: —"
        self._target_label.setText(target_text)

        # 일일 PnL
        pnl = status.get("daily_pnl", 0)
        pnl_pct = status.get("daily_pnl_pct", 0)
        pnl_sign = "+" if pnl >= 0 else ""
        pnl_color = self._COLOR_GREEN if pnl >= 0 else self._COLOR_RED
        self._pnl_label.setText(f"PnL: {pnl_sign}{pnl:,.0f} ({pnl_sign}{pnl_pct:.1f}%)")
        self._pnl_label.setStyleSheet(
            f"color: {pnl_color}; font-size: 11px; font-weight: bold;"
        )

        # 거래 횟수
        trades = status.get("trades_count", 0)
        max_t = status.get("max_trades", 3)
        wins = status.get("wins", 0)
        losses = status.get("losses", 0)
        self._trades_label.setText(f"거래: {trades}/{max_t} (W{wins} L{losses})")
        self._trades_label.setStyleSheet(
            f"color: {self._COLOR_OVERLAY0}; font-size: 10px;"
        )

    def update_connection(self, rest_ok: bool, ws_ok: bool) -> None:
        """연결 상태를 UI에 반영.

        Args:
            rest_ok: REST API 연결 정상 여부.
            ws_ok: WebSocket 연결 정상 여부.
        """
        if rest_ok:
            self._rest_dot.setStyleSheet(
                f"color: {self._COLOR_GREEN}; font-size: 6px;"
            )
            self._rest_label.setText("REST 연결됨")
            self._rest_label.setStyleSheet(
                f"color: {self._COLOR_GREEN}; font-size: 11px;"
            )
        else:
            self._rest_dot.setStyleSheet(
                f"color: {self._COLOR_RED}; font-size: 6px;"
            )
            self._rest_label.setText("REST 미연결")
            self._rest_label.setStyleSheet(
                f"color: {self._COLOR_OVERLAY0}; font-size: 11px;"
            )

        if ws_ok:
            self._ws_dot.setStyleSheet(
                f"color: {self._COLOR_GREEN}; font-size: 6px;"
            )
            self._ws_label.setText("WS 연결됨")
            self._ws_label.setStyleSheet(
                f"color: {self._COLOR_GREEN}; font-size: 11px;"
            )
        else:
            self._ws_dot.setStyleSheet(
                f"color: {self._COLOR_RED}; font-size: 6px;"
            )
            self._ws_label.setText("WS 미연결")
            self._ws_label.setStyleSheet(
                f"color: {self._COLOR_OVERLAY0}; font-size: 11px;"
            )
