"""시장 상태 패널 — KOSPI/KOSDAQ 상태, 포지션 슬롯, VI, 다음 일정."""
from __future__ import annotations

from datetime import datetime, time as _time

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from gui.components.progress_ring import ProgressRing
from gui.design_tokens import Colors, Border, Spacing
from gui.widgets.card import Card

_SCHEDULES = [
    (_time(9, 5),   "매수 시작"),
    (_time(12, 0),  "매수 차단"),
    (_time(15, 10), "강제 청산"),
    (_time(15, 30), "보고서"),
]


class MarketStatusPanel(QWidget):
    """대시보드 상단 — 시장 상태 요약 바."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._kospi_strong = False
        self._kosdaq_strong = False
        self._build_ui()

        self._sched_timer = QTimer(self)
        self._sched_timer.timeout.connect(self._refresh_schedule)
        self._sched_timer.start(30_000)
        self._refresh_schedule()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.setFixedHeight(56)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.gap_sm)

        # KOSPI 카드
        self._kospi_card = self._market_card("KOSPI")
        layout.addWidget(self._kospi_card)

        # KOSDAQ 카드
        self._kosdaq_card = self._market_card("KOSDAQ")
        layout.addWidget(self._kosdaq_card)

        sep = self._vsep()
        layout.addWidget(sep)

        # 포지션 슬롯
        slot_card = Card()
        slot_row = QHBoxLayout()
        slot_row.setSpacing(Spacing.gap_md)
        self._slot_ring = ProgressRing(size=36)
        slot_info = QVBoxLayout()
        slot_info.setSpacing(0)
        slot_title = QLabel("포지션 슬롯")
        slot_title.setStyleSheet(f"color: {Colors.text_muted}; font-size: 10px;")
        self._slot_lbl = QLabel("0 / 3")
        self._slot_lbl.setStyleSheet(f"color: {Colors.text_primary}; font-size: 13px; font-weight: bold;")
        slot_info.addWidget(slot_title)
        slot_info.addWidget(self._slot_lbl)
        slot_row.addWidget(self._slot_ring)
        slot_row.addLayout(slot_info)
        slot_row.addStretch()
        slot_card.addLayout(slot_row)
        layout.addWidget(slot_card)

        sep2 = self._vsep()
        layout.addWidget(sep2)

        # VI 활성 수
        vi_card = Card()
        vi_layout = QVBoxLayout()
        vi_layout.setSpacing(0)
        vi_title = QLabel("VI 활성")
        vi_title.setStyleSheet(f"color: {Colors.text_muted}; font-size: 10px;")
        self._vi_lbl = QLabel("0종목")
        self._vi_lbl.setStyleSheet(f"color: {Colors.text_primary}; font-size: 13px; font-weight: bold;")
        vi_layout.addWidget(vi_title)
        vi_layout.addWidget(self._vi_lbl)
        vi_card.addLayout(vi_layout)
        layout.addWidget(vi_card)

        sep3 = self._vsep()
        layout.addWidget(sep3)

        # 다음 일정
        sched_card = Card()
        sched_layout = QVBoxLayout()
        sched_layout.setSpacing(0)
        sched_title = QLabel("다음 일정")
        sched_title.setStyleSheet(f"color: {Colors.text_muted}; font-size: 10px;")
        self._sched_lbl = QLabel("—")
        self._sched_lbl.setStyleSheet(
            f"color: {Colors.accent_yellow}; font-size: 12px; font-weight: bold;"
        )
        sched_layout.addWidget(sched_title)
        sched_layout.addWidget(self._sched_lbl)
        sched_card.addLayout(sched_layout)
        layout.addWidget(sched_card, stretch=1)

    def _market_card(self, name: str) -> Card:
        card = Card()
        vbox = QVBoxLayout()
        vbox.setSpacing(0)
        title_lbl = QLabel(name)
        title_lbl.setStyleSheet(f"color: {Colors.text_muted}; font-size: 10px;")
        status_lbl = QLabel("약세 ▼")
        status_lbl.setStyleSheet(f"color: {Colors.accent_red}; font-size: 13px; font-weight: bold;")
        vbox.addWidget(title_lbl)
        vbox.addWidget(status_lbl)
        card.addLayout(vbox)
        setattr(card, "_status_lbl", status_lbl)
        return card

    def _vsep(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {Colors.surface_border};")
        sep.setFixedWidth(1)
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
        self._kospi_strong = kospi_strong
        self._kosdaq_strong = kosdaq_strong
        self._apply_market_card(self._kospi_card, kospi_strong)
        self._apply_market_card(self._kosdaq_card, kosdaq_strong)

    def _apply_market_card(self, card: Card, strong: bool):
        lbl = getattr(card, "_status_lbl", None)
        if lbl is None:
            return
        if strong:
            lbl.setText("강세 ▲")
            lbl.setStyleSheet(f"color: {Colors.accent_green}; font-size: 13px; font-weight: bold;")
        else:
            lbl.setText("약세 ▼")
            lbl.setStyleSheet(f"color: {Colors.accent_red}; font-size: 13px; font-weight: bold;")

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
        self._vi_lbl.setStyleSheet(f"color: {color}; font-size: 13px; font-weight: bold;")
