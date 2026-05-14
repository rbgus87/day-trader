"""gui/views/dashboard_view.py — 대시보드 뷰 (패널 조합, DashboardTab 동일 공개 API)."""
from __future__ import annotations

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QSplitter
from PyQt6.QtCore import Qt

from gui.views.dashboard.metrics_panel import MetricsPanel
from gui.views.dashboard.positions_panel import PositionsPanel
from gui.views.dashboard.watchlist_panel import WatchlistPanel
from gui.views.dashboard.trades_panel import TradesPanel


class DashboardView(QWidget):
    """대시보드 메인 뷰 — 4개 패널 조합.

    공개 API는 기존 DashboardTab 과 동일하게 유지하여 main_window.py 무변경.
    status strip(시간/매수차단/KOSPI/KOSDAQ/PnL/자본)은 HeaderBar로 이전됨.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # 상단: KPI 카드 4개 + PnL 차트
        self._metrics = MetricsPanel()
        root.addWidget(self._metrics)

        # 보유 포지션 (고정 높이)
        self._positions = PositionsPanel()
        root.addWidget(self._positions)

        # 하단: 감시종목(좌) + 당일 체결(우), 스플리터
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(10)
        splitter.setStyleSheet("QSplitter::handle { background-color: transparent; }")
        self._watchlist = WatchlistPanel()
        self._trades = TradesPanel()
        splitter.addWidget(self._watchlist)
        splitter.addWidget(self._trades)
        splitter.setSizes([500, 500])
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, stretch=1)

    # ── 공개 API (DashboardTab 호환) ─────────────────────────────────────────

    def on_engine_status(self, status: dict) -> None:
        """포지션 수/최대 포지션 → 포지션 패널 타이틀 갱신."""
        max_pos = int(status.get("max_positions", 3) or 3)
        self._positions.set_max_positions(max_pos)

    def on_market_status(self, kospi_strong: bool, kosdaq_strong: bool) -> None:
        """HeaderBar가 처리하므로 여기서는 no-op (호환성 유지)."""

    def on_daily_loss(self, pnl: float, capital: float | None = None) -> None:
        """HeaderBar가 처리하므로 여기서는 no-op (호환성 유지)."""

    def update_summary(self, data: dict) -> None:
        self._metrics.update_summary(data)

    def update_pnl_chart(self, timestamp: float, value: float) -> None:
        self._metrics.update_pnl_chart(timestamp, value)

    def update_positions(self, positions: list[dict]) -> None:
        self._positions.update_positions(positions)

    def update_watchlist(self, items: list[dict]) -> None:
        self._watchlist.update_watchlist(items)

    def update_trades(self, trades: list[dict]) -> None:
        self._trades.update_trades(trades)
