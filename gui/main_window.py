"""메인 윈도우 — 좌측 사이드바 + 우측 5탭 레이아웃."""

import ctypes
import sys
from datetime import datetime
from pathlib import Path

import yaml
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
        self._load_config_to_ui()

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

        self.tabs.addTab(self.dashboard_tab, "대시보드")
        self.tabs.addTab(self.screener_tab, "스크리너")
        self.tabs.addTab(self.backtest_tab, "백테스트")
        self.tabs.addTab(self.strategy_tab, "전략 설정")
        self.tabs.addTab(self.log_tab, "로그")

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
        self.sidebar.strategy_changed.connect(self._on_strategy_changed)

        # Connect tab signals
        self.screener_tab.run_screening_clicked.connect(self._on_screening)
        self.strategy_tab.settings_saved.connect(self._on_settings_saved)
        self.backtest_tab.run_backtest_clicked.connect(self._on_run_backtest)

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

    def _on_strategy_changed(self, strategy: str):
        """전략 변경 → 엔진에 전달."""
        if self._worker:
            self._worker.signals.request_strategy_change.emit(strategy)

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
            # 안전장치: 7초 후에도 stopped 시그널 미수신 시 강제 복구
            QTimer.singleShot(7000, self._check_stop_timeout)

    def _check_stop_timeout(self):
        """Stop 요청 후 7초 경과 시 강제 UI 복구."""
        if self._stop_btn_pressed and self._worker:
            logger.warning("엔진 7초 내 미종료 — 강제 terminate")
            self._worker.terminate()
            self._worker.wait(2000)
            self._on_engine_stopped()

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

    def _on_settings_saved(self):
        """전략 탭 설정 저장 → config.yaml에 병합 기록."""
        config_path = Path("config.yaml")
        try:
            existing = yaml.safe_load(open(config_path, encoding="utf-8")) or {}
            new_values = self.strategy_tab.get_config()
            # strategy 섹션 병합
            existing.setdefault("strategy", {}).update(new_values.get("strategy", {}))
            # trading 섹션 병합
            existing.setdefault("trading", {}).update(new_values.get("trading", {}))
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(existing, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            # 유니버스 저장
            tickers = self.strategy_tab.get_universe()
            if tickers:
                uni_path = Path("config/universe.yaml")
                uni_data = {"stocks": [{"ticker": t, "name": ""} for t in tickers]}
                # 기존 이름 유지
                if uni_path.exists():
                    old_uni = yaml.safe_load(open(uni_path, encoding="utf-8")) or {}
                    name_map = {s["ticker"]: s.get("name", "") for s in old_uni.get("stocks", [])}
                    for s in uni_data["stocks"]:
                        s["name"] = name_map.get(s["ticker"], "")
                with open(uni_path, "w", encoding="utf-8") as f:
                    yaml.dump(uni_data, f, allow_unicode=True, default_flow_style=False)
            logger.info("config.yaml 저장 완료")
            QMessageBox.information(self, "저장 완료", "설정이 config.yaml에 저장되었습니다.")
        except Exception as e:
            logger.error(f"config.yaml 저장 실패: {e}")
            QMessageBox.critical(self, "저장 실패", str(e))

    def _on_run_backtest(self, params: dict):
        """백테스트 실행 (별도 스레드)."""
        import asyncio
        from threading import Thread

        strategy_name = params["strategy"]
        ticker = params["ticker"]
        start = params["start_date"]
        end = params["end_date"]

        self.backtest_tab.set_progress(10)

        def _run():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(
                    self._run_backtest_async(strategy_name, ticker, start, end)
                )
                loop.close()
                # UI 업데이트는 메인 스레드에서
                QTimer.singleShot(0, lambda: self._show_backtest_result(result))
            except Exception as e:
                QTimer.singleShot(0, lambda: self._show_backtest_error(str(e)))

        Thread(target=_run, daemon=True).start()

    async def _run_backtest_async(self, strategy_name, ticker, start, end):
        from config.settings import TradingConfig, BacktestConfig
        from data.db_manager import DbManager
        from backtest.backtester import Backtester
        from strategy.momentum_strategy import MomentumStrategy
        from strategy.pullback_strategy import PullbackStrategy
        from strategy.flow_strategy import FlowStrategy
        from strategy.gap_strategy import GapStrategy
        from strategy.open_break_strategy import OpenBreakStrategy
        from strategy.big_candle_strategy import BigCandleStrategy

        cfg_path = Path("config.yaml")
        raw = yaml.safe_load(open(cfg_path, encoding="utf-8")) or {}
        bt_cfg = raw.get("backtest", {})
        backtest_config = BacktestConfig(
            commission=bt_cfg.get("commission", 0.00015),
            tax=bt_cfg.get("tax", 0.0018),
            slippage=bt_cfg.get("slippage", 0.0003),
        )
        trading_config = TradingConfig()
        strategies = {
            "momentum": MomentumStrategy(trading_config),
            "pullback": PullbackStrategy(trading_config),
            "flow": FlowStrategy(trading_config),
            "gap": GapStrategy(trading_config),
            "openbreak": OpenBreakStrategy(trading_config),
            "bigcandle": BigCandleStrategy(trading_config),
        }
        strategy = strategies.get(strategy_name)
        if not strategy:
            return {"error": f"Unknown strategy: {strategy_name}"}

        db = DbManager("daytrader.db")
        await db.init()
        bt = Backtester(db=db, config=trading_config, backtest_config=backtest_config)
        kpi = await bt.run_multi_day(ticker, start, end, strategy)
        await db.close()
        return kpi

    def _show_backtest_result(self, result: dict):
        self.backtest_tab.set_progress(0)
        if "error" in result:
            QMessageBox.critical(self, "백테스트 오류", result["error"])
            return
        trades = result.get("trades", [])
        formatted_trades = []
        cum_pnl = 0
        for t in trades:
            cum_pnl += t.get("pnl", 0)
            entry_ts = t.get("entry_ts", "")
            date_str = entry_ts.strftime("%m-%d") if hasattr(entry_ts, "strftime") else str(entry_ts)[:5]
            time_str = entry_ts.strftime("%H:%M") if hasattr(entry_ts, "strftime") else str(entry_ts)[11:16]
            formatted_trades.append({
                "date": date_str,
                "time": time_str,
                "side": t.get("exit_reason", ""),
                "price": t.get("exit_price", 0),
                "qty": 0,
                "pnl": t.get("pnl", 0),
                "cumulative_pnl": cum_pnl,
            })
        total_pnl = result.get("total_pnl", 0)
        capital = 1_000_000
        self.backtest_tab.show_results(
            {
                "total_trades": result.get("total_trades", 0),
                "win_rate": result.get("win_rate", 0) * 100,
                "profit_factor": result.get("profit_factor", 0),
                "sharpe": result.get("sharpe_ratio", 0),
                "max_drawdown": result.get("max_drawdown", 0),
                "total_return": (total_pnl / capital) * 100 if capital else 0,
            },
            formatted_trades,
        )

    def _show_backtest_error(self, error: str):
        self.backtest_tab.set_progress(0)
        QMessageBox.critical(self, "백테스트 오류", error)

    def _load_config_to_ui(self):
        """config.yaml → 전략 탭 UI에 로드."""
        config_path = Path("config.yaml")
        try:
            cfg = yaml.safe_load(open(config_path, encoding="utf-8")) or {}
            self.strategy_tab.load_config(cfg)
            # 유니버스 로드
            uni_path = Path("config/universe.yaml")
            if uni_path.exists():
                uni = yaml.safe_load(open(uni_path, encoding="utf-8")) or {}
                tickers = [s["ticker"] for s in uni.get("stocks", [])]
                self.strategy_tab.load_universe(tickers)
                self.backtest_tab.set_tickers(tickers)
            # force_strategy → 사이드바 콤보 동기화
            force = cfg.get("strategy", {}).get("force", "")
            if force:
                combo = self.sidebar._strategy_combo
                for i in range(combo.count()):
                    if combo.itemText(i).lower() == force.lower():
                        combo.setCurrentIndex(i)
                        break
        except Exception as e:
            logger.warning(f"config UI 로드 실패: {e}")

    # ── 엔진 이벤트 핸들러 ────────────────────────────────────────────────────

    def _on_engine_started(self):
        mode = self.sidebar.get_mode()
        self._lbl_status_left.setText(f"Mode: {mode.upper()} | Engine: Running")
        self.sidebar.update_connection(True, True)
        # 콤보 선택값이 Auto가 아니면 엔진에 즉시 전달
        combo_text = self.sidebar._strategy_combo.currentText()
        if combo_text != "Auto":
            strategy = combo_text.lower()
            self._worker.signals.request_strategy_change.emit(strategy)

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
        # 연결 상태 업데이트
        ws_ok = status.get("ws_connected", False)
        rest_ok = status.get("running", False)  # REST는 엔진 실행 중이면 연결
        self.sidebar.update_connection(rest_ok, ws_ok)
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
        target = status.get("target_name") or status.get("target", "—")
        mode = self.sidebar.get_mode().upper()
        combo_text = self.sidebar._strategy_combo.currentText()
        force_tag = f" [{combo_text}]" if combo_text != "Auto" else ""
        self._lbl_status_left.setText(
            f"Mode: {mode}{force_tag} | Engine: Running | Strategy: {strategy} | Target: {target}"
        )

    def _on_positions_updated(self, positions: list):
        self.dashboard_tab.update_positions(positions)

    def _on_trades_updated(self, trades: list):
        self.dashboard_tab.update_trades(trades)

    def _on_pnl_updated(self, pnl: float):
        import time as _time
        self.dashboard_tab.update_pnl_chart(_time.time(), pnl)

    def _on_candidates_updated(self, candidates: list):
        self.screener_tab.update_candidates(candidates)
        self.dashboard_tab.update_watchlist(candidates[:5])

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
