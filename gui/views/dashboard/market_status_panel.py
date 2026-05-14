"""시장 상태 패널 — KOSPI/KOSDAQ 상태, 포지션 슬롯, VI, 다음 일정."""
from __future__ import annotations

from datetime import datetime, time as _time

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from gui.components.progress_ring import ProgressRing
from gui.design_tokens import Colors

_SCHEDULES = [
    (_time(9, 5),   "매수 시작"),
    (_time(12, 0),  "매수 차단"),
    (_time(15, 10), "강제 청산"),
    (_time(15, 30), "보고서"),
]


class MarketStatusPanel(QFrame):
    """대시보드 상단 — 시장 상태 요약 바 (단일 카드, 높이 48px)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

        self._sched_timer = QTimer(self)
        self._sched_timer.timeout.connect(self._refresh_schedule)
        self._sched_timer.start(30_000)
        self._refresh_schedule()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.setObjectName("msp")
        self.setStyleSheet(
            "QFrame#msp {"
            "  background-color: #2a2a3d;"
            "  border: 1px solid #313244;"
            "  border-radius: 8px;"
            "}"
            "QFrame#msp * { background: transparent; }"
        )
        self.setFixedHeight(48)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(8)

        # KOSPI
        kospi_w, self._kospi_lbl = self._info_item("KOSPI", "약세 ▼", Colors.accent_red)
        kospi_w.setFixedWidth(90)
        layout.addWidget(kospi_w)
        layout.addWidget(self._vsep())

        # KOSDAQ
        kosdaq_w, self._kosdaq_lbl = self._info_item("KOSDAQ", "약세 ▼", Colors.accent_red)
        kosdaq_w.setFixedWidth(90)
        layout.addWidget(kosdaq_w)
        layout.addWidget(self._vsep())

        # 포지션 슬롯 (링 + 텍스트)
        slot_w = QWidget()
        slot_w.setFixedWidth(110)
        sl = QHBoxLayout(slot_w)
        sl.setContentsMargins(8, 0, 8, 0)
        sl.setSpacing(6)
        sl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._slot_ring = ProgressRing(size=30)
        si = QVBoxLayout()
        si.setSpacing(1)
        si.setContentsMargins(0, 0, 0, 0)
        si.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        slot_title = self._mk_lbl("포지션", 9, Colors.text_muted)
        slot_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        si.addWidget(slot_title)
        self._slot_lbl = self._mk_lbl("0 / 3", 12, Colors.text_primary, bold=True)
        self._slot_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        si.addWidget(self._slot_lbl)
        sl.addWidget(self._slot_ring)
        sl.addLayout(si)
        layout.addWidget(slot_w)
        layout.addWidget(self._vsep())

        # VI 활성
        vi_w, self._vi_lbl = self._info_item("VI 활성", "0종목", Colors.text_primary)
        vi_w.setFixedWidth(80)
        layout.addWidget(vi_w)

        layout.addStretch()

        # 다음 일정 — 우측 정렬, 내용 크기에 맞춤
        sched_w = QWidget()
        sv = QVBoxLayout(sched_w)
        sv.setContentsMargins(8, 0, 4, 0)
        sv.setSpacing(1)
        sv.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        t = self._mk_lbl("다음 일정", 9, Colors.text_muted)
        t.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        sv.addWidget(t)
        self._sched_lbl = self._mk_lbl("—", 12, Colors.accent_yellow, bold=True)
        self._sched_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        sv.addWidget(self._sched_lbl)
        layout.addWidget(sched_w)

    # ── 헬퍼 ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _mk_lbl(text: str, size: int, color: str, bold: bool = False) -> QLabel:
        lbl = QLabel(text)
        style = f"color: {color}; font-size: {size}px;"
        if bold:
            style += " font-weight: bold;"
        lbl.setStyleSheet(style)
        return lbl

    def _info_item(self, title: str, initial: str, color: str) -> tuple[QWidget, QLabel]:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(8, 0, 8, 0)
        vbox.setSpacing(1)
        vbox.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        title_lbl = self._mk_lbl(title, 9, Colors.text_muted)
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(title_lbl)
        val = self._mk_lbl(initial, 12, color, bold=True)
        val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(val)
        return w, val

    def _vsep(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFixedWidth(1)
        sep.setStyleSheet("background: #313244;")
        return sep

    # ── 스케줄 갱신 ───────────────────────────────────────────────────────────

    def _refresh_schedule(self):
        now = datetime.now().time()
        for t, name in _SCHEDULES:
            if t > now:
                self._sched_lbl.setText(f"{t.strftime('%H:%M')} {name}")
                return
        self._sched_lbl.setText("일정 없음")

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def update_market_status(self, kospi_strong: bool, kosdaq_strong: bool):
        for lbl, strong in ((self._kospi_lbl, kospi_strong), (self._kosdaq_lbl, kosdaq_strong)):
            if strong:
                lbl.setText("강세 ▲")
                lbl.setStyleSheet(f"color: {Colors.accent_green}; font-size: 12px; font-weight: bold;")
            else:
                lbl.setText("약세 ▼")
                lbl.setStyleSheet(f"color: {Colors.accent_red}; font-size: 12px; font-weight: bold;")

    def update_slot(self, count: int, max_pos: int):
        self._slot_lbl.setText(f"{count} / {max_pos}")
        ratio = count / max_pos if max_pos > 0 else 0.0
        color = (Colors.accent_green if ratio < 0.5
                 else Colors.accent_yellow if ratio < 1.0
                 else Colors.accent_red)
        self._slot_ring.set_value(ratio, color)

    def update_vi_count(self, count: int):
        self._vi_lbl.setText(f"{count}종목")
        color = Colors.accent_yellow if count > 0 else Colors.text_primary
        self._vi_lbl.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: bold;")
