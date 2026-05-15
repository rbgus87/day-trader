"""gui/views/dashboard/daily_summary_panel.py — 장 마감 후 일일 요약 패널."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from gui.components.metric_card import MetricCard
from gui.design_tokens import Colors, Border, Spacing, Typography
from gui.widgets.card import Card

_EXIT_REASON_KO = {
    "trailing_stop":  "trailing_stop",
    "forced_close":   "forced_close",
    "stop_loss":      "stop_loss",
    "breakeven_stop": "breakeven_stop",
    "momentum_fade":  "momentum_fade",
    "limit_up_exit":  "limit_up_exit",
}

_EXIT_REASON_COLORS = {
    "trailing_stop":  Colors.accent_blue,
    "forced_close":   Colors.text_secondary,
    "stop_loss":      Colors.accent_red,
    "breakeven_stop": Colors.accent_green,
    "momentum_fade":  Colors.accent_mauve,
    "limit_up_exit":  Colors.accent_yellow,
}

_BAR_MAX_W = 200


class _ExitBar(QWidget):
    """단일 청산 사유 수평 바."""

    def __init__(self, reason: str, count: int, total: int, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.gap_md)

        color = _EXIT_REASON_COLORS.get(reason, Colors.text_muted)
        bar_w = max(8, int(_BAR_MAX_W * count / max(total, 1)))

        bar = QFrame()
        bar.setFixedSize(bar_w, 12)
        bar.setStyleSheet(f"background: {color}; border-radius: 3px; border: none;")

        label_text = f"{_EXIT_REASON_KO.get(reason, reason)} ({count}건)"
        lbl = QLabel(label_text)
        lbl.setStyleSheet(
            f"color: {Colors.text_secondary}; font-size: {Typography.size_xs}px;"
            f" background: transparent;"
        )

        layout.addWidget(bar)
        layout.addWidget(lbl)
        layout.addStretch()


class DailySummaryPanel(QWidget):
    """장 마감 후 일일 요약 패널.

    15:30 daily_report 완료 시 또는 리포트 버튼 클릭 시 자동 표시.
    장중에는 숨겨져 있음. 닫기 버튼으로 수동 숨김 가능.
    """

    close_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.setVisible(False)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._card = Card()
        cl = self._card.content_layout()
        cl.setSpacing(Spacing.gap_md)

        # ── 헤더: 제목 + 닫기 버튼 ─────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        self._title_lbl = QLabel("일일 요약")
        self._title_lbl.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {Colors.text_primary};"
            f" background: transparent;"
        )
        hdr.addWidget(self._title_lbl)
        hdr.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {Colors.text_muted};"
            f" border: none; font-size: 11px; }}"
            f"QPushButton:hover {{ color: {Colors.text_primary}; }}"
        )
        close_btn.clicked.connect(self.close_requested)
        hdr.addWidget(close_btn)
        cl.addLayout(hdr)

        # 구분선
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {Colors.surface_border}; border: none;")
        cl.addWidget(sep)

        # ── KPI 카드 행 ────────────────────────────────────────────
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(Spacing.gap_md)
        self._card_trades  = MetricCard("거래")
        self._card_winrate = MetricCard("승률")
        self._card_pnl     = MetricCard("손익")
        kpi_row.addWidget(self._card_trades)
        kpi_row.addWidget(self._card_winrate)
        kpi_row.addWidget(self._card_pnl)
        cl.addLayout(kpi_row)

        # ── 청산 분포 ───────────────────────────────────────────────
        self._exit_section = QWidget()
        self._exit_section.setStyleSheet("background: transparent;")
        exit_v = QVBoxLayout(self._exit_section)
        exit_v.setContentsMargins(0, 0, 0, 0)
        exit_v.setSpacing(Spacing.gap_xs)
        exit_title = QLabel("청산 분포")
        exit_title.setStyleSheet(
            f"color: {Colors.text_muted}; font-size: {Typography.size_xs}px; background: transparent;"
        )
        exit_v.addWidget(exit_title)
        self._exit_bars_widget = QWidget()
        self._exit_bars_widget.setStyleSheet("background: transparent;")
        self._exit_bars_layout = QVBoxLayout(self._exit_bars_widget)
        self._exit_bars_layout.setContentsMargins(0, 0, 0, 0)
        self._exit_bars_layout.setSpacing(Spacing.gap_xs)
        exit_v.addWidget(self._exit_bars_widget)
        cl.addWidget(self._exit_section)
        self._exit_section.setVisible(False)

        # ── 섀도우 트래커 섹션 ──────────────────────────────────────
        self._shadow_widget = QWidget()
        self._shadow_widget.setStyleSheet("background: transparent;")
        shadow_v = QVBoxLayout(self._shadow_widget)
        shadow_v.setContentsMargins(0, 0, 0, 0)
        shadow_v.setSpacing(Spacing.gap_xs)
        shadow_title = QLabel("섀도우 (시장 필터 차단)")
        shadow_title.setStyleSheet(
            f"color: {Colors.text_muted}; font-size: {Typography.size_xs}px; background: transparent;"
        )
        self._shadow_body = QLabel("데이터 없음")
        self._shadow_body.setWordWrap(True)
        self._shadow_body.setStyleSheet(
            f"color: {Colors.text_secondary}; font-size: {Typography.size_sm}px; background: transparent;"
        )
        shadow_v.addWidget(shadow_title)
        shadow_v.addWidget(self._shadow_body)
        cl.addWidget(self._shadow_widget)
        self._shadow_widget.setVisible(False)

        # ── 빈 상태 ────────────────────────────────────────────────
        self._empty_lbl = QLabel("오늘 거래 없음")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setStyleSheet(
            f"color: {Colors.text_muted}; font-size: {Typography.size_md}px;"
            f" background: transparent; padding: 6px;"
        )
        cl.addWidget(self._empty_lbl)
        self._empty_lbl.setVisible(False)

        root.addWidget(self._card)

    # ── 공개 API ──────────────────────────────────────────────────────────────

    def update_summary(self, data: dict) -> None:
        """summary dict로 패널을 갱신하고 표시한다."""
        today = data.get("date", "")
        self._title_lbl.setText(f"일일 요약 ({today})" if today else "일일 요약")

        total_trades = int(data.get("total_trades", 0))
        wins         = int(data.get("wins", 0))
        losses       = int(data.get("losses", 0))
        win_rate     = float(data.get("win_rate", 0.0))
        total_pnl    = int(data.get("total_pnl", 0))
        exit_reasons: dict[str, int] = data.get("exit_reasons", {})
        shadow: dict = data.get("shadow", {})

        if total_trades == 0:
            self._show_empty()
        else:
            self._show_data(total_trades, wins, losses, win_rate, total_pnl, exit_reasons, shadow)

        self.setVisible(True)

    # ── 내부 렌더링 ───────────────────────────────────────────────────────────

    def _show_empty(self) -> None:
        self._card_trades.set_value("0건")
        self._card_winrate.set_value("—")
        self._card_pnl.set_value("0원")
        self._exit_section.setVisible(False)
        self._shadow_widget.setVisible(False)
        self._empty_lbl.setVisible(True)

    def _show_data(
        self,
        total_trades: int,
        wins: int,
        losses: int,
        win_rate: float,
        total_pnl: int,
        exit_reasons: dict[str, int],
        shadow: dict,
    ) -> None:
        self._empty_lbl.setVisible(False)

        # KPI
        self._card_trades.set_value(f"{total_trades}건")
        self._card_trades.set_subtitle(f"승 {wins} / 패 {losses}")

        wr_pct = win_rate * 100 if win_rate <= 1.0 else win_rate
        wr_color = Colors.accent_green if wr_pct >= 50 else Colors.accent_red
        self._card_winrate.set_value(f"{wr_pct:.1f}%", wr_color)

        pnl_color = Colors.accent_green if total_pnl >= 0 else Colors.accent_red
        self._card_pnl.set_value(f"{total_pnl:+,}원", pnl_color)

        # 청산 분포
        if exit_reasons:
            self._rebuild_exit_bars(exit_reasons)
            self._exit_section.setVisible(True)
        else:
            self._exit_section.setVisible(False)

        # 섀도우
        shadow_total = int(shadow.get("total", 0))
        if shadow_total > 0:
            self._update_shadow(shadow, shadow_total)
            self._shadow_widget.setVisible(True)
        else:
            self._shadow_widget.setVisible(False)

    def _rebuild_exit_bars(self, exit_reasons: dict[str, int]) -> None:
        while self._exit_bars_layout.count():
            item = self._exit_bars_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        total = sum(exit_reasons.values())
        for reason, count in sorted(exit_reasons.items(), key=lambda x: -x[1]):
            self._exit_bars_layout.addWidget(_ExitBar(reason, count, total))

    def _update_shadow(self, shadow: dict, shadow_total: int) -> None:
        profit_count   = int(shadow.get("profit_count", 0))
        avg_profit_pct = float(shadow.get("avg_profit_pct", 0.0)) * 100

        pct_of_total = profit_count / shadow_total * 100
        body = f"차단 {shadow_total}건 중 수익이었을 {profit_count}건 ({pct_of_total:.0f}%)"
        if avg_profit_pct > 0:
            body += f" | 평균 +{avg_profit_pct:.1f}% (놓친 기회)"

        color = Colors.accent_red if profit_count > 0 else Colors.text_secondary
        self._shadow_body.setText(body)
        self._shadow_body.setStyleSheet(
            f"color: {color}; font-size: {Typography.size_sm}px; background: transparent;"
        )
