"""gui/views/dashboard/metrics_panel.py — KPI 카드 4개 + PnL 차트."""
from __future__ import annotations

import sqlite3

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QProgressBar,
)
from PyQt6.QtCore import Qt

from gui.widgets.card import Card
from gui.design_tokens import Colors

try:
    import matplotlib
    matplotlib.use("QtAgg")
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
    import matplotlib.dates as mdates
    matplotlib.rcParams["font.family"] = "Malgun Gothic"
    matplotlib.rcParams["axes.unicode_minus"] = False
    _HAS_MPL = True
except ImportError:
    _HAS_MPL = False

_TOP_HEIGHT = 104


class MetricsPanel(QWidget):
    """KPI 카드 4개 + 일일 손익 차트를 한 행으로 묶은 패널."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pnl_timestamps: list[float] = []
        self._pnl_values: list[float] = []
        self._pnl_fig = None
        self._pnl_ax = None
        self._pnl_canvas = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # KPI 카드 4개
        summary_widget = QWidget()
        summary_layout = self._build_summary_bar()
        summary_widget.setLayout(summary_layout)
        summary_widget.setFixedHeight(_TOP_HEIGHT)
        layout.addWidget(summary_widget, stretch=1)

        # PnL 차트
        chart_card = Card(title="일일 손익 추이")
        chart_card.setFixedHeight(_TOP_HEIGHT)
        chart_card.addWidget(self._build_pnl_chart(), stretch=1)
        layout.addWidget(chart_card, stretch=1)

    # ── KPI 카드 ──────────────────────────────────────────────────────────────

    def _build_summary_bar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(6)

        card, self._pnl_value, self._pnl_subtitle = self._make_card("일일 손익")
        self._pnl_bar = QProgressBar()
        self._pnl_bar.setRange(-100, 100)
        self._pnl_bar.setValue(0)
        self._pnl_bar.setFixedHeight(4)
        self._pnl_bar.setTextVisible(False)
        self._pnl_bar.setStyleSheet(
            "QProgressBar { background-color: #45475a; border-radius: 2px; }"
            "QProgressBar::chunk { background-color: #a6e3a1; border-radius: 2px; }"
        )
        card.layout().addWidget(self._pnl_bar)
        layout.addWidget(card, stretch=1)

        card, self._trades_value, self._trades_subtitle = self._make_card("당일 거래")
        layout.addWidget(card, stretch=1)

        card, self._winrate_value, self._winrate_subtitle = self._make_card("승률")
        layout.addWidget(card, stretch=1)

        card, self._risk_value, self._risk_subtitle = self._make_card("리스크")
        layout.addWidget(card, stretch=1)

        return layout

    def _make_card(self, title: str) -> tuple[Card, QLabel, QLabel]:
        card = Card()
        card.content_layout().setSpacing(2)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-size: 10px; color: #6c7086;")
        value_lbl = QLabel("—")
        value_lbl.setStyleSheet("font-size: 18px; font-weight: bold; color: #cdd6f4;")
        sub_lbl = QLabel("")
        sub_lbl.setStyleSheet("font-size: 10px; color: #6c7086;")
        card.addWidget(title_lbl)
        card.addWidget(value_lbl)
        card.addWidget(sub_lbl)
        return card, value_lbl, sub_lbl

    # ── PnL 차트 ──────────────────────────────────────────────────────────────

    def _build_pnl_chart(self) -> QWidget:
        if not _HAS_MPL:
            return self._pnl_text_fallback()
        try:
            self._pnl_fig = Figure(figsize=(6, 0.9), dpi=100)
            self._pnl_fig.patch.set_facecolor("#313244")
            self._pnl_ax = self._pnl_fig.add_subplot(111)
            self._setup_pnl_empty()
            self._pnl_fig.tight_layout(pad=0.3)
            canvas = FigureCanvasQTAgg(self._pnl_fig)
            self._pnl_canvas = canvas
            return canvas
        except Exception:
            return self._pnl_text_fallback()

    def _setup_pnl_empty(self) -> None:
        ax = self._pnl_ax
        ax.clear()
        ax.set_facecolor("#313244")
        ax.text(0.5, 0.5, "엔진 시작 후 PnL 차트가 표시됩니다",
                transform=ax.transAxes, ha="center", va="center",
                color="#6c7086", fontsize=10)
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)

    def _pnl_text_fallback(self) -> QWidget:
        from PyQt6.QtWidgets import QFrame
        frame = QFrame()
        frame.setStyleSheet("background-color: #313244; border-radius: 6px;")
        h = QHBoxLayout(frame)
        h.setContentsMargins(12, 8, 12, 8)
        self._pnl_chart_label = QLabel("일중 PnL: —")
        self._pnl_chart_label.setStyleSheet("color: #cdd6f4; font-size: 14px; font-weight: bold;")
        self._pnl_range_label = QLabel("고: — / 저: —")
        self._pnl_range_label.setStyleSheet("color: #6c7086; font-size: 11px;")
        h.addWidget(self._pnl_chart_label)
        h.addStretch()
        h.addWidget(self._pnl_range_label)
        self._pnl_canvas = None
        self._pnl_fig = None
        self._pnl_ax = None
        return frame

    # ── 공개 업데이트 API ─────────────────────────────────────────────────────

    def update_pnl_chart(self, timestamp: float, value: float) -> None:
        self._pnl_timestamps.append(timestamp)
        self._pnl_values.append(value)

        if self._pnl_ax is None or self._pnl_canvas is None:
            if hasattr(self, "_pnl_chart_label"):
                color = Colors.accent_green if value >= 0 else Colors.accent_red
                sign = "+" if value >= 0 else ""
                self._pnl_chart_label.setText(f"일중 PnL: {sign}{value:,.0f}원")
                self._pnl_chart_label.setStyleSheet(
                    f"color: {color}; font-size: 14px; font-weight: bold;"
                )
                if self._pnl_values:
                    self._pnl_range_label.setText(
                        f"고: +{max(self._pnl_values):,.0f} / 저: {min(self._pnl_values):,.0f}"
                    )
            return

        from datetime import datetime
        ax = self._pnl_ax
        ax.clear()
        ax.set_facecolor("#313244")
        ax.axhline(y=0, color="#585b70", linewidth=0.5, linestyle="--")

        times = [datetime.fromtimestamp(t) for t in self._pnl_timestamps]
        vals_k = [v / 1000.0 for v in self._pnl_values]

        ax.plot(times, vals_k, color="#89b4fa", linewidth=1.5)
        ax.fill_between(times, vals_k, 0,
                        where=[v >= 0 for v in vals_k],
                        color="#a6e3a1", alpha=0.15)
        ax.fill_between(times, vals_k, 0,
                        where=[v < 0 for v in vals_k],
                        color="#f38ba8", alpha=0.15)
        ax.tick_params(colors="#6c7086", labelsize=7)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#45475a")
        ax.spines["bottom"].set_color("#45475a")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        self._pnl_fig.tight_layout(pad=0.5)
        self._pnl_canvas.draw_idle()

    def update_summary(self, data: dict) -> None:
        db_pnl, db_buys, db_sells, db_wins, _ = self._query_db()

        data_pnl = float(data.get("daily_pnl", 0.0) or 0.0)
        pnl = db_pnl if abs(db_pnl) > abs(data_pnl) else data_pnl
        pnl_pct = data.get("daily_pnl_pct", 0.0)
        capital = data.get("available_capital", 0)
        initial = data.get("initial_capital", 0)
        pnl_color = Colors.accent_green if pnl >= 0 else Colors.accent_red
        sign = "+" if pnl >= 0 else ""
        self._pnl_value.setText(f"{sign}{pnl:,.0f}")
        self._pnl_value.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {pnl_color};"
        )
        self._pnl_subtitle.setText(f"자본: {int(capital):,}원 / {int(initial):,}원")
        bar_val = max(-100, min(100, int(pnl_pct * 50)))
        self._pnl_bar.setValue(bar_val)
        self._pnl_bar.setStyleSheet(
            f"QProgressBar {{ background-color: #45475a; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background-color: {pnl_color}; border-radius: 2px; }}"
        )

        open_count = int(data.get("open_positions_count", 0) or 0)
        data_trades = int(data.get("trades_count", 0) or 0)
        trades_count = max(db_sells, data_trades)
        buys_count = max(db_buys, data_trades)
        self._trades_value.setText(f"{buys_count}")
        self._trades_subtitle.setText(f"청산 {trades_count} / 보유 {open_count}")

        if db_sells > 0:
            win_rate = db_wins / db_sells * 100
        else:
            win_rate = float(data.get("win_rate", 0.0) or 0.0)
        avg = data.get("avg_win_rate", 0.0)
        self._winrate_value.setText(f"{win_rate:.1f}%")
        self._winrate_value.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #f9e2af;"
        )
        self._winrate_subtitle.setText(f"Avg: {avg:.1f}%")

        status = data.get("risk_status", "Normal")
        dd = data.get("dd_pct", 0.0)
        usage_pct = ((initial - capital) / initial * 100) if initial > 0 else 0
        status_color = {
            "Normal": Colors.accent_green,
            "Warning": Colors.accent_yellow,
            "Halted": Colors.accent_red,
        }.get(status, Colors.accent_green)
        self._risk_value.setText(status)
        self._risk_value.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {status_color};"
        )
        self._risk_subtitle.setText(f"DD: -{abs(dd):.2f}% | 투자: {usage_pct:.0f}%")

    @staticmethod
    def _query_db() -> tuple[float, int, int, int, int]:
        try:
            from datetime import date as _date
            conn = sqlite3.connect("daytrader.db")
            today = _date.today().isoformat()
            row = conn.execute(
                "SELECT "
                " COALESCE(SUM(CASE WHEN side='sell' THEN COALESCE(pnl,0) ELSE 0 END),0),"
                " COALESCE(SUM(CASE WHEN side='buy'  THEN 1 ELSE 0 END),0),"
                " COALESCE(SUM(CASE WHEN side='sell' THEN 1 ELSE 0 END),0),"
                " COALESCE(SUM(CASE WHEN side='sell' AND COALESCE(pnl,0)>0 THEN 1 ELSE 0 END),0),"
                " COALESCE(SUM(CASE WHEN side='sell' AND COALESCE(pnl,0)<0 THEN 1 ELSE 0 END),0)"
                " FROM trades WHERE date(traded_at)=?",
                (today,),
            ).fetchone()
            conn.close()
            return (float(row[0] or 0), int(row[1] or 0), int(row[2] or 0),
                    int(row[3] or 0), int(row[4] or 0))
        except Exception:
            return 0.0, 0, 0, 0, 0
