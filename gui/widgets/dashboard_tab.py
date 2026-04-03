"""Dashboard Tab — matplotlib 차트 + 멀티종목 대시보드."""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QTableWidget, QTableWidgetItem, QSplitter, QHeaderView,
    QAbstractItemView, QSizePolicy, QProgressBar,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

# matplotlib 차트 (OpenGL 불필요, segfault 안전)
try:
    import matplotlib
    matplotlib.use("QtAgg")
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.figure import Figure
    import matplotlib.dates as mdates
    matplotlib.rcParams["font.family"] = "Malgun Gothic"
    matplotlib.rcParams["axes.unicode_minus"] = False
    _HAS_MATPLOTLIB = True
except ImportError:
    _HAS_MATPLOTLIB = False


class DashboardTab(QWidget):
    """Dashboard tab — summary, PnL chart, positions, watchlist, trades, daily history."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pnl_timestamps: list[float] = []
        self._pnl_values: list[float] = []
        self._pnl_fig = None
        self._pnl_ax = None
        self._pnl_canvas = None
        self._daily_fig = None
        self._daily_ax = None
        self._daily_canvas = None
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # 1. 서머리 바
        root.addLayout(self._build_summary_bar())

        # 2. PnL 차트 (전체 폭)
        root.addWidget(self._build_pnl_chart())

        # 3. 중앙: 포지션(좌) + 감시종목(우)
        mid_splitter = QSplitter(Qt.Orientation.Horizontal)
        mid_splitter.addWidget(self._build_positions_panel())
        mid_splitter.addWidget(self._build_watchlist_panel())
        mid_splitter.setSizes([550, 450])
        mid_splitter.setChildrenCollapsible(False)
        root.addWidget(mid_splitter, stretch=1)

        # 4. 당일 체결
        root.addWidget(self._build_trades_panel())

        # 5. 최근 성과 바 차트
        root.addWidget(self._build_daily_history())

    def _build_summary_bar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(6)

        frame, self._pnl_value, self._pnl_subtitle = self._make_summary_card("일일 손익")
        self._pnl_bar = QProgressBar()
        self._pnl_bar.setRange(-100, 100)
        self._pnl_bar.setValue(0)
        self._pnl_bar.setFixedHeight(4)
        self._pnl_bar.setTextVisible(False)
        self._pnl_bar.setStyleSheet(
            "QProgressBar { background-color: #45475a; border-radius: 2px; }"
            "QProgressBar::chunk { background-color: #a6e3a1; border-radius: 2px; }"
        )
        frame.layout().addWidget(self._pnl_bar)
        layout.addWidget(frame, stretch=1)

        frame, self._trades_value, self._trades_subtitle = self._make_summary_card("당일 거래")
        layout.addWidget(frame, stretch=1)

        frame, self._winrate_value, self._winrate_subtitle = self._make_summary_card("승률")
        layout.addWidget(frame, stretch=1)

        frame, self._risk_value, self._risk_subtitle = self._make_summary_card("리스크")
        layout.addWidget(frame, stretch=1)

        return layout

    def _make_summary_card(self, title: str) -> tuple["QFrame", "QLabel", "QLabel"]:
        frame = QFrame()
        frame.setStyleSheet("QFrame { background-color: #313244; border-radius: 6px; }")
        vbox = QVBoxLayout(frame)
        vbox.setContentsMargins(10, 8, 10, 8)
        vbox.setSpacing(2)

        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 10px; color: #6c7086;")
        value_label = QLabel("—")
        value_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #cdd6f4;")
        subtitle_label = QLabel("")
        subtitle_label.setStyleSheet("font-size: 10px; color: #6c7086;")

        vbox.addWidget(title_label)
        vbox.addWidget(value_label)
        vbox.addWidget(subtitle_label)

        return frame, value_label, subtitle_label

    # ── 차트 빌더 ────────────────────────────────────────────────────────

    def _setup_pnl_empty_state(self) -> None:
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

    def _setup_daily_empty_state(self) -> None:
        ax = self._daily_ax
        ax.clear()
        ax.set_facecolor("#313244")
        ax.text(0.5, 0.5, "최근 5일 성과 — 데이터 수집 중",
                transform=ax.transAxes, ha="center", va="center",
                color="#6c7086", fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)

    def _build_pnl_chart(self) -> QWidget:
        """일중 PnL 영역 차트 (matplotlib)."""
        if not _HAS_MATPLOTLIB:
            return self._pnl_text_fallback()
        try:
            self._pnl_fig = Figure(figsize=(10, 1.5), dpi=100)
            self._pnl_fig.patch.set_facecolor("#313244")
            self._pnl_ax = self._pnl_fig.add_subplot(111)
            self._setup_pnl_empty_state()
            self._pnl_fig.tight_layout(pad=0.5)

            canvas = FigureCanvasQTAgg(self._pnl_fig)
            canvas.setFixedHeight(150)
            self._pnl_canvas = canvas
            return canvas
        except Exception as e:
            from loguru import logger
            logger.warning(f"PnL 차트 초기화 실패: {e}")
            return self._pnl_text_fallback()

    def _pnl_text_fallback(self) -> QWidget:
        frame = QFrame()
        frame.setFixedHeight(60)
        frame.setStyleSheet("background-color: #313244; border-radius: 6px;")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        self._pnl_chart_label = QLabel("일중 PnL: —")
        self._pnl_chart_label.setStyleSheet("color: #cdd6f4; font-size: 14px; font-weight: bold;")
        layout.addWidget(self._pnl_chart_label)
        layout.addStretch()
        self._pnl_range_label = QLabel("고: — / 저: —")
        self._pnl_range_label.setStyleSheet("color: #6c7086; font-size: 11px;")
        layout.addWidget(self._pnl_range_label)
        self._pnl_canvas = None
        self._pnl_fig = None
        self._pnl_ax = None
        return frame

    def _build_daily_history(self) -> QWidget:
        """최근 5일 일일 PnL 바 차트 (matplotlib)."""
        if not _HAS_MATPLOTLIB:
            return self._daily_history_fallback()
        try:
            self._daily_fig = Figure(figsize=(10, 0.8), dpi=100)
            self._daily_fig.patch.set_facecolor("#313244")
            self._daily_ax = self._daily_fig.add_subplot(111)
            self._setup_daily_empty_state()
            self._daily_fig.tight_layout(pad=0.5)

            canvas = FigureCanvasQTAgg(self._daily_fig)
            canvas.setFixedHeight(80)
            self._daily_canvas = canvas
            return canvas
        except Exception as e:
            from loguru import logger
            logger.warning(f"일일 성과 차트 초기화 실패: {e}")
            return self._daily_history_fallback()

    def _daily_history_fallback(self) -> QWidget:
        frame = QFrame()
        frame.setFixedHeight(40)
        frame.setStyleSheet("background-color: #313244; border-radius: 6px;")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 4, 12, 4)
        lbl = QLabel("최근 성과: matplotlib 미설치")
        lbl.setStyleSheet("color: #6c7086; font-size: 10px;")
        layout.addWidget(lbl)
        self._daily_canvas = None
        self._daily_ax = None
        self._daily_fig = None
        return frame

    # ── 테이블 빌더 ──────────────────────────────────────────────────────

    def _build_positions_panel(self) -> QWidget:
        panel = QWidget()
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(0, 0, 4, 0)
        vbox.setSpacing(2)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel("보유 포지션")
        title.setStyleSheet("font-size: 12px; font-weight: bold; color: #cdd6f4;")
        header.addWidget(title)
        self._positions_count_label = QLabel("0 / 3")
        self._positions_count_label.setStyleSheet("font-size: 11px; color: #6c7086;")
        header.addWidget(self._positions_count_label)
        header.addStretch()
        vbox.addLayout(header)

        self._positions_table = QTableWidget()
        columns = ["종목", "전략", "수익률", "경과", "손절가", "TP1", "상태"]
        self._positions_table.setColumnCount(len(columns))
        self._positions_table.setHorizontalHeaderLabels(columns)
        self._positions_table.setAlternatingRowColors(True)
        self._positions_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._positions_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._positions_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._positions_table.verticalHeader().setVisible(False)
        vbox.addWidget(self._positions_table, stretch=1)
        return panel

    def _build_watchlist_panel(self) -> QWidget:
        panel = QWidget()
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(4, 0, 0, 0)
        vbox.setSpacing(2)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel("감시 종목")
        title.setStyleSheet("font-size: 12px; font-weight: bold; color: #cdd6f4;")
        header.addWidget(title)
        self._watchlist_count_label = QLabel("0종목")
        self._watchlist_count_label.setStyleSheet("font-size: 11px; color: #6c7086;")
        header.addWidget(self._watchlist_count_label)
        header.addStretch()
        vbox.addLayout(header)

        self._watchlist_table = QTableWidget()
        columns = ["종목", "현재가", "등락%", "점수"]
        self._watchlist_table.setColumnCount(len(columns))
        self._watchlist_table.setHorizontalHeaderLabels(columns)
        self._watchlist_table.setAlternatingRowColors(True)
        self._watchlist_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._watchlist_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._watchlist_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._watchlist_table.verticalHeader().setVisible(False)
        vbox.addWidget(self._watchlist_table, stretch=1)
        return panel

    def _build_trades_panel(self) -> QWidget:
        panel = QWidget()
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(0, 4, 0, 0)
        vbox.setSpacing(2)

        title = QLabel("당일 체결")
        title.setStyleSheet("font-size: 12px; font-weight: bold; color: #cdd6f4;")
        vbox.addWidget(title)

        self._trades_table = QTableWidget()
        columns = ["시간", "종목", "매매", "가격", "수량", "손익", "사유"]
        self._trades_table.setColumnCount(len(columns))
        self._trades_table.setHorizontalHeaderLabels(columns)
        self._trades_table.setAlternatingRowColors(True)
        self._trades_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._trades_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._trades_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._trades_table.verticalHeader().setVisible(False)
        self._trades_table.setMaximumHeight(120)
        vbox.addWidget(self._trades_table)
        return panel

    # ------------------------------------------------------------------
    # Public update methods
    # ------------------------------------------------------------------

    def update_pnl_chart(self, timestamp: float, value: float) -> None:
        """PnL 데이터 포인트 추가 및 차트 업데이트."""
        self._pnl_timestamps.append(timestamp)
        self._pnl_values.append(value)

        if self._pnl_ax is None or self._pnl_canvas is None:
            if hasattr(self, "_pnl_chart_label") and self._pnl_chart_label:
                color = "#a6e3a1" if value >= 0 else "#f38ba8"
                sign = "+" if value >= 0 else ""
                self._pnl_chart_label.setText(f"일중 PnL: {sign}{value:,.0f}원")
                self._pnl_chart_label.setStyleSheet(
                    f"color: {color}; font-size: 14px; font-weight: bold;"
                )
                high = max(self._pnl_values)
                low = min(self._pnl_values)
                self._pnl_range_label.setText(f"고: +{high:,.0f} / 저: {low:,.0f}")
            return

        from datetime import datetime

        ax = self._pnl_ax
        ax.clear()
        ax.set_facecolor("#313244")
        ax.axhline(y=0, color="#585b70", linewidth=0.5, linestyle="--")

        times = [datetime.fromtimestamp(t) for t in self._pnl_timestamps]
        values_k = [v / 1000.0 for v in self._pnl_values]

        ax.plot(times, values_k, color="#89b4fa", linewidth=1.5)
        ax.fill_between(times, values_k, 0,
                        where=[v >= 0 for v in values_k],
                        color="#a6e3a1", alpha=0.15)
        ax.fill_between(times, values_k, 0,
                        where=[v < 0 for v in values_k],
                        color="#f38ba8", alpha=0.15)

        ax.tick_params(colors="#6c7086", labelsize=7)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#45475a")
        ax.spines["bottom"].set_color("#45475a")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

        self._pnl_fig.tight_layout(pad=0.5)
        self._pnl_canvas.draw_idle()

    def update_daily_history(self, daily_data: list[dict]) -> None:
        """최근 N일 PnL 바 차트 업데이트."""
        if self._daily_ax is None or self._daily_canvas is None:
            return

        ax = self._daily_ax
        ax.clear()
        ax.set_facecolor("#313244")

        if not daily_data:
            self._setup_daily_empty_state()
            self._daily_canvas.draw_idle()
            return

        ax.axhline(y=0, color="#585b70", linewidth=0.5)
        ax.set_title("최근 5일 성과 (천원)", color="#6c7086", fontsize=8, loc="left", pad=2)

        dates = [d.get("date", "") for d in daily_data]
        values = [d.get("pnl", 0) / 1000 for d in daily_data]
        colors = ["#a6e3a1" if v >= 0 else "#f38ba8" for v in values]

        ax.bar(range(len(dates)), values, color=colors, width=0.6, alpha=0.8)
        ax.set_xticks(range(len(dates)))
        ax.set_xticklabels(dates, color="#6c7086", fontsize=7)
        ax.tick_params(colors="#6c7086", labelsize=7)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#45475a")
        ax.spines["bottom"].set_color("#45475a")

        self._daily_fig.tight_layout(pad=0.5)
        self._daily_canvas.draw_idle()

    def update_summary(self, data: dict) -> None:
        """Update summary bar."""
        pnl = data.get("daily_pnl", 0.0)
        pnl_pct = data.get("daily_pnl_pct", 0.0)
        pnl_color = "#a6e3a1" if pnl >= 0 else "#f38ba8"
        sign = "+" if pnl >= 0 else ""
        self._pnl_value.setText(f"{sign}{pnl:,.0f}")
        self._pnl_value.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {pnl_color};"
        )
        self._pnl_subtitle.setText(f"{sign}{pnl_pct:.2f}%")

        bar_value = max(-100, min(100, int(pnl_pct * 50)))
        self._pnl_bar.setValue(bar_value)
        self._pnl_bar.setStyleSheet(
            f"QProgressBar {{ background-color: #45475a; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background-color: {pnl_color}; border-radius: 2px; }}"
        )

        count = data.get("trades_count", 0)
        max_t = data.get("max_trades", 0)
        wins = data.get("wins", 0)
        losses = data.get("losses", 0)
        self._trades_value.setText(f"{count} / {max_t}")
        self._trades_subtitle.setText(f"Win {wins} / Loss {losses}")

        win_rate = data.get("win_rate", 0.0)
        avg = data.get("avg_win_rate", 0.0)
        self._winrate_value.setText(f"{win_rate:.1f}%")
        self._winrate_value.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #f9e2af;"
        )
        self._winrate_subtitle.setText(f"Avg: {avg:.1f}%")

        status = data.get("risk_status", "Normal")
        dd = data.get("dd_pct", 0.0)
        status_color = {
            "Normal": "#a6e3a1", "Warning": "#f9e2af", "Halted": "#f38ba8",
        }.get(status, "#a6e3a1")
        self._risk_value.setText(status)
        self._risk_value.setStyleSheet(
            f"font-size: 18px; font-weight: bold; color: {status_color};"
        )
        self._risk_subtitle.setText(f"DD: -{abs(dd):.2f}%")

    def update_watchlist(self, candidates: list[dict]) -> None:
        """감시 종목 테이블 업데이트."""
        table = self._watchlist_table
        table.setRowCount(0)
        self._watchlist_count_label.setText(f"{len(candidates)}종목")

        for row_data in candidates[:10]:
            row = table.rowCount()
            table.insertRow(row)

            name = row_data.get("name", "")
            ticker = row_data.get("ticker", "")
            ticker_text = f"{name}\n{ticker}" if name else ticker

            current_price = row_data.get("current_price", 0)
            change_pct = row_data.get("change_pct", 0)
            score = row_data.get("score", 0)

            price_text = f"{current_price:,.0f}" if current_price > 0 else "—"

            if current_price > 0:
                change_text = f"{change_pct:+.2f}%"
                change_color = QColor("#a6e3a1") if change_pct >= 0 else QColor("#f38ba8")
            else:
                change_text = "—"
                change_color = QColor("#6c7086")

            score_color = (
                QColor("#a6e3a1") if score >= 7
                else QColor("#f9e2af") if score >= 5
                else QColor("#6c7086")
            )

            cells = [
                (ticker_text, QColor("#89b4fa")),
                (price_text, None),
                (change_text, change_color),
                (f"{score:.1f}", score_color),
            ]

            for col, (text, color) in enumerate(cells):
                item = QTableWidgetItem(str(text))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if color is not None:
                    item.setForeground(color)
                table.setItem(row, col, item)

    def update_positions(self, positions: list[dict]) -> None:
        """Rebuild the active positions table."""
        table = self._positions_table
        table.setRowCount(0)
        self._positions_count_label.setText(f"{len(positions)} / 3")

        for row_data in positions:
            row = table.rowCount()
            table.insertRow(row)

            pnl_pct = row_data.get("pnl_pct", 0.0)
            pnl_color = QColor("#a6e3a1") if pnl_pct >= 0 else QColor("#f38ba8")

            ticker = row_data.get("ticker", "")
            name = row_data.get("name", "")
            ticker_text = f"{name}\n{ticker}" if name else ticker

            entry_time = row_data.get("entry_time")
            if entry_time:
                from datetime import datetime as _dt
                if isinstance(entry_time, str):
                    try:
                        entry_time = _dt.fromisoformat(entry_time)
                    except ValueError:
                        entry_time = None
            if entry_time:
                from datetime import datetime as _dt
                elapsed_min = int((_dt.now() - entry_time).total_seconds() / 60)
                time_limit = row_data.get("time_stop_minutes", 60)
                remaining = max(0, time_limit - elapsed_min)
                elapsed_text = f"{elapsed_min}m / {time_limit}m"
                elapsed_color = QColor("#f9e2af") if remaining <= 10 else QColor("#6c7086")
            else:
                elapsed_text = "—"
                elapsed_color = QColor("#6c7086")

            cells = [
                (ticker_text, QColor("#89b4fa")),
                (row_data.get("strategy", ""), None),
                (f"{'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}%", pnl_color),
                (elapsed_text, elapsed_color),
                (f"{row_data.get('stop_loss', 0):,.0f}", None),
                (f"{row_data.get('tp1_price', 0):,.0f}", None),
                (row_data.get("status", ""), None),
            ]

            for col, (text, color) in enumerate(cells):
                item = QTableWidgetItem(str(text))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if color is not None:
                    item.setForeground(color)
                table.setItem(row, col, item)

    def update_trades(self, trades: list[dict]) -> None:
        """Rebuild today's trades table."""
        table = self._trades_table
        table.setRowCount(0)

        for row_data in trades:
            row = table.rowCount()
            table.insertRow(row)

            # 시간 형식 정리 ("2026-04-03T15:19:01" → "15:19:01")
            raw_time = str(row_data.get("time", "") or row_data.get("traded_at", "") or "")
            if "T" in raw_time:
                time_text = raw_time.split("T")[1][:8]
            elif len(raw_time) > 8:
                time_text = raw_time[-8:]
            else:
                time_text = raw_time

            # 매매 구분 색상
            side = str(row_data.get("side", ""))
            side_color = QColor("#a6e3a1") if side.lower() == "buy" else QColor("#f38ba8")

            # 손익: 매수는 "—", 매도는 금액
            pnl = row_data.get("pnl")
            if side.lower() == "buy" or pnl is None:
                pnl_text = "—"
                pnl_color = QColor("#6c7086")
            else:
                pnl = int(pnl)
                pnl_text = f"{pnl:+,}"
                pnl_color = QColor("#a6e3a1") if pnl >= 0 else QColor("#f38ba8")

            # 사유: 전략명 또는 매도 사유
            reason = str(row_data.get("reason", "") or row_data.get("strategy", "") or "")

            cells = [
                (time_text, None),
                (str(row_data.get("ticker", "")), QColor("#89b4fa")),
                (side, side_color),
                (f"{int(row_data.get('price', 0) or 0):,}", None),
                (str(row_data.get("qty", 0) or 0), None),
                (pnl_text, pnl_color),
                (reason, None),
            ]

            for col, (text, color) in enumerate(cells):
                item = QTableWidgetItem(str(text))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if color is not None:
                    item.setForeground(color)
                table.setItem(row, col, item)
