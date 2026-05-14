"""호가창 미니 위젯 — 5단계 매수/매도 바 차트. 0D 미수신 시 placeholder."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QSizePolicy, QVBoxLayout, QWidget,
)

from gui.design_tokens import Colors, Border
from gui.widgets.card import Card


class OrderbookWidget(QWidget):
    """호가 데이터 시각화 (0D 확인 전 — placeholder 상태)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._has_data = False
        self._ticker = ""
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._card = Card(title="호가 (—)")

        # OBI 행
        obi_row = QHBoxLayout()
        obi_lbl = QLabel("OBI")
        obi_lbl.setStyleSheet(f"color: {Colors.text_muted}; font-size: 10px;")
        self._obi_val = QLabel("—")
        self._obi_val.setStyleSheet(f"color: {Colors.text_primary}; font-size: 11px; font-weight: bold;")
        obi_row.addWidget(obi_lbl)
        obi_row.addWidget(self._obi_val)
        obi_row.addStretch()
        self._card.addLayout(obi_row)

        # 호가 바 영역 (ask 5 + bid 5)
        self._bars_widget = QWidget()
        bars_layout = QVBoxLayout(self._bars_widget)
        bars_layout.setSpacing(1)
        bars_layout.setContentsMargins(0, 2, 0, 2)

        self._ask_bars: list[_ObBar] = []
        self._bid_bars: list[_ObBar] = []

        # ask (역순: 5→1, 5가 가장 먼 가격)
        for _ in range(5):
            bar = _ObBar("ask")
            self._ask_bars.append(bar)
            bars_layout.insertWidget(0, bar)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {Colors.surface_border};")
        sep.setFixedHeight(1)
        bars_layout.addWidget(sep)

        # bid (순서: 1→5, 1이 가장 가까운 가격)
        for _ in range(5):
            bar = _ObBar("bid")
            self._bid_bars.append(bar)
            bars_layout.addWidget(bar)

        self._card.addWidget(self._bars_widget, stretch=1)

        # 미수신 안내 (기본 표시)
        self._no_data_lbl = QLabel(
            "호가 데이터 없음\n(0D 구독 확인 전 — obi_filter_enabled: false)"
        )
        self._no_data_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._no_data_lbl.setStyleSheet(
            f"color: {Colors.text_muted}; font-size: 10px; padding: 8px;"
        )
        self._card.addWidget(self._no_data_lbl)

        self._bars_widget.setVisible(False)
        self._no_data_lbl.setVisible(True)

        layout.addWidget(self._card)

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def update_orderbook(
        self,
        ticker: str,
        asks: list[tuple[int, int]],
        bids: list[tuple[int, int]],
        obi: float,
    ):
        """호가 데이터 수신 시 업데이트 (0D 활성화 이후 사용)."""
        self._ticker = ticker
        self._has_data = True
        self._card.setTitle(f"호가 ({ticker})")
        self._no_data_lbl.setVisible(False)
        self._bars_widget.setVisible(True)

        obi_color = Colors.accent_blue if obi >= 0 else Colors.accent_red
        self._obi_val.setText(f"{obi:+.3f}")
        self._obi_val.setStyleSheet(f"color: {obi_color}; font-size: 11px; font-weight: bold;")

        all_qty = sum(q for _, q in asks + bids) or 1

        # ask: asks[0] = 1호가(최근), asks[4] = 5호가
        for i, bar in enumerate(self._ask_bars):
            if i < len(asks):
                p, q = asks[i]
                bar.set_data(p, q, q / all_qty)
            else:
                bar.set_empty()

        # bid: bids[0] = 1호가(최근), bids[4] = 5호가
        for i, bar in enumerate(self._bid_bars):
            if i < len(bids):
                p, q = bids[i]
                bar.set_data(p, q, q / all_qty)
            else:
                bar.set_empty()

    def set_no_data(self, ticker: str = ""):
        """데이터 없음 상태로 전환."""
        self._has_data = False
        self._card.setTitle(f"호가 ({ticker})" if ticker else "호가 (—)")
        self._bars_widget.setVisible(False)
        self._no_data_lbl.setVisible(True)


class _ObBar(QWidget):
    """단일 호가 행: 가격 | 바 | 수량."""

    def __init__(self, side: str, parent=None):
        super().__init__(parent)
        self._side = side
        self.setFixedHeight(18)
        self._build()

    def _build(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        price_color = Colors.accent_red if self._side == "ask" else Colors.accent_green
        self._price_lbl = QLabel("—")
        self._price_lbl.setFixedWidth(68)
        self._price_lbl.setStyleSheet(
            f"color: {price_color}; font-size: 9px;"
        )
        self._price_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self._bar = QProgressBar()
        self._bar.setRange(0, 1000)
        self._bar.setValue(0)
        self._bar.setFixedHeight(10)
        self._bar.setTextVisible(False)
        bar_color = "#f38ba844" if self._side == "ask" else "#a6e3a144"
        bar_chunk = "#f38ba8" if self._side == "ask" else "#a6e3a1"
        self._bar.setStyleSheet(
            f"QProgressBar {{ background: {Colors.surface}; border: none; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {bar_chunk}; border-radius: 2px; }}"
        )

        self._qty_lbl = QLabel("—")
        self._qty_lbl.setFixedWidth(52)
        self._qty_lbl.setStyleSheet(f"color: {Colors.text_muted}; font-size: 9px;")
        self._qty_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(self._price_lbl)
        layout.addWidget(self._bar, stretch=1)
        layout.addWidget(self._qty_lbl)

    def set_data(self, price: int, qty: int, ratio: float):
        self._price_lbl.setText(f"{price:,}")
        self._qty_lbl.setText(f"{qty:,}")
        self._bar.setValue(int(ratio * 1000))

    def set_empty(self):
        self._price_lbl.setText("—")
        self._qty_lbl.setText("—")
        self._bar.setValue(0)
