"""메인 윈도우 — 좌측 사이드바 + 우측 5탭 레이아웃."""

import ctypes
import sys
from datetime import datetime

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QStatusBar, QTabWidget, QVBoxLayout, QWidget,
)
from loguru import logger

from gui.themes import dark_theme
from gui.tray_icon import TrayIcon
from gui.widgets.sidebar import Sidebar
from gui.widgets.dashboard_tab import DashboardTab
from gui.widgets.screener_tab import ScreenerTab
from gui.widgets.backtest_tab import BacktestTab
from gui.widgets.strategy_tab import StrategyTab
from gui.widgets.log_tab import LogTab
from gui.workers.engine_worker import EngineWorker


class MainWindow(QMainWindow):
    """DayTrader 메인 윈도우."""

    _log_signal = pyqtSignal(str, str)  # text, level

    def __init__(self):
        super().__init__()
        self.setWindowTitle("DayTrader")
        self.setMinimumSize(1100, 720)
        self.resize(1280, 800)

        self._worker: EngineWorker | None = None
        self._stop_btn_pressed = False

        self._init_ui()
        self._apply_theme()
        self._apply_dark_titlebar()
        self._setup_tray()
        self._setup_loguru_sink()
        self._setup_refresh_timer()

    # ── UI 초기화 ─────────────────────────────────────────────────────────────

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Left sidebar
        self.sidebar = Sidebar()
        root.addWidget(self.sidebar)

        # Right content area
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)

        # Tab widget
        self.tabs = QTabWidget()
        self.dashboard_tab = DashboardTab()
        self.screener_tab = ScreenerTab()
        self.backtest_tab = BacktestTab()
        self.strategy_tab = StrategyTab()
        self.log_tab = LogTab()

        self.tabs.addTab(self.dashboard_tab, "Dashboard")
        self.tabs.addTab(self.screener_tab, "Screener")
        self.tabs.addTab(self.backtest_tab, "Backtest")
        self.tabs.addTab(self.strategy_tab, "Strategy")
        self.tabs.addTab(self.log_tab, "Log")

        right.addWidget(self.tabs)
        root.addLayout(right, stretch=1)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._lbl_status_left = QLabel("Mode: PAPER | Engine: Stopped")
        self._lbl_status_time = QLabel()
        self.status_bar.addWidget(self._lbl_status_left, 1)
        self.status_bar.addPermanentWidget(self._lbl_status_time)

        # Connect sidebar signals
        self.sidebar.start_clicked.connect(self._on_start)
        self.sidebar.stop_clicked.connect(self._on_stop)
        self.sidebar.halt_clicked.connect(self._on_halt)
        self.sidebar.screening_clicked.connect(self._on_screening)
        self.sidebar.force_close_clicked.connect(self._on_force_close)
        self.sidebar.report_clicked.connect(self._on_report)
        self.sidebar.reconnect_clicked.connect(self._on_reconnect)
        self.sidebar.mode_changed.connect(self._on_mode_changed)

    # ── 테마 / 타이틀바 ───────────────────────────────────────────────────────

    def _apply_theme(self):
        self.setStyleSheet(dark_theme())

    def _apply_dark_titlebar(self):
        """Windows 타이틀바를 다크 모드로 변경."""
        try:
            hwnd = int(self.winId())
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            value = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(value), ctypes.sizeof(value),
            )
        except Exception:
            pass

    # ── 로그 싱크 ─────────────────────────────────────────────────────────────

    def _setup_loguru_sink(self):
        self._log_signal.connect(self._dispatch_log)

        def gui_sink(message):
            record = message.record
            level = record["level"].name
            time_str = record["time"].strftime("%H:%M:%S")
            text = f"[{time_str}] {level:8s} {record['message']}"
            self._log_signal.emit(text, level)

        self._loguru_sink_id = logger.add(gui_sink, level="DEBUG", format="{message}")

    def _dispatch_log(self, text: str, level: str):
        self.log_tab.append_log(text, level)

    # ── 상태바 타이머 ─────────────────────────────────────────────────────────

    def _setup_refresh_timer(self):
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status_bar)
        self._timer.start(1000)

    def _refresh_status_bar(self):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S KST")
        self._lbl_status_time.setText(f"  {now}  ")

    # ── 엔진 제어 ─────────────────────────────────────────────────────────────

    def _on_mode_changed(self, mode: str):
        """LIVE 모드 전환 시 확인 다이얼로그."""
        if mode == "live":
            reply = QMessageBox.warning(
                self, "실거래 모드",
                "실거래 모드는 실제 주문이 실행됩니다.\n계속하시겠습니까?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                # Revert to paper
                self.sidebar._paper_btn.setChecked(True)
                self.sidebar._on_paper_clicked()

    def _on_start(self):
        mode = self.sidebar.get_mode()
        self._worker = EngineWorker(mode=mode)
        self._connect_worker_signals()
        self._worker.start()
        self.sidebar.set_engine_running(True)
        self._lbl_status_left.setText(f"Mode: {mode.upper()} | Engine: Starting...")

    def _connect_worker_signals(self):
        s = self._worker.signals
        s.started.connect(self._on_engine_started)
        s.stopped.connect(self._on_engine_stopped)
        s.error.connect(self._on_engine_error)
        s.status_updated.connect(self._on_status_updated)
        s.positions_updated.connect(self._on_positions_updated)
        s.trades_updated.connect(self._on_trades_updated)
        s.pnl_updated.connect(self._on_pnl_updated)
        s.candidates_updated.connect(self._on_candidates_updated)

    def _on_stop(self):
        if self._worker:
            # 즉각적 UI 피드백: 버튼 비활성 + 상태 표시
            self._stop_btn_pressed = True
            self.sidebar._stop_btn.setEnabled(False)
            self.sidebar._start_btn.setEnabled(False)
            self._lbl_status_left.setText(
                f"Mode: {self.sidebar.get_mode().upper()} | Engine: Stopping..."
            )
            self._worker.signals.request_stop.emit()

    def _on_halt(self):
        if self._worker:
            self._worker.signals.request_halt.emit()

    def _on_screening(self):
        if self._worker:
            self._worker.signals.request_screening.emit()

    def _on_force_close(self):
        if self._worker:
            reply = QMessageBox.warning(
                self, "강제 청산",
                "모든 포지션을 즉시 청산합니다.\n계속하시겠습니까?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._worker.signals.request_force_close.emit()

    def _on_report(self):
        if self._worker:
            self._worker.signals.request_report.emit()

    def _on_reconnect(self):
        if self._worker:
            self._worker.signals.request_reconnect.emit()

    # ── 엔진 이벤트 핸들러 ────────────────────────────────────────────────────

    def _on_engine_started(self):
        mode = self.sidebar.get_mode()
        self._lbl_status_left.setText(f"Mode: {mode.upper()} | Engine: Running")
        self.sidebar.update_connection(True, True)

    def _on_engine_stopped(self):
        self._stop_btn_pressed = False
        self.sidebar.set_engine_running(False)
        self.sidebar.update_connection(False, False)
        self._lbl_status_left.setText(f"Mode: {self.sidebar.get_mode().upper()} | Engine: Stopped")
        self._worker = None

    def _on_engine_error(self, error: str):
        # 에러 시에도 UI 상태 복구 (stopped 시그널이 뒤따라오지만 안전 차원)
        self.sidebar.set_engine_running(False)
        self._lbl_status_left.setText(
            f"Mode: {self.sidebar.get_mode().upper()} | Engine: Error"
        )
        QMessageBox.critical(self, "엔진 오류", error)

    def _on_status_updated(self, status: dict):
        # 정지 요청 중이면 상태바 덮어쓰기 방지
        if getattr(self, "_stop_btn_pressed", False):
            return

        self.sidebar.update_status(status)
        # Update dashboard summary
        self.dashboard_tab.update_summary({
            "daily_pnl": status.get("daily_pnl", 0),
            "daily_pnl_pct": status.get("daily_pnl_pct", 0),
            "trades_count": status.get("trades_count", 0),
            "max_trades": status.get("max_trades", 5),
            "wins": status.get("wins", 0),
            "losses": status.get("losses", 0),
            "win_rate": status.get("win_rate", 0),
            "avg_win_rate": status.get("avg_win_rate", 0),
            "risk_status": "Halted" if status.get("halted") else "Normal",
            "dd_pct": status.get("dd_pct", 0),
        })
        # Update status bar
        strategy = status.get("strategy", "—")
        target = status.get("target_name", "—")
        mode = self.sidebar.get_mode().upper()
        self._lbl_status_left.setText(
            f"Mode: {mode} | Engine: Running | Strategy: {strategy} | Target: {target}"
        )

    def _on_positions_updated(self, positions: list):
        self.dashboard_tab.update_positions(positions)

    def _on_trades_updated(self, trades: list):
        self.dashboard_tab.update_trades(trades)

    def _on_pnl_updated(self, pnl: float):
        pass  # Already handled via status_updated

    def _on_candidates_updated(self, candidates: list):
        self.screener_tab.update_candidates(candidates)

    # ── 트레이 ────────────────────────────────────────────────────────────────

    def _setup_tray(self):
        self._tray = TrayIcon(self)
        self._tray.show_requested.connect(self._tray_show)
        self._tray.quit_requested.connect(self._tray_quit)
        self._tray.stop_requested.connect(self._on_stop)

    def _tray_show(self):
        self.showNormal()
        self.activateWindow()

    def _tray_quit(self):
        self._cleanup_and_quit()

    # ── 종료 처리 ─────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            event.ignore()
            self.hide()
            self._tray.tray.show()
            self._tray.tray.showMessage(
                "DayTrader",
                "엔진이 구동 중입니다. 트레이에서 실행됩니다.",
                self._tray.tray.MessageIcon.Information,
                2000,
            )
        else:
            event.accept()
            self._cleanup_and_quit()

    def _cleanup_and_quit(self):
        if getattr(self, "_cleanup_done", False):
            return
        self._cleanup_done = True

        self._timer.stop()

        if hasattr(self, "_loguru_sink_id"):
            try:
                logger.remove(self._loguru_sink_id)
            except ValueError:
                pass

        if self._worker and self._worker.isRunning():
            self._worker.signals.request_stop.emit()
            if not self._worker.wait(5000):
                logger.warning("EngineWorker 5초 내 미종료 — 강제 terminate")
                self._worker.terminate()
                self._worker.wait(2000)
        self._worker = None

        self._tray.tray.hide()
        QApplication.quit()
