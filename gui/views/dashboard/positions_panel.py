"""보유 포지션 카드 패널 — 각 포지션을 카드로 표시, 1초 경과 시간 갱신."""
from __future__ import annotations

from datetime import datetime as _dt

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from gui.design_tokens import Colors, Border, Spacing
from gui.widgets.card import Card

_GAUGE_RANGE = 15.0   # ±15% 게이지 범위


class PositionCard(QWidget):
    """단일 포지션 카드 위젯."""

    close_requested = pyqtSignal(str, str, int)  # ticker, name, qty

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: dict = {}
        self._entry_time: _dt | None = None
        self._build_ui()

    def _build_ui(self):
        self.setStyleSheet(
            f"QWidget {{ background: {Colors.surface}; border-radius: {Border.radius_md}px; }}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(Spacing.gap_lg, Spacing.gap_md, Spacing.gap_lg, Spacing.gap_md)
        root.setSpacing(Spacing.gap_sm)

        # ── 헤더 행: 종목명 + VI 뱃지 ──────────────────────────────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(Spacing.gap_sm)
        self._ticker_lbl = QLabel("—")
        self._ticker_lbl.setStyleSheet(
            f"color: {Colors.accent_blue}; font-size: 13px; font-weight: bold; background: transparent;"
        )
        self._vi_badge = QLabel("VI")
        self._vi_badge.setStyleSheet(
            f"color: {Colors.accent_yellow}; background: #f9e2af22; "
            f"border: 1px solid {Colors.accent_yellow}; border-radius: 3px; "
            f"font-size: 9px; font-weight: bold; padding: 0px 4px;"
        )
        self._vi_badge.setVisible(False)
        self._elapsed_lbl = QLabel("0분")
        self._elapsed_lbl.setStyleSheet(
            f"color: {Colors.text_muted}; font-size: 10px; background: transparent;"
        )
        self._elapsed_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header_row.addWidget(self._ticker_lbl)
        header_row.addWidget(self._vi_badge)
        header_row.addStretch()
        header_row.addWidget(self._elapsed_lbl)
        root.addLayout(header_row)

        # ── 가격 행: 진입가 → 현재가 (수익률) ────────────────────────────
        price_row = QHBoxLayout()
        price_row.setSpacing(Spacing.gap_sm)
        self._entry_price_lbl = QLabel("—")
        self._entry_price_lbl.setStyleSheet(
            f"color: {Colors.text_secondary}; font-size: 11px; background: transparent;"
        )
        arrow = QLabel("→")
        arrow.setStyleSheet(f"color: {Colors.text_muted}; background: transparent;")
        self._cur_price_lbl = QLabel("—")
        self._cur_price_lbl.setStyleSheet(
            f"color: {Colors.text_primary}; font-size: 11px; font-weight: bold; background: transparent;"
        )
        self._pnl_pct_lbl = QLabel("+0.00%")
        self._pnl_pct_lbl.setStyleSheet(
            f"color: {Colors.accent_green}; font-size: 12px; font-weight: bold; background: transparent;"
        )
        self._pnl_pct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        price_row.addWidget(self._entry_price_lbl)
        price_row.addWidget(arrow)
        price_row.addWidget(self._cur_price_lbl)
        price_row.addStretch()
        price_row.addWidget(self._pnl_pct_lbl)
        root.addLayout(price_row)

        # ── 수익률 게이지 바 ──────────────────────────────────────────────
        self._gauge = QProgressBar()
        self._gauge.setRange(0, int(_GAUGE_RANGE * 2 * 10))
        self._gauge.setValue(int(_GAUGE_RANGE * 10))  # 0% 중앙
        self._gauge.setFixedHeight(6)
        self._gauge.setTextVisible(False)
        self._gauge.setStyleSheet(
            f"QProgressBar {{ background: {Colors.surface_elevated}; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {Colors.accent_green}; border-radius: 3px; }}"
        )
        root.addWidget(self._gauge)

        # ── PnL + 수량 행 ────────────────────────────────────────────────
        pnl_row = QHBoxLayout()
        pnl_row.setSpacing(Spacing.gap_md)
        self._unrealized_lbl = QLabel("미실현: —")
        self._unrealized_lbl.setStyleSheet(
            f"color: {Colors.accent_green}; font-size: 11px; font-weight: bold; background: transparent;"
        )
        self._qty_lbl = QLabel("수량: —")
        self._qty_lbl.setStyleSheet(
            f"color: {Colors.text_muted}; font-size: 10px; background: transparent;"
        )
        self._qty_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        pnl_row.addWidget(self._unrealized_lbl)
        pnl_row.addStretch()
        pnl_row.addWidget(self._qty_lbl)
        root.addLayout(pnl_row)

        # ── 손절 정보 행 ─────────────────────────────────────────────────
        self._stop_lbl = QLabel("손절: —")
        self._stop_lbl.setStyleSheet(
            f"color: {Colors.text_muted}; font-size: 10px; background: transparent;"
        )
        root.addWidget(self._stop_lbl)

        # ── 수동 청산 버튼 ─────────────────────────────────────────────
        close_btn = QPushButton("청산")
        close_btn.setFixedHeight(24)
        close_btn.setStyleSheet(
            "QPushButton { background: #c0392b; color: white; border-radius: 4px; "
            "font-size: 11px; font-weight: bold; border: none; }"
            "QPushButton:hover { background: #e74c3c; }"
            "QPushButton:pressed { background: #922b21; }"
        )
        close_btn.clicked.connect(self._on_close_clicked)
        root.addWidget(close_btn)

    def _on_close_clicked(self):
        ticker = self._data.get("ticker", "")
        name = self._data.get("name", ticker)
        qty = int(self._data.get("remaining_qty", 0) or self._data.get("qty", 0) or 0)
        self.close_requested.emit(ticker, name, qty)

    # ── 업데이트 ──────────────────────────────────────────────────────────────

    def update_data(self, data: dict):
        self._data = data

        ticker = data.get("ticker", "")
        name = data.get("name", "")
        display = f"{name} ({ticker})" if name and name != ticker else ticker
        self._ticker_lbl.setText(display)

        # VI 뱃지
        vi = bool(data.get("vi_active", False))
        self._vi_badge.setVisible(vi)

        # 진입 시각
        et = data.get("entry_time")
        if isinstance(et, str):
            try:
                self._entry_time = _dt.fromisoformat(et)
            except ValueError:
                self._entry_time = None
        elif isinstance(et, _dt):
            self._entry_time = et
        else:
            self._entry_time = None

        entry_price = float(data.get("entry_price", 0) or 0)
        cur_price = float(data.get("current_price", 0) or 0)
        qty_total = int(data.get("qty", 0) or 0)
        qty_rem = int(data.get("remaining_qty", qty_total) or qty_total)
        pnl_pct = float(data.get("pnl_pct", 0) or 0)

        self._entry_price_lbl.setText(f"{entry_price:,.0f}")
        self._cur_price_lbl.setText(f"{cur_price:,.0f}" if cur_price > 0 else "—")

        pnl_color = Colors.accent_green if pnl_pct >= 0 else Colors.accent_red
        sign = "+" if pnl_pct >= 0 else ""
        self._pnl_pct_lbl.setText(f"{sign}{pnl_pct:.2f}%")
        self._pnl_pct_lbl.setStyleSheet(
            f"color: {pnl_color}; font-size: 12px; font-weight: bold; background: transparent;"
        )

        # 게이지 (0% = 중앙, ±_GAUGE_RANGE = 끝)
        gauge_val = int((_GAUGE_RANGE + max(-_GAUGE_RANGE, min(_GAUGE_RANGE, pnl_pct))) * 10)
        self._gauge.setValue(gauge_val)
        chunk_color = Colors.accent_green if pnl_pct >= 0 else Colors.accent_red
        self._gauge.setStyleSheet(
            f"QProgressBar {{ background: {Colors.surface_elevated}; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {chunk_color}; border-radius: 3px; }}"
        )

        # 미실현 PnL
        if cur_price > 0 and entry_price > 0:
            unrealized = (cur_price - entry_price) * qty_rem
            sign_u = "+" if unrealized >= 0 else ""
            u_color = Colors.accent_green if unrealized >= 0 else Colors.accent_red
            self._unrealized_lbl.setText(f"미실현: {sign_u}{unrealized:,.0f}원")
            self._unrealized_lbl.setStyleSheet(
                f"color: {u_color}; font-size: 11px; font-weight: bold; background: transparent;"
            )
        else:
            self._unrealized_lbl.setText("미실현: —")

        qty_text = f"수량: {qty_rem}" if qty_rem == qty_total else f"수량: {qty_rem}/{qty_total}"
        self._qty_lbl.setText(qty_text)

        # 손절 정보
        stop_price = float(data.get("stop_loss", 0) or 0)
        be_active = bool(data.get("breakeven_active", False))

        if stop_price > 0 and cur_price > 0:
            stop_dist_pct = (cur_price - stop_price) / cur_price * 100
            if be_active and stop_price > entry_price:
                stop_text = f"손절: {stop_price:,.0f}  [BE발동 중 — 손절까지 -{stop_dist_pct:.1f}%]"
                stop_color = Colors.accent_green
            elif stop_dist_pct < 2.0:
                stop_text = f"손절: {stop_price:,.0f}  [손절까지 -{stop_dist_pct:.1f}%  ⚠]"
                stop_color = Colors.accent_red
            else:
                stop_text = f"손절: {stop_price:,.0f}  [손절까지 -{stop_dist_pct:.1f}%]"
                stop_color = Colors.text_muted
        else:
            stop_text = "손절: —"
            stop_color = Colors.text_muted

        self._stop_lbl.setText(stop_text)
        self._stop_lbl.setStyleSheet(
            f"color: {stop_color}; font-size: 10px; background: transparent;"
        )

        self.refresh_elapsed()

    def refresh_elapsed(self):
        """1초 타이머에서 경과 시간 갱신."""
        if self._entry_time:
            elapsed = int((_dt.now() - self._entry_time).total_seconds() / 60)
            self._elapsed_lbl.setText(f"{elapsed}분")


class PositionsPanel(QWidget):
    """보유 포지션 카드 목록 패널 (최대 3개)."""

    manual_close_requested = pyqtSignal(str, str, int)  # ticker, name, qty

    def __init__(self, parent=None):
        super().__init__(parent)
        self._max_positions = 3
        self._cards: list[PositionCard] = []
        self._build_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._card_wrapper = Card(title="보유 포지션  0 / 3")

        # 스크롤 영역
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(self._scroll.Shape.NoFrame)
        self._scroll.setStyleSheet("background: transparent;")
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(0, 0, 0, 0)
        self._container_layout.setSpacing(Spacing.gap_sm)

        # 빈 상태 레이블
        self._empty_lbl = QLabel("보유 종목 없음 — 신호 대기 중")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setStyleSheet(
            f"color: {Colors.text_muted}; font-size: 11px; padding: 20px; background: transparent;"
        )
        self._container_layout.addWidget(self._empty_lbl)
        self._container_layout.addStretch()

        self._scroll.setWidget(self._container)
        self._card_wrapper.addWidget(self._scroll, stretch=1)
        layout.addWidget(self._card_wrapper)

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def set_max_positions(self, n: int):
        self._max_positions = n

    def update_positions(self, positions: list[dict]):
        self._card_wrapper.setTitle(
            f"보유 포지션  {len(positions)} / {self._max_positions}"
        )

        # 카드 수 조정 (생성/제거)
        while len(self._cards) < len(positions):
            c = PositionCard()
            c.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            c.close_requested.connect(self.manual_close_requested)
            self._cards.append(c)
            # empty_lbl 위에 삽입
            self._container_layout.insertWidget(
                self._container_layout.count() - 2, c
            )

        while len(self._cards) > len(positions):
            c = self._cards.pop()
            self._container_layout.removeWidget(c)
            c.deleteLater()

        # 데이터 업데이트
        for card, data in zip(self._cards, positions):
            card.update_data(data)

        self._empty_lbl.setVisible(len(positions) == 0)

    # ── 내부 ──────────────────────────────────────────────────────────────────

    def _tick(self):
        for card in self._cards:
            card.refresh_elapsed()
