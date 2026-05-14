"""gui/views/nav_panel.py — 좌측 네비게이션 패널 (Phase 1 시험적 구현).

.. deprecated::
    Phase 1 이후 기존 sidebar.py + QTabWidget 레이아웃으로 복원됨.
    이 파일은 향후 참고용으로 유지되며 main_window.py에서 사용하지 않음.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QComboBox,
)
from PyQt6.QtCore import pyqtSignal

from gui.design_tokens import Colors, Border, Spacing

_NAV_ITEMS = [
    ("🏠", "대시보드", 0),
    ("🔍", "스크리너", 1),
    ("📊", "백테스트", 2),
    ("⚙", "전략 설정", 3),
    ("📋", "로그", 4),
]


class NavPanel(QFrame):
    """좌측 수직 네비게이션 패널."""

    # sidebar.py 와 동일한 시그널 (main_window 호환성 유지)
    start_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()
    halt_clicked = pyqtSignal()
    screening_clicked = pyqtSignal()
    force_close_clicked = pyqtSignal()
    report_clicked = pyqtSignal()
    reconnect_clicked = pyqtSignal()
    mode_changed = pyqtSignal(str)      # "paper" or "live"
    strategy_changed = pyqtSignal(str)  # 전략명 (Auto → "")
    # 추가: 페이지 전환
    page_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(200)
        self.setStyleSheet(
            f"NavPanel {{ background: {Colors.mantle}; "
            f"border-right: 1px solid {Colors.surface_border}; }}"
        )
        self._build_ui()

    # ── UI 구성 ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._build_nav())
        root.addWidget(self._make_sep())
        root.addWidget(self._build_engine_status())
        root.addWidget(self._make_sep())
        root.addWidget(self._build_ctrl_buttons())
        root.addWidget(self._make_sep())
        root.addWidget(self._build_manual_buttons())
        root.addStretch()
        root.addWidget(self._make_sep())
        root.addWidget(self._build_connection())

    def _build_header(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{ background: {Colors.crust}; "
            f"border-bottom: 1px solid {Colors.surface_border}; }}"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(Spacing.gap_sm)

        title = QLabel("DayTrader")
        title.setStyleSheet(
            f"color: {Colors.text_primary}; font-size: 15px; font-weight: bold; background: transparent;"
        )

        mode_row = QHBoxLayout()
        mode_row.setSpacing(Spacing.gap_xs)
        self._paper_btn = QPushButton("PAPER")
        self._live_btn = QPushButton("LIVE")
        for btn in (self._paper_btn, self._live_btn):
            btn.setCheckable(True)
            btn.setFixedHeight(26)
        self._paper_btn.setChecked(True)
        self._paper_btn.setStyleSheet(self._mode_style(True, "paper"))
        self._live_btn.setStyleSheet(self._mode_style(False))
        self._paper_btn.clicked.connect(self._on_paper_clicked)
        self._live_btn.clicked.connect(self._on_live_clicked)
        mode_row.addWidget(self._paper_btn)
        mode_row.addWidget(self._live_btn)

        layout.addWidget(title)
        layout.addLayout(mode_row)
        return frame

    def _build_nav(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet("QFrame { background: transparent; }")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 4)
        layout.setSpacing(2)

        self._nav_btns: list[QPushButton] = []
        for icon, text, idx in _NAV_ITEMS:
            btn = QPushButton(f"{icon}  {text}")
            btn.setCheckable(True)
            btn.setFixedHeight(36)
            btn.setStyleSheet(self._nav_style(False))
            btn.clicked.connect(lambda _, i=idx: self._on_nav_clicked(i))
            self._nav_btns.append(btn)
            layout.addWidget(btn)

        self._nav_btns[0].setChecked(True)
        self._nav_btns[0].setStyleSheet(self._nav_style(True))
        return frame

    def _build_engine_status(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet("QFrame { background: transparent; }")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(Spacing.gap_sm)

        status_row = QHBoxLayout()
        status_row.setSpacing(Spacing.gap_sm)
        self._dot_lbl = QLabel("●")
        self._dot_lbl.setStyleSheet(
            f"color: {Colors.surface_border}; background: transparent; font-size: 10px;"
        )
        self._status_lbl = QLabel("정지됨")
        self._status_lbl.setStyleSheet(
            f"color: {Colors.text_muted}; background: transparent; font-size: 11px;"
        )
        status_row.addWidget(self._dot_lbl)
        status_row.addWidget(self._status_lbl, 1)

        self._strategy_combo = QComboBox()
        self._strategy_combo.addItems(["Auto", "Momentum"])
        self._strategy_combo.setStyleSheet(
            f"QComboBox {{ background: {Colors.surface}; color: {Colors.text_primary}; "
            f"border: 1px solid {Colors.surface_border}; border-radius: {Border.radius_sm}px; "
            f"padding: 2px 6px; font-size: 11px; }}"
            f"QComboBox::drop-down {{ border: none; }}"
            f"QComboBox QAbstractItemView {{ background: {Colors.surface}; "
            f"color: {Colors.text_primary}; selection-background-color: {Colors.surface_elevated}; }}"
        )
        self._strategy_combo.currentTextChanged.connect(
            lambda t: self.strategy_changed.emit("" if t == "Auto" else t)
        )

        layout.addLayout(status_row)
        layout.addWidget(self._strategy_combo)
        return frame

    def _build_ctrl_buttons(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet("QFrame { background: transparent; }")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(Spacing.gap_sm)

        self._start_btn = QPushButton("▶  시작")
        self._stop_btn = QPushButton("■  정지")
        self._halt_btn = QPushButton("⚠  긴급 정지")

        for btn in (self._start_btn, self._stop_btn, self._halt_btn):
            btn.setFixedHeight(32)

        self._start_btn.setStyleSheet(self._start_style())
        self._stop_btn.setStyleSheet(self._stop_style())
        self._halt_btn.setStyleSheet(self._halt_style())

        self._stop_btn.setEnabled(False)
        self._halt_btn.setEnabled(False)

        self._start_btn.clicked.connect(self.start_clicked)
        self._stop_btn.clicked.connect(self.stop_clicked)
        self._halt_btn.clicked.connect(self.halt_clicked)

        layout.addWidget(self._start_btn)
        layout.addWidget(self._stop_btn)
        layout.addWidget(self._halt_btn)
        return frame

    def _build_manual_buttons(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet("QFrame { background: transparent; }")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(Spacing.gap_sm)

        row1 = QHBoxLayout()
        row1.setSpacing(Spacing.gap_sm)
        row2 = QHBoxLayout()
        row2.setSpacing(Spacing.gap_sm)

        scr = self._manual_btn("스크리닝")
        fc = self._manual_btn("강제청산")
        rep = self._manual_btn("리포트")
        ws = self._manual_btn("WS 재연결")

        scr.clicked.connect(self.screening_clicked)
        fc.clicked.connect(self.force_close_clicked)
        rep.clicked.connect(self.report_clicked)
        ws.clicked.connect(self.reconnect_clicked)

        row1.addWidget(scr)
        row1.addWidget(fc)
        row2.addWidget(rep)
        row2.addWidget(ws)

        layout.addLayout(row1)
        layout.addLayout(row2)
        return frame

    def _build_connection(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet("QFrame { background: transparent; }")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 10)
        layout.setSpacing(Spacing.gap_xs)

        self._rest_lbl = QLabel("○ REST 연결 대기")
        self._ws_lbl = QLabel("○ WS 연결 대기")
        for lbl in (self._rest_lbl, self._ws_lbl):
            lbl.setStyleSheet(
                f"color: {Colors.surface_border}; font-size: 10px; background: transparent;"
            )
        layout.addWidget(self._rest_lbl)
        layout.addWidget(self._ws_lbl)
        return frame

    # ── 스타일 헬퍼 ──────────────────────────────────────────────────────────

    def _mode_style(self, active: bool, mode: str = "") -> str:
        if active:
            bg = Colors.accent_red if mode == "live" else Colors.accent_blue
            fg = Colors.background
        else:
            bg, fg = Colors.surface, Colors.text_muted
        return (
            f"QPushButton {{ background: {bg}; color: {fg}; border: none; "
            f"border-radius: {Border.radius_sm}px; font-size: 11px; font-weight: bold; }}"
        )

    def _nav_style(self, active: bool) -> str:
        if active:
            bg = Colors.surface_elevated
            fg = Colors.text_primary
            left = f"border-left: 3px solid {Colors.accent_mauve};"
            weight = "font-weight: bold;"
        else:
            bg = "transparent"
            fg = Colors.text_muted
            left = "border-left: 3px solid transparent;"
            weight = ""
        return (
            f"QPushButton {{ background: {bg}; color: {fg}; border: none; {left} "
            f"border-radius: {Border.radius_sm}px; padding: 4px 12px; "
            f"font-size: 12px; text-align: left; {weight} }}"
            f"QPushButton:hover {{ background: {Colors.surface_elevated}; color: {Colors.text_primary}; }}"
        )

    def _ctrl_style(self, color: str) -> str:
        return (
            f"QPushButton {{ background: {color}22; color: {color}; "
            f"border: 1px solid {color}55; border-radius: {Border.radius_sm}px; "
            f"font-size: 11px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {color}44; }}"
            f"QPushButton:disabled {{ background: {Colors.surface}; color: {Colors.text_muted}; "
            f"border-color: {Colors.surface_border}; }}"
        )

    def _start_style(self) -> str:
        return (
            f"QPushButton {{ background: {Colors.surface}; color: {Colors.text_primary}; "
            f"border: 1px solid {Colors.surface_border}; border-radius: {Border.radius_sm}px; "
            f"font-size: 11px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {Colors.surface_elevated}; color: white; }}"
            f"QPushButton:disabled {{ color: {Colors.text_muted}; border-color: {Colors.surface_border}; "
            f"background: {Colors.surface}; }}"
        )

    def _stop_style(self) -> str:
        return (
            f"QPushButton {{ background: #e64553; color: white; border: none; "
            f"border-radius: {Border.radius_sm}px; font-size: 11px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: #c73a46; }}"
            f"QPushButton:disabled {{ background: {Colors.surface}; color: {Colors.text_muted}; "
            f"border: 1px solid {Colors.surface_border}; }}"
        )

    def _halt_style(self) -> str:
        return (
            f"QPushButton {{ background: #f38ba8; color: white; border: none; "
            f"border-radius: {Border.radius_sm}px; font-size: 11px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: #e0748e; }}"
            f"QPushButton:disabled {{ background: {Colors.surface}; color: {Colors.text_muted}; "
            f"border: 1px solid {Colors.surface_border}; }}"
        )

    def _manual_btn(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedHeight(28)
        btn.setStyleSheet(
            f"QPushButton {{ background: {Colors.surface}; color: {Colors.text_secondary}; "
            f"border: 1px solid {Colors.surface_border}; border-radius: {Border.radius_sm}px; "
            f"font-size: 10px; }}"
            f"QPushButton:hover {{ background: {Colors.surface_elevated}; color: {Colors.text_primary}; }}"
        )
        return btn

    def _make_sep(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"QFrame {{ color: {Colors.surface_border}; background: {Colors.surface_border}; }}")
        sep.setFixedHeight(1)
        return sep

    # ── 이벤트 핸들러 ────────────────────────────────────────────────────────

    def _on_paper_clicked(self) -> None:
        self._paper_btn.setChecked(True)
        self._paper_btn.setStyleSheet(self._mode_style(True, "paper"))
        self._live_btn.setChecked(False)
        self._live_btn.setStyleSheet(self._mode_style(False))
        self.mode_changed.emit("paper")

    def _on_live_clicked(self) -> None:
        self._live_btn.setChecked(True)
        self._live_btn.setStyleSheet(self._mode_style(True, "live"))
        self._paper_btn.setChecked(False)
        self._paper_btn.setStyleSheet(self._mode_style(False))
        self.mode_changed.emit("live")

    def _on_nav_clicked(self, idx: int) -> None:
        for i, btn in enumerate(self._nav_btns):
            active = (i == idx)
            btn.setChecked(active)
            btn.setStyleSheet(self._nav_style(active))
        self.page_changed.emit(idx)

    # ── 공개 API (sidebar.py 호환) ────────────────────────────────────────────

    def get_mode(self) -> str:
        return "live" if self._live_btn.isChecked() else "paper"

    def revert_to_paper(self) -> None:
        self._on_paper_clicked()

    def get_strategy(self) -> str:
        return self._strategy_combo.currentText()

    def set_strategy(self, force: str) -> None:
        for i in range(self._strategy_combo.count()):
            if self._strategy_combo.itemText(i).lower() == force.lower():
                self._strategy_combo.setCurrentIndex(i)
                return

    def set_engine_running(self, running: bool) -> None:
        self._start_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)
        self._halt_btn.setEnabled(running)
        if running:
            c = Colors.accent_green
            self._dot_lbl.setStyleSheet(f"color: {c}; background: transparent; font-size: 10px;")
            self._status_lbl.setText("실행 중")
            self._status_lbl.setStyleSheet(f"color: {c}; background: transparent; font-size: 11px;")
        else:
            c = Colors.surface_border
            self._dot_lbl.setStyleSheet(f"color: {c}; background: transparent; font-size: 10px;")
            self._status_lbl.setText("정지됨")
            self._status_lbl.setStyleSheet(
                f"color: {Colors.text_muted}; background: transparent; font-size: 11px;"
            )

    def update_status(self, status: dict) -> None:
        strategy = status.get("strategy") or "—"
        halted = status.get("halted", False)
        running = status.get("running", False)
        if halted:
            self._status_lbl.setText("긴급 정지")
            self._status_lbl.setStyleSheet(
                f"color: {Colors.accent_yellow}; background: transparent; font-size: 11px;"
            )
        elif running:
            self._status_lbl.setText(f"실행 중 [{strategy}]")
            self._status_lbl.setStyleSheet(
                f"color: {Colors.accent_green}; background: transparent; font-size: 11px;"
            )

    def update_connection(self, rest_ok: bool, ws_ok: bool) -> None:
        rest_c = Colors.accent_green if rest_ok else Colors.surface_border
        ws_c = Colors.accent_green if ws_ok else Colors.surface_border
        self._rest_lbl.setText(f"{'●' if rest_ok else '○'} REST {'연결됨' if rest_ok else '대기'}")
        self._rest_lbl.setStyleSheet(f"color: {rest_c}; font-size: 10px; background: transparent;")
        self._ws_lbl.setText(f"{'●' if ws_ok else '○'} WS {'연결됨' if ws_ok else '대기'}")
        self._ws_lbl.setStyleSheet(f"color: {ws_c}; font-size: 10px; background: transparent;")
