"""gui/views/dashboard_view.py — 실시간 대시보드 뷰 (Phase 2 재구성)."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from gui.views.dashboard.market_status_panel import MarketStatusPanel
from gui.views.dashboard.equity_chart import EquityChart
from gui.views.dashboard.positions_panel import PositionsPanel
from gui.views.dashboard.watchlist_panel import WatchlistPanel
from gui.views.dashboard.signal_timeline import SignalTimeline
from gui.views.dashboard.orderbook_widget import OrderbookWidget
from gui.views.dashboard.trades_panel import TradesPanel


class DashboardView(QWidget):
    """대시보드 메인 뷰.

    레이아웃:
        ┌──────────────────────────────────────────────┐
        │ MarketStatusPanel (56px 고정)                 │
        ├─────────────────────┬────────────────────────┤
        │ PositionsPanel (카드)│ EquityChart + OB Widget│
        │ WatchlistPanel      │ SignalTimeline          │
        ├─────────────────────┴────────────────────────┤
        │ TradesPanel (당일 체결)                       │
        └──────────────────────────────────────────────┘
    """

    manual_close_requested = pyqtSignal(str, str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── 1. 시장 상태 패널 ─────────────────────────────────────────────
        self._market_status = MarketStatusPanel()
        root.addWidget(self._market_status)

        # ── 2. 중간 영역: 좌우 분할 ──────────────────────────────────────
        mid_splitter = QSplitter(Qt.Orientation.Horizontal)
        mid_splitter.setHandleWidth(6)
        mid_splitter.setStyleSheet(
            "QSplitter::handle { background-color: #313244; }"
        )
        mid_splitter.setChildrenCollapsible(False)

        # 좌측 컬럼: 포지션 카드 + 감시종목
        left_splitter = QSplitter(Qt.Orientation.Vertical)
        left_splitter.setHandleWidth(6)
        left_splitter.setStyleSheet(
            "QSplitter::handle { background-color: #313244; }"
        )
        left_splitter.setChildrenCollapsible(False)
        self._positions = PositionsPanel()
        self._positions.manual_close_requested.connect(self.manual_close_requested)
        self._watchlist = WatchlistPanel()
        left_splitter.addWidget(self._positions)
        left_splitter.addWidget(self._watchlist)
        left_splitter.setSizes([280, 220])

        # 우측 컬럼: Equity Curve + SignalTimeline + OrderbookWidget
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.setHandleWidth(6)
        right_splitter.setStyleSheet(
            "QSplitter::handle { background-color: #313244; }"
        )
        right_splitter.setChildrenCollapsible(False)
        self._equity_chart = EquityChart()
        self._signal_timeline = SignalTimeline()
        self._orderbook = OrderbookWidget()
        right_splitter.addWidget(self._equity_chart)
        right_splitter.addWidget(self._signal_timeline)
        right_splitter.addWidget(self._orderbook)
        right_splitter.setSizes([250, 180, 80])

        mid_splitter.addWidget(left_splitter)
        mid_splitter.addWidget(right_splitter)
        mid_splitter.setSizes([480, 520])
        root.addWidget(mid_splitter, stretch=1)

        # ── 3. 당일 체결 (하단 전폭) ────────────────────────────────────
        self._trades = TradesPanel()
        root.addWidget(self._trades)

    # ── 공개 API (main_window 호환) ──────────────────────────────────────────

    def on_engine_status(self, status: dict) -> None:
        max_pos = int(status.get("max_positions", 3) or 3)
        self._positions.set_max_positions(max_pos)
        pos_count = int(status.get("positions_count", 0) or 0)
        self._market_status.update_slot(pos_count, max_pos)

    def on_market_status(self, kospi_strong: bool, kosdaq_strong: bool) -> None:
        self._market_status.update_market_status(kospi_strong, kosdaq_strong)

    def on_daily_loss(self, pnl: float, capital: float | None = None) -> None:
        """HeaderBar가 처리 — no-op (호환성 유지)."""

    def update_summary(self, data: dict) -> None:
        """KPI 데이터 — VI 수, 슬롯은 market_status_panel에 반영."""
        pos_count = int(data.get("open_positions_count", 0) or 0)
        max_pos = int(data.get("initial_capital", 0) or 0)  # fallback — slot은 on_engine_status에서
        vi_count = int(data.get("vi_count", 0) or 0)
        self._market_status.update_vi_count(vi_count)

    def update_pnl_chart(self, timestamp: float, value: float) -> None:
        self._equity_chart.add_pnl(timestamp, value)

    def update_positions(self, positions: list[dict]) -> None:
        self._positions.update_positions(positions)
        # 포지션 보유 중이면 첫 번째 종목의 호가 표시 (데이터 없음 상태 유지)
        if positions:
            self._orderbook.set_no_data(positions[0].get("ticker", ""))
        else:
            self._orderbook.set_no_data()

    def update_watchlist(self, items: list[dict]) -> None:
        self._watchlist.update_watchlist(items)

    def update_trades(self, trades: list[dict]) -> None:
        self._trades.update_trades(trades)

    def on_trade_executed(self, trade: dict) -> None:
        """trade_executed 시그널 → Equity Curve 마커 + Signal Timeline."""
        self._equity_chart.add_trade_marker(trade)
        self._signal_timeline.add_trade(trade)

    def on_log_message(self, text: str, level: str) -> None:
        """loguru 로그 → Signal Timeline 차단 이벤트 필터링."""
        self._signal_timeline.add_log(text, level)
