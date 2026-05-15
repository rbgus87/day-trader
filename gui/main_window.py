"""메인 윈도우 — 헤더 바 + 사이드바 + 탭 레이아웃."""

import ctypes
import re
from datetime import datetime
from pathlib import Path

import yaml
from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QLabel, QMainWindow, QMessageBox,
    QTabWidget, QStatusBar, QHBoxLayout, QVBoxLayout, QWidget,
)
from loguru import logger

from gui.themes import dark_theme
from gui.tray_icon import TrayIcon
from gui.widgets.sidebar import Sidebar
from gui.views.header_bar import HeaderBar
from gui.views.dashboard_view import DashboardView
from gui.widgets.screener_tab import ScreenerTab
from gui.widgets.backtest_tab import BacktestTab
from gui.widgets.strategy_tab import StrategyTab
from gui.widgets.log_tab import LogTab
from gui.workers.engine_worker import EngineWorker


class MainWindow(QMainWindow):
    """DayTrader 메인 윈도우 (헤더 + 사이드바 + 탭)."""

    _log_signal = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("DayTrader")
        self.setMinimumSize(1100, 720)
        self.resize(1366, 860)

        self._worker: EngineWorker | None = None
        self._stop_btn_pressed = False
        self._current_positions: list = []
        self._max_positions = 3

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
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 상단 헤더 바
        self.header_bar = HeaderBar()
        root.addWidget(self.header_bar)

        # 바디: 좌측 사이드바 + 우측 탭
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.sidebar = Sidebar()
        body.addWidget(self.sidebar)

        # 우측 탭 위젯
        self.tab_widget = QTabWidget()
        self.dashboard_view = DashboardView()
        self.screener_tab = ScreenerTab()
        self.backtest_tab = BacktestTab()
        self.strategy_tab = StrategyTab()
        self.log_tab = LogTab()

        self.tab_widget.addTab(self.dashboard_view, "대시보드")
        self.tab_widget.addTab(self.screener_tab, "스크리너")
        self.tab_widget.addTab(self.backtest_tab, "백테스트")
        self.tab_widget.addTab(self.strategy_tab, "전략 설정")
        self.tab_widget.addTab(self.log_tab, "로그")
        body.addWidget(self.tab_widget, stretch=1)
        root.addLayout(body, stretch=1)

        # 하단 상태바
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._lbl_status_left = QLabel("Mode: PAPER | Engine: 정지됨")
        self._lbl_status_time = QLabel("")
        self.status_bar.addWidget(self._lbl_status_left, 1)
        self._lbl_rec_status = QLabel("")
        self._lbl_rec_status.setStyleSheet("color: #f38ba8; font-weight: bold;")
        self._lbl_rec_status.setVisible(False)
        self.status_bar.addPermanentWidget(self._lbl_rec_status)
        self.status_bar.addPermanentWidget(self._lbl_status_time)

        # 사이드바 시그널 연결
        self.sidebar.start_clicked.connect(self._on_start)
        self.sidebar.stop_clicked.connect(self._on_stop)
        self.sidebar.halt_clicked.connect(self._on_halt)
        self.sidebar.screening_clicked.connect(self._on_screening)
        self.sidebar.force_close_clicked.connect(self._on_force_close)
        self.sidebar.report_clicked.connect(self._on_report)
        self.sidebar.reconnect_clicked.connect(self._on_reconnect)
        self.sidebar.mode_changed.connect(self._on_mode_changed)
        self.sidebar.strategy_changed.connect(self._on_strategy_changed)
        self.sidebar.test_alert_clicked.connect(self._on_test_alert)
        self.sidebar.ws_record_toggled.connect(self._on_ws_record_toggled)

        # 탭 시그널 연결
        self.screener_tab.run_screening_clicked.connect(self._on_screening)
        self.strategy_tab.settings_saved.connect(self._on_settings_saved)
        self.backtest_tab.run_backtest_clicked.connect(self._on_run_backtest)
        self.backtest_tab.run_compare_clicked.connect(self._on_run_compare)

    # ── 테마 / 타이틀바 ───────────────────────────────────────────────────────

    def _apply_theme(self):
        self.setStyleSheet(dark_theme())

    def _apply_dark_titlebar(self):
        try:
            hwnd = int(self.winId())
            value = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(value), ctypes.sizeof(value),
            )
        except Exception:
            pass

    # ── 로그 싱크 ─────────────────────────────────────────────────────────────

    def _setup_loguru_sink(self):
        self._log_signal.connect(self._dispatch_log)

        try:
            logger.add(
                "logs/day.log",
                rotation="10 MB", retention=5, level="DEBUG",
                encoding="utf-8", compression="zip",
            )
        except Exception:
            pass

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
                rotation="5 MB", retention=10, level="INFO",
                encoding="utf-8", filter=_trade_filter, compression="zip",
            )
        except Exception:
            pass

        try:
            from utils.logging_config import setup_json_logging
            setup_json_logging("logs")
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
        self.dashboard_view.on_log_message(text, level)

    # ── 상태바 타이머 ─────────────────────────────────────────────────────────

    def _setup_refresh_timer(self):
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status_bar)
        self._timer.start(1000)

    def _refresh_status_bar(self):
        self._lbl_status_time.setText(datetime.now().strftime("%H:%M:%S"))

    # ── 엔진 제어 ─────────────────────────────────────────────────────────────

    def _on_mode_changed(self, mode: str):
        if mode == "live":
            reply = QMessageBox.warning(
                self, "실거래 모드",
                "실거래 모드는 실제 주문이 실행됩니다.\n계속하시겠습니까?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self.sidebar.revert_to_paper()

    def _on_strategy_changed(self, strategy: str):
        if self._worker:
            self._worker.signals.request_strategy_change.emit(strategy)

    def _on_start(self):
        mode = self.sidebar.get_mode()
        self._worker = EngineWorker(mode=mode)
        self._connect_worker_signals()
        self._worker.start()
        self.sidebar.set_engine_running(True)
        self._lbl_status_left.setText(f"Mode: {mode.upper()} | Engine: 시작 중...")

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
        s.market_status_updated.connect(self._on_market_status)
        s.trade_executed.connect(self._on_trade_executed)
        s.startup_progress.connect(self._on_startup_progress)
        s.ws_record_status.connect(self._on_ws_record_status)
        self.dashboard_view.manual_close_requested.connect(self._on_manual_close)

    def _on_market_status(self, kospi_strong: bool, kosdaq_strong: bool):
        self.header_bar.on_market_status(kospi_strong, kosdaq_strong)
        self.dashboard_view.on_market_status(kospi_strong, kosdaq_strong)

    def _on_stop(self):
        if not self._worker:
            return
        self._stop_btn_pressed = True
        self.sidebar.set_engine_running(False)
        self._lbl_status_left.setText(
            f"Mode: {self.sidebar.get_mode().upper()} | Engine: 정지 중..."
        )
        self._worker.signals.request_stop.emit()
        QTimer.singleShot(5000, self._stop_phase2)

    def _stop_phase2(self):
        if not self._worker or not self._worker.isRunning():
            return
        logger.warning("엔진 자연 종료 실패 — terminate fallback")
        self._worker.terminate()
        if not self._worker.wait(3000):
            logger.error("terminate 후 3초 내 미종료 — worker reference 유지")
            self.sidebar.set_engine_running(False)
            self._lbl_status_left.setText(
                f"Mode: {self.sidebar.get_mode().upper()} | Engine: Hung"
            )
            return

        import threading
        threading.Thread(target=self._send_stop_telegram, daemon=True).start()

        self._stop_btn_pressed = False
        self.sidebar.set_engine_running(False)
        self.sidebar.update_connection(False, False)
        self._lbl_status_left.setText(
            f"Mode: {self.sidebar.get_mode().upper()} | Engine: 정지됨"
        )
        self._worker = None

    def _send_stop_telegram(self):
        try:
            from config.settings import AppConfig
            from notification.telegram_bot import TelegramNotifier
            config = AppConfig.from_yaml()
            notifier = TelegramNotifier(config.telegram)
            mode_tag = "[PAPER] " if self.sidebar.get_mode() == "paper" else ""
            notifier.send(f"{mode_tag}시스템 종료 (GUI)", retries=1)
            notifier.aclose()
        except Exception:
            pass

    def _on_halt(self):
        pos_count = len(self._current_positions)
        pos_text = f"현재 보유 {pos_count}개 포지션은 유지됩니다." if pos_count > 0 else "현재 보유 포지션 없음."
        reply = QMessageBox.warning(
            self, "긴급 정지 확인",
            f"신규 매매를 즉시 중단합니다.\n{pos_text}\n\n계속하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes and self._worker:
            logger.warning("Halt 긴급 정지 확인됨")
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

    def _on_manual_close(self, ticker: str, name: str, qty: int):
        display = f"{name}({ticker})" if name and name != ticker else ticker
        reply = QMessageBox.warning(
            self, "수동 청산 확인",
            f"{display} {qty}주를\n시장가 매도하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes and self._worker:
            logger.info(f"수동 청산 요청: {ticker} {qty}주")
            self._worker.signals.request_manual_close.emit(ticker)

    def _on_report(self):
        logger.info("리포트 수동 발송 클릭")
        if self._worker:
            self._worker.signals.request_report.emit()

    def _on_reconnect(self):
        logger.info("WS 재연결 클릭")
        if self._worker:
            self._worker.signals.request_reconnect.emit()

    def _on_test_alert(self):
        from threading import Thread
        def _send():
            try:
                from config.settings import AppConfig
                from notification.telegram_bot import TelegramNotifier
                config = AppConfig.from_yaml()
                notifier = TelegramNotifier(config.telegram)
                notifier.send("[TEST] DayTrader 알림 테스트 정상", retries=1)
                notifier.aclose()
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda: QMessageBox.information(self, "알림 테스트", "텔레그램 발송 완료"))
            except Exception as e:
                err = str(e)
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda err=err: QMessageBox.critical(self, "알림 테스트 실패", err))
        Thread(target=_send, daemon=True).start()

    def _on_ws_record_toggled(self, enabled: bool) -> None:
        if self._worker:
            self._worker.signals.request_ws_record.emit(enabled)

    def _on_ws_record_status(self, recording: bool, count: int) -> None:
        if recording:
            self._lbl_rec_status.setText(f"● REC {count:,}건")
            self._lbl_rec_status.setVisible(True)
        else:
            self._lbl_rec_status.setVisible(False)
        self.sidebar.update_record_status(recording, count)

    def _on_settings_saved(self):
        config_path = Path("config.yaml")
        try:
            existing = yaml.safe_load(open(config_path, encoding="utf-8")) or {}
            new_values = self.strategy_tab.get_config()
            existing.setdefault("strategy", {}).update(new_values.get("strategy", {}))
            existing.setdefault("trading", {}).update(new_values.get("trading", {}))
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(existing, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            tickers = self.strategy_tab.get_universe()
            if tickers:
                uni_path = Path("config/universe.yaml")
                uni_data = {"stocks": [{"ticker": t, "name": ""} for t in tickers]}
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
                QTimer.singleShot(0, lambda: self._show_backtest_result(result))
            except Exception as e:
                err = str(e)
                QTimer.singleShot(0, lambda err=err: self._show_backtest_error(err))

        Thread(target=_run, daemon=True).start()

    async def _run_backtest_async(self, strategy_name, ticker, start, end):
        from config.settings import TradingConfig, BacktestConfig
        from data.db_manager import DbManager
        from backtest.backtester import Backtester
        from strategy.momentum_strategy import MomentumStrategy

        cfg_path = Path("config.yaml")
        raw = yaml.safe_load(open(cfg_path, encoding="utf-8")) or {}
        bt_cfg = raw.get("backtest", {})
        backtest_config = BacktestConfig(
            commission=bt_cfg.get("commission", 0.00015),
            tax=bt_cfg.get("tax", 0.0018),
            slippage=bt_cfg.get("slippage", 0.0003),
        )
        trading_config = TradingConfig()
        if strategy_name != "momentum":
            return {"error": f"Unknown strategy: {strategy_name} (momentum만 지원)"}
        strategy = MomentumStrategy(trading_config)
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
                "date": date_str, "time": time_str,
                "side": t.get("exit_reason", ""),
                "price": t.get("exit_price", 0),
                "qty": 0, "pnl": t.get("pnl", 0),
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
                err = str(e)
                QTimer.singleShot(0, lambda err=err: self._show_backtest_error(err))

        Thread(target=_run, daemon=True).start()

    async def _run_compare_async(self, params: dict) -> list[dict]:
        from config.settings import TradingConfig, BacktestConfig
        from data.db_manager import DbManager
        from backtest.backtester import Backtester
        from strategy.momentum_strategy import MomentumStrategy

        cfg_path = Path("config.yaml")
        raw = yaml.safe_load(open(cfg_path, encoding="utf-8")) or {}
        bt_cfg = raw.get("backtest", {})
        backtest_config = BacktestConfig(
            commission=bt_cfg.get("commission", 0.00015),
            tax=bt_cfg.get("tax", 0.0018),
            slippage=bt_cfg.get("slippage", 0.0003),
        )
        tc = TradingConfig()
        db = DbManager("daytrader.db")
        await db.init()
        bt = Backtester(db=db, config=tc, backtest_config=backtest_config)
        strategy = MomentumStrategy(tc)
        kpi = await bt.run_multi_day(
            params["ticker"], params["start_date"], params["end_date"], strategy,
        )
        pf = kpi.get("profit_factor", 0)
        results = [{
            "strategy": "Momentum",
            "total_trades": kpi.get("total_trades", 0),
            "win_rate": kpi.get("win_rate", 0),
            "profit_factor": pf,
            "total_pnl": kpi.get("total_pnl", 0),
            "verdict": "O" if pf >= 1.0 else "X",
        }]
        await db.close()
        return results

    def _show_compare_result(self, results: list[dict]):
        self.backtest_tab.set_progress(0)
        self.backtest_tab.show_compare_results(results)

    def _load_config_to_ui(self):
        config_path = Path("config.yaml")
        try:
            cfg = yaml.safe_load(open(config_path, encoding="utf-8")) or {}
            self.strategy_tab.load_config(cfg)
            uni_path = Path("config/universe.yaml")
            if uni_path.exists():
                uni = yaml.safe_load(open(uni_path, encoding="utf-8")) or {}
                stocks = uni.get("stocks", [])
                self.strategy_tab.load_universe(stocks)
                self.backtest_tab.set_tickers([s["ticker"] for s in stocks])
            force = cfg.get("strategy", {}).get("force", "")
            if force:
                self.sidebar.set_strategy(force)
        except Exception as e:
            logger.warning(f"config UI 로드 실패: {e}")

    # ── 엔진 이벤트 핸들러 ────────────────────────────────────────────────────

    def _on_startup_progress(self, stage: str, pct: int):
        mode = self.sidebar.get_mode().upper()
        self._lbl_status_left.setText(
            f"Mode: {mode} | 시작 중... [{pct}%] {stage}"
        )

    def _on_engine_started(self):
        mode = self.sidebar.get_mode()
        self._lbl_status_left.setText(f"Mode: {mode.upper()} | Engine: 실행 중")
        self.sidebar.update_connection(True, True)
        combo_text = self.sidebar.get_strategy()
        if combo_text and combo_text != "Auto":
            self._worker.signals.request_strategy_change.emit(combo_text.lower())
        self._update_tray_tooltip()

    def _on_engine_stopped(self):
        self._stop_btn_pressed = False
        self.sidebar.set_engine_running(False)
        self.sidebar.update_connection(False, False)
        self._lbl_status_left.setText(
            f"Mode: {self.sidebar.get_mode().upper()} | Engine: 정지됨"
        )
        self._worker = None
        self._update_tray_tooltip()

    def _on_engine_error(self, error: str):
        self.sidebar.set_engine_running(False)
        self._lbl_status_left.setText(
            f"Mode: {self.sidebar.get_mode().upper()} | Engine: 오류"
        )
        QMessageBox.critical(self, "엔진 오류", error)

    def _on_status_updated(self, status: dict):
        if getattr(self, "_stop_btn_pressed", False):
            return

        self.sidebar.update_status(status)
        ws_ok = status.get("ws_connected", False)
        rest_ok = status.get("running", False)
        self.sidebar.update_connection(rest_ok, ws_ok)

        self.header_bar.on_engine_status(status)
        self.dashboard_view.on_engine_status(status)
        self.dashboard_view.update_summary({
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

        strategy = status.get("strategy", "—")
        active = status.get("active_count", 0)
        pos_count = status.get("positions_count", 0)
        max_pos = status.get("max_positions", 3)
        self._max_positions = max_pos
        available = int(status.get("available_capital", 0) or 0)
        mode = self.sidebar.get_mode().upper()
        combo_text = self.sidebar.get_strategy()
        force_tag = f" [{combo_text}]" if combo_text not in ("Auto", "") else ""
        self._lbl_status_left.setText(
            f"Mode: {mode}{force_tag} | Strategy: {strategy} | "
            f"감시: {active}종목 | 포지션: {pos_count}/{max_pos} | 가용: {available:,}원"
        )

    def _on_positions_updated(self, positions: list):
        self._current_positions = positions
        self.dashboard_view.update_positions(positions)

    def _on_trades_updated(self, trades: list):
        self.dashboard_view.update_trades(trades)

    def _on_pnl_updated(self, pnl: float):
        import time as _time
        self.dashboard_view.update_pnl_chart(_time.time(), pnl)
        capital = None
        if self._worker and self._worker._risk_manager:
            capital = self._worker._risk_manager._daily_capital
        self.header_bar.on_daily_pnl(pnl, capital)
        self.dashboard_view.on_daily_loss(pnl, capital)

    def _on_candidates_updated(self, candidates: list):
        self.screener_tab.update_candidates(candidates)

    def _on_watchlist_updated(self, items: list):
        self.dashboard_view.update_watchlist(items)

    def _on_trade_executed(self, trade: dict):
        self.dashboard_view.on_trade_executed(trade)

    # ── 트레이 ────────────────────────────────────────────────────────────────

    def _setup_tray(self):
        self._tray = TrayIcon(self)
        self._tray.show_requested.connect(self._tray_show)
        self._tray.toggle_requested.connect(self._tray_toggle)
        self._tray.quit_requested.connect(self._tray_quit)
        self._tray.stop_requested.connect(self._on_stop)
        self._tray.tray.show()
        self._tray_tooltip_timer = QTimer(self)
        self._tray_tooltip_timer.timeout.connect(self._update_tray_tooltip)
        self._tray_tooltip_timer.start(60_000)

    def _tray_show(self):
        self.showNormal()
        self.activateWindow()

    def _tray_toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.showNormal()
            self.activateWindow()

    def _update_tray_tooltip(self):
        mode = self.sidebar.get_mode().upper()
        engine_state = "엔진 실행 중" if (self._worker and self._worker.isRunning()) else "엔진 정지됨"
        pos_count = len(self._current_positions)
        self._tray.tray.setToolTip(
            f"DayTrader — {mode} | {engine_state} | 포지션 {pos_count}/{self._max_positions}"
        )

    def _tray_quit(self):
        self._cleanup_and_quit()

    # ── 종료 처리 ─────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            event.ignore()
            self.hide()
            self._tray.tray.showMessage(
                "DayTrader",
                "엔진이 구동 중입니다. 트레이에서 실행됩니다.",
                self._tray.tray.MessageIcon.Information,
                2000,
            )
            return

        reply = QMessageBox.question(
            self, "DayTrader",
            "프로그램을 종료하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            event.accept()
            self._cleanup_and_quit()
        else:
            event.ignore()

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
