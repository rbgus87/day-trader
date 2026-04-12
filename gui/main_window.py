"""메인 윈도우 — 좌측 사이드바 + 우측 5탭 레이아웃."""

import ctypes
import re
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
        self._lbl_market_status = QLabel("시장: -")
        self.status_bar.addWidget(self._lbl_status_left, 1)
        self.status_bar.addPermanentWidget(self._lbl_market_status)
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
        self.backtest_tab.run_compare_clicked.connect(self._on_run_compare)

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

        # 파일 로깅 (main.py와 동일 정책)
        try:
            logger.add(
                "logs/day.log",
                rotation="10 MB",
                retention=5,
                level="DEBUG",
                encoding="utf-8",
                compression="zip",
            )
        except Exception:
            pass

        # 매매 전용 로그 (WS 트래픽에 밀리지 않도록 분리)
        try:
            _trade_kw = re.compile(
                r"매수|매도|체결|주문|청산|손절|TP1|트레일링|신호|포지션|손익|PnL|"
                r"승률|TRADE-LIMIT|일일 실적|일일 손실|PAPER",
                re.IGNORECASE,
            )

            def _trade_filter(record):
                return bool(_trade_kw.search(record["message"]))

            logger.add(
                "logs/trade.log",
                rotation="5 MB",
                retention=10,
                level="INFO",
                encoding="utf-8",
                filter=_trade_filter,
                compression="zip",
            )
        except Exception:
            pass

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
        s.watchlist_updated.connect(self._on_watchlist_updated)
        s.daily_history_updated.connect(self._on_daily_history)
        s.market_status_updated.connect(self._on_market_status)

    def _on_market_status(self, kospi_strong: bool, kosdaq_strong: bool):
        """Phase 3 Day 12+: 시장필터 갱신 수신 → 상태바 + 대시보드."""
        k = "강세" if kospi_strong else "약세"
        q = "강세" if kosdaq_strong else "약세"
        self._lbl_market_status.setText(f"  시장: KOSPI {k} | KOSDAQ {q}  ")
        # 대시보드 상태 스트립에도 전파
        if hasattr(self, "dashboard_tab") and hasattr(self.dashboard_tab, "on_market_status"):
            self.dashboard_tab.on_market_status(kospi_strong, kosdaq_strong)

    def _on_stop(self):
        if not self._worker:
            return

        self._stop_btn_pressed = True
        self.sidebar._stop_btn.setEnabled(False)
        self.sidebar._start_btn.setEnabled(False)
        self._lbl_status_left.setText(
            f"Mode: {self.sidebar.get_mode().upper()} | Engine: Stopping..."
        )

        # 1단계: _running 플래그 해제 (worker가 감지하면 자연 종료)
        if self._worker:
            self._worker._running = False

        # 2단계: 2초 대기 — 자연 종료되면 stopped 시그널이 옴
        QTimer.singleShot(2000, self._stop_phase2)

    def _stop_phase2(self):
        """2초 후 — 자연 종료 안 됐으면 terminate."""
        if not self._worker or not self._worker.isRunning():
            return

        logger.info("엔진 정지 완료 (terminate)")
        self._worker.terminate()
        self._worker.wait(1000)  # 최대 1초만 대기

        # 텔레그램은 별도 스레드에서 (UI 블로킹 없음)
        import threading
        threading.Thread(target=self._send_stop_telegram, daemon=True).start()

        # UI 즉시 복구
        self._stop_btn_pressed = False
        self.sidebar.set_engine_running(False)
        self.sidebar.update_connection(False, False)
        self._lbl_status_left.setText(
            f"Mode: {self.sidebar.get_mode().upper()} | Engine: Stopped"
        )
        self._worker = None

    def _send_stop_telegram(self):
        """별도 스레드에서 텔레그램 발송 (UI 블로킹 없음)."""
        import asyncio
        try:
            loop = asyncio.new_event_loop()
            async def _send():
                from config.settings import AppConfig
                from notification.telegram_bot import TelegramNotifier
                config = AppConfig.from_yaml()
                notifier = TelegramNotifier(config.telegram)
                mode_tag = "[PAPER] " if self.sidebar.get_mode() == "paper" else ""
                await notifier.send(f"{mode_tag}시스템 종료 (GUI)")
                await notifier.aclose()
            loop.run_until_complete(asyncio.wait_for(_send(), timeout=5.0))
            loop.close()
        except Exception:
            pass

    def _on_halt(self):
        logger.info("Halt 긴급 클릭")
        if self._worker:
            self._worker.signals.request_halt.emit()

    def _on_screening(self):
        logger.info("수동 스크리닝 클릭")
        if self._worker:
            self._worker.signals.request_screening.emit()

    def _on_force_close(self):
        logger.info("강제청산 클릭")
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
        logger.info("리포트 수동 발송 클릭")
        if self._worker:
            self._worker.signals.request_report.emit()

    def _on_reconnect(self):
        logger.info("WS 재연결 클릭")
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

    def _on_run_compare(self, params: dict):
        """6전략 비교 백테스트 실행."""
        import asyncio
        from threading import Thread

        self.backtest_tab.set_progress(50)

        def _run():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(self._run_compare_async(params))
                loop.close()
                QTimer.singleShot(0, lambda: self._show_compare_result(result))
            except Exception as e:
                QTimer.singleShot(0, lambda: self._show_backtest_error(str(e)))

        Thread(target=_run, daemon=True).start()

    async def _run_compare_async(self, params: dict) -> list[dict]:
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
        tc = TradingConfig()
        strategies = {
            "Momentum": MomentumStrategy(tc),
            "Pullback": PullbackStrategy(tc),
            "Flow": FlowStrategy(tc),
            "Gap": GapStrategy(tc),
            "OpenBreak": OpenBreakStrategy(tc),
            "BigCandle": BigCandleStrategy(tc),
        }
        db = DbManager("daytrader.db")
        await db.init()
        bt = Backtester(db=db, config=tc, backtest_config=backtest_config)
        results = []
        for name, strategy in strategies.items():
            kpi = await bt.run_multi_day(
                params["ticker"], params["start_date"], params["end_date"], strategy,
            )
            pf = kpi.get("profit_factor", 0)
            results.append({
                "strategy": name,
                "total_trades": kpi.get("total_trades", 0),
                "win_rate": kpi.get("win_rate", 0),
                "profit_factor": pf,
                "total_pnl": kpi.get("total_pnl", 0),
                "verdict": "O" if pf >= 1.0 else "X",
            })
        await db.close()
        results.sort(key=lambda x: x["profit_factor"], reverse=True)
        return results

    def _show_compare_result(self, results: list[dict]):
        self.backtest_tab.set_progress(0)
        self.backtest_tab.show_compare_results(results)

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
                stocks = uni.get("stocks", [])
                self.strategy_tab.load_universe(stocks)
                self.backtest_tab.set_tickers([s["ticker"] for s in stocks])
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
        self._lbl_status_left.setText(
            f"Mode: {self.sidebar.get_mode().upper()} | Engine: Stopped"
        )
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
            "open_positions_count": status.get("open_positions_count", 0),
            "wins": status.get("wins", 0),
            "losses": status.get("losses", 0),
            "win_rate": status.get("win_rate", 0),
            "avg_win_rate": status.get("avg_win_rate", 0),
            "risk_status": "Halted" if status.get("halted") else "Normal",
            "dd_pct": status.get("dd_pct", 0),
            "available_capital": status.get("available_capital", 0),
            "initial_capital": status.get("initial_capital", 0),
        })
        # Update status bar
        strategy = status.get("strategy", "—")
        active = status.get("active_count", 0)
        pos_count = status.get("positions_count", 0)
        max_pos = status.get("max_positions", 3)
        mode = self.sidebar.get_mode().upper()
        combo_text = self.sidebar._strategy_combo.currentText()
        force_tag = f" [{combo_text}]" if combo_text != "Auto" else ""
        capital = status.get("available_capital", 0)
        self._lbl_status_left.setText(
            f"Mode: {mode}{force_tag} | Engine: Running | "
            f"Strategy: {strategy} | 감시: {active}종목 | 포지션: {pos_count}/{max_pos} | "
            f"가용: {int(capital):,}원"
        )

    def _on_positions_updated(self, positions: list):
        self.dashboard_tab.update_positions(positions)

    def _on_trades_updated(self, trades: list):
        self.dashboard_tab.update_trades(trades)

    def _on_pnl_updated(self, pnl: float):
        import time as _time
        self.dashboard_tab.update_pnl_chart(_time.time(), pnl)
        # Phase 3 Day 12+ Level 1: 일일손실 라벨 갱신
        if hasattr(self.dashboard_tab, "on_daily_loss"):
            capital = None
            if self._worker and self._worker._risk_manager:
                capital = self._worker._risk_manager._daily_capital
            self.dashboard_tab.on_daily_loss(pnl, capital)

    def _on_candidates_updated(self, candidates: list):
        self.screener_tab.update_candidates(candidates)

    def _on_watchlist_updated(self, items: list):
        self.dashboard_tab.update_watchlist(items)

    def _on_daily_history(self, data: list):
        self.dashboard_tab.update_daily_history(data)

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
            self._worker._running = False
            if not self._worker.wait(2000):
                logger.warning("종료 시 EngineWorker 2초 내 미종료 — terminate")
                self._worker.terminate()
                self._worker.wait(1000)
        self._worker = None

        self._tray.tray.hide()
        QApplication.quit()
