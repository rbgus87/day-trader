"""TradingEngine을 별도 스레드에서 asyncio로 실행하는 QThread 래퍼.

매매 파이프라인(tick/candle/signal/order consumer + APScheduler)을
QThread 내 asyncio 이벤트 루프에서 실행.
모든 cross-thread 호출은 Qt signal 또는 asyncio.run_coroutine_threadsafe로 처리.
"""
import asyncio
import sys
from datetime import datetime, time as dt_time

from PyQt6.QtCore import QThread
from loguru import logger

from gui.workers.signals import EngineSignals
from pipeline.trading_state import TradingState
from pipeline.ui_emitter import UIEmitter


class EngineWorker(QThread):
    """asyncio 매매 파이프라인을 QThread에서 실행하는 오케스트레이터."""

    def __init__(self, mode: str = "paper", parent=None):
        super().__init__(parent)
        self._mode = mode
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._stop_event: asyncio.Event | None = None
        self._state = TradingState()
        self._session_manager = None
        self._scheduler = None
        self._ui_emitter: UIEmitter | None = None
        self._pipeline_tasks: list[asyncio.Task] = []
        self.signals = EngineSignals()
        self.signals.request_stop.connect(self._on_request_stop)
        self.signals.request_halt.connect(self._on_request_halt)
        self.signals.request_screening.connect(self._on_request_screening)
        self.signals.request_force_close.connect(self._on_request_force_close)
        self.signals.request_manual_close.connect(self._on_request_manual_close)
        self.signals.request_report.connect(self._on_request_report)
        self.signals.request_reconnect.connect(self._on_request_reconnect)
        self.signals.request_daily_reset.connect(self._on_request_daily_reset)
        self.signals.request_strategy_change.connect(self._on_request_strategy_change)
        self.setTerminationEnabled(True)

    def run(self):
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._install_exception_handlers()
        try:
            self._loop.run_until_complete(self._run_engine())
        except Exception as e:
            logger.error(f"EngineWorker 오류: {e}")
            try: self.signals.error.emit(str(e))
            except Exception: pass
        finally:
            self._running = False
            try: self._scheduler.shutdown(wait=False) if (self._scheduler and self._scheduler.running) else None
            except Exception: pass
            try:
                if self._session_manager is not None:
                    self._session_manager.cleanup_sync(self._loop)
            except Exception as e:
                logger.error(f"클린업 예외: {e}")
            try:
                if not self._loop.is_closed():
                    self._loop.close()
            except Exception:
                pass
            self._loop = None
            self._stop_event = None
            logger.info("EngineWorker 종료 완료")
            self.signals.stopped.emit()

    def _install_exception_handlers(self):
        import traceback
        def _emit_error(msg: str):
            try: self.signals.error.emit(msg)
            except Exception: pass
        prev_excepthook = sys.excepthook
        def _excepthook(exc_type, exc_value, exc_tb):
            try:
                tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
                logger.error(f"[CRASH] unhandled exception:\n{tb_str}")
                _emit_error(f"unhandled: {exc_type.__name__}: {exc_value}")
            except Exception: pass
            try: prev_excepthook(exc_type, exc_value, exc_tb)
            except Exception: pass
        sys.excepthook = _excepthook
        def _loop_exc_handler(loop, context):
            exc = context.get("exception")
            msg = context.get("message", "")
            try:
                if exc is not None:
                    tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                    logger.error(f"[CRASH] asyncio unhandled ({msg}):\n{tb_str}")
                    _emit_error(f"asyncio: {type(exc).__name__}: {exc}")
                else:
                    logger.error(f"[CRASH] asyncio handler context: {context}")
                    _emit_error(f"asyncio: {msg}")
            except Exception: pass
        if self._loop is not None:
            self._loop.set_exception_handler(_loop_exc_handler)

    async def _run_engine(self):
        import time as _time_mod
        _t_start = _time_mod.monotonic()
        self._stop_event = asyncio.Event()

        from config.settings import AppConfig
        from core.auth import TokenManager
        from core.kiwoom_rest import KiwoomRestClient
        from core.kiwoom_ws import KiwoomWebSocketClient
        from core.order_manager import OrderManager
        from core.order_tracker import OrderTracker
        from core.orderbook import OrderbookManager
        from core.paper_order_manager import PaperOrderManager
        from core.rate_limiter import AsyncRateLimiter
        from core.shadow_tracker import ShadowTracker
        from core.signal_scorer import SignalScorer
        from core.vi_handler import VIHandler
        from data.candle_builder import CandleBuilder
        from data.db_manager import DbManager
        from notification.telegram_bot import TelegramNotifier
        from risk.risk_manager import RiskManager
        from screener.candidate_collector import CandidateCollector
        from screener.pre_market import PreMarketScreener
        from apscheduler.schedulers.background import BackgroundScheduler
        from pipeline.tick_processor import TickProcessor
        from pipeline.signal_evaluator import SignalEvaluator
        from pipeline.order_executor import OrderExecutor
        from pipeline.screener_scheduler import ScreenerScheduler
        from pipeline.session_manager import SessionManager

        self._config = AppConfig.from_yaml()
        paper_mode = self._mode == "paper"
        self._vi_handler = VIHandler(
            static_pct=self._config.trading.vi_static_pct,
            assumed_duration_sec=self._config.trading.vi_assumed_duration_sec,
            suspected_duration_sec=self._config.trading.vi_suspected_duration_sec,
        )
        self._order_tracker = OrderTracker(timeout_seconds=self._config.trading.order_confirmation_timeout_sec)
        self._signal_scorer = SignalScorer(
            w_volume_ratio=self._config.trading.score_weight_volume_ratio,
            w_adx_strength=self._config.trading.score_weight_adx_strength,
            w_breakout_pct=self._config.trading.score_weight_breakout_pct,
            w_close_position=self._config.trading.score_weight_close_position,
            w_atr_normalized=self._config.trading.score_weight_atr_normalized,
        )
        self._shadow_tracker = ShadowTracker(stop_loss_pct=abs(getattr(self._config.trading, "stop_loss_pct", 0.08)))
        self._db = DbManager(self._config.db_path)
        await self._db.init()
        self._notifier = TelegramNotifier(self._config.telegram)
        if self._config.notifications.system_start:
            self._notifier.send(f"{'[PAPER] ' if paper_mode else ''}단타 매매 시스템 시작 (GUI)")
        self._token_manager = TokenManager(
            app_key=self._config.kiwoom.app_key,
            secret_key=self._config.kiwoom.secret_key,
            base_url=self._config.kiwoom.rest_base_url,
        )
        rate_limiter = AsyncRateLimiter(
            max_calls=self._config.kiwoom.rate_limit_calls,
            period=self._config.kiwoom.rate_limit_period,
        )
        self._rest_client = KiwoomRestClient(
            config=self._config.kiwoom, token_manager=self._token_manager, rate_limiter=rate_limiter,
        )
        self._tick_queue = asyncio.Queue(maxsize=10000)
        self._candle_queue = asyncio.Queue(maxsize=1000)
        self._signal_queue = asyncio.Queue(maxsize=100)
        self._order_queue = asyncio.Queue(maxsize=100)
        self._orderbook_manager = OrderbookManager()
        self._ws_client = KiwoomWebSocketClient(
            ws_url=self._config.kiwoom.ws_url, token_manager=self._token_manager,
            tick_queue=self._tick_queue, order_queue=self._order_queue,
            notifier=self._notifier, notifications_config=self._config.notifications,
            orderbook_manager=self._orderbook_manager,
        )
        self._ws_client.set_subscription_provider(lambda: list(self._state.active_strategies.keys()))
        self._candle_builder = CandleBuilder(candle_queue=self._candle_queue, timeframes=["1m"])
        self._risk_manager = RiskManager(trading_config=self._config.trading, db=self._db, notifier=self._notifier)
        self._risk_manager.set_daily_capital(self._config.trading.initial_capital)
        if paper_mode:
            self._order_manager = PaperOrderManager(
                risk_manager=self._risk_manager, notifier=self._notifier, db=self._db,
                trading_config=self._config.trading, order_queue=self._order_queue,
                notifications_config=self._config.notifications, backtest_config=self._config.backtest,
            )
            logger.info("주문 관리자: PaperOrderManager (시뮬레이션)")
        else:
            self._order_manager = OrderManager(
                rest_client=self._rest_client, risk_manager=self._risk_manager,
                notifier=self._notifier, db=self._db, trading_config=self._config.trading,
                order_queue=self._order_queue, notifications_config=self._config.notifications,
            )
            logger.info("주문 관리자: OrderManager (실매매)")
        self._ws_client._risk_manager = self._risk_manager
        self._ws_client._order_manager = self._order_manager
        self._candidate_collector = CandidateCollector(self._rest_client)
        self._pre_market_screener = PreMarketScreener(self._rest_client, self._db, self._config.screener)
        self._market_filter = None
        if self._config.trading.market_filter_enabled:
            from core.market_filter import MarketFilter
            self._market_filter = MarketFilter(self._rest_client, ma_length=self._config.trading.market_ma_length)
            logger.info(f"시장 필터 활성화 (MA{self._config.trading.market_ma_length})")

        _on_trade = lambda data: self.signals.trade_executed.emit(data)
        _on_market = lambda k, q: self.signals.market_status_updated.emit(k, q)
        self._tick_processor = TickProcessor(
            risk_manager=self._risk_manager, order_manager=self._order_manager,
            vi_handler=self._vi_handler, shadow_tracker=self._shadow_tracker,
            order_tracker=self._order_tracker, candle_builder=self._candle_builder,
            config=self._config, state=self._state, paper_mode=paper_mode, on_trade_executed=_on_trade,
        )
        self._signal_evaluator = SignalEvaluator(
            risk_manager=self._risk_manager, config=self._config,
            notifier=self._notifier, state=self._state,
        )
        self._order_executor = OrderExecutor(
            risk_manager=self._risk_manager, order_manager=self._order_manager,
            order_tracker=self._order_tracker, vi_handler=self._vi_handler,
            orderbook_manager=self._orderbook_manager, signal_scorer=self._signal_scorer,
            market_filter=self._market_filter, config=self._config, notifier=self._notifier,
            state=self._state, paper_mode=paper_mode, on_trade_executed=_on_trade,
        )
        self._order_executor.set_shadow_tracker(self._shadow_tracker)
        self._screener_scheduler = ScreenerScheduler(
            rest_client=self._rest_client, token_manager=self._token_manager,
            ws_client=self._ws_client, config=self._config, notifier=self._notifier,
            db=self._db, candidate_collector=self._candidate_collector,
            pre_market_screener=self._pre_market_screener, state=self._state,
        )
        self._session_manager = SessionManager(
            risk_manager=self._risk_manager, order_manager=self._order_manager,
            order_tracker=self._order_tracker, shadow_tracker=self._shadow_tracker,
            candle_builder=self._candle_builder, market_filter=self._market_filter,
            config=self._config, notifier=self._notifier, db=self._db,
            rest_client=self._rest_client, ws_client=self._ws_client,
            token_manager=self._token_manager, state=self._state,
            paper_mode=paper_mode, on_trade_executed=_on_trade, on_market_status=_on_market,
        )
        self._session_manager.set_vi_handler(self._vi_handler)
        self._ui_emitter = UIEmitter(
            signals=self.signals, state=self._state, risk_manager=self._risk_manager,
            config=self._config, mode=self._mode, ws_client=self._ws_client, db=self._db,
        )
        self._ui_emitter.set_loop(self._loop)

        # ── Scheduler ──
        self._scheduler = BackgroundScheduler()

        def _sa(coro_fn, name):
            def wrapper():
                if self._loop and self._loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(coro_fn(), self._loop)
                    try:
                        future.result(timeout=60)
                    except TimeoutError:
                        logger.error(f"[SCHED] {name} 타임아웃 (60초)")
                    except Exception as e:
                        import traceback
                        logger.error(f"[SCHED] {name} 오류: {type(e).__name__}: {e}\n{traceback.format_exc()}")
                else:
                    logger.warning(f"[SCHED] {name} 스킵 — 이벤트 루프 미실행")
            return wrapper

        _sm = self._session_manager
        _ss = self._screener_scheduler
        self._scheduler.add_job(_sa(_sm.refresh_token, "token"), "cron", hour=8, minute=0, misfire_grace_time=300)
        self._scheduler.add_job(_sa(lambda: _ss.run_screening(_sm.refresh_ohlcv), "screening"), "cron", hour=8, minute=30, misfire_grace_time=300)
        self._scheduler.add_job(_sa(_sm.force_close, "force_close"), "cron", hour=15, minute=10, misfire_grace_time=60, id="force_close", replace_existing=True)
        self._scheduler.add_job(_sa(_sm.daily_report, "daily_report"), "cron", hour=15, minute=30, misfire_grace_time=300)
        self._scheduler.add_job(_sa(lambda: _sm.daily_reset(_ss.register_active_strategies), "daily_reset"), "cron", hour=0, minute=1, misfire_grace_time=600)
        self._scheduler.add_job(_sa(_sm.refresh_ohlcv_all, "refresh_ohlcv"), "cron", hour=8, minute=5, misfire_grace_time=600)
        self._scheduler.add_job(_sa(_sm.skip_universe_refresh, "universe_refresh"), "cron", day_of_week="mon", hour=7, minute=30, misfire_grace_time=600)
        self._scheduler.add_job(_sa(_sm.collect_daily_candles, "candle_collection"), "cron", day_of_week="mon-fri", hour=15, minute=35, misfire_grace_time=600)
        self._scheduler.add_job(_sa(_sm.refresh_market_filter, "market_filter_refresh"), "cron", day_of_week="mon-fri", hour=9, minute=5, misfire_grace_time=300)
        self._scheduler.add_job(_sa(_sm.refresh_market_filter, "market_filter_mid"), "cron", day_of_week="mon-fri", hour=10, minute=0, misfire_grace_time=300, id="market_filter_refresh_mid", replace_existing=True)
        if getattr(self._config.trading, "intraday_market_filter_enabled", False):
            self._scheduler.add_job(_sa(_sm.refresh_intraday_filter, "intraday_filter"), "interval", minutes=self._config.trading.intraday_check_interval_min, id="intraday_filter_refresh", replace_existing=True)
        self._scheduler.add_job(
            lambda: self._vi_handler.log_summary() if self._vi_handler else None,
            "interval", minutes=5, id="vi_summary",
        )
        self._scheduler.start()
        logger.debug(f"BackgroundScheduler 시작됨, running={self._scheduler.running}")

        # ── Startup ──
        final_stocks, source = await self._session_manager.startup(
            screener_scheduler=self._screener_scheduler,
            progress_fn=lambda msg, pct: self.signals.startup_progress.emit(msg, pct),
        )
        now = datetime.now().time()
        if dt_time(8, 30) < now < dt_time(15, 10):
            logger.info("장중 실행 감지 — 즉시 스크리닝 시작")
            await self._screener_scheduler.run_screening(refresh_ohlcv_fn=self._session_manager.refresh_ohlcv)
        _t_total = _time_mod.monotonic() - _t_start
        logger.info(f"[STARTUP] 완료: {_t_total:.1f}s ({len(final_stocks)}종목, source={source})")

        self._running = True
        self.signals.started.emit()
        self._pipeline_tasks = [
            asyncio.create_task(self._tick_consumer(), name="tick_consumer"),
            asyncio.create_task(self._candle_consumer(), name="candle_consumer"),
            asyncio.create_task(self._signal_consumer(), name="signal_consumer"),
            asyncio.create_task(self._order_confirmation_consumer(), name="order_consumer"),
            asyncio.create_task(self._order_tracker_timeout_checker(), name="order_timeout_checker"),
        ]
        logger.info("파이프라인 시작 -- 매매 대기 중 (GUI)")

        import time as _time
        _last_health = _last_heartbeat = _time.time()
        while self._running:
            now_ts = _time.time()
            if now_ts - _last_heartbeat >= 300:
                _last_heartbeat = now_ts
                sched_ok = self._scheduler.running if self._scheduler else False
                alive = len([t for t in self._pipeline_tasks if not t.done()])
                pos_count = len(self._risk_manager.get_open_positions()) if self._risk_manager else 0
                logger.info(f"[HEARTBEAT] 스케줄러={sched_ok}, 파이프라인={alive}/5, 포지션={pos_count}")
            if now_ts - _last_health >= 30:
                _last_health = now_ts
                self._health_check()
            self._ui_emitter.emit_all()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=2.0)
                logger.info("stop_event 감지 — polling loop 탈출")
                break
            except asyncio.TimeoutError:
                pass

        logger.info("polling loop 종료 — 파이프라인 취소")
        for t in self._pipeline_tasks:
            if not t.done():
                t.cancel()
        try:
            await asyncio.wait_for(asyncio.gather(*self._pipeline_tasks, return_exceptions=True), timeout=1.0)
        except asyncio.TimeoutError:
            logger.warning("파이프라인 태스크 1초 내 미종료")
        logger.info("_run_engine 종료")

    async def _tick_consumer(self):
        while self._running and not self._stop_event.is_set():
            try:
                tick = await asyncio.wait_for(self._tick_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                self._tick_processor.check_no_tick_warning()
                continue
            except asyncio.CancelledError:
                break
            await self._tick_processor.process_tick(tick, self._signal_queue)

    async def _candle_consumer(self):
        while self._running and not self._stop_event.is_set():
            try:
                candle = await asyncio.wait_for(self._candle_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            signal = await self._signal_evaluator.process_candle(candle)
            if signal:
                await self._signal_queue.put(signal)

    async def _signal_consumer(self):
        while self._running and not self._stop_event.is_set():
            try:
                signal = await asyncio.wait_for(self._signal_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            result = await self._order_executor.execute_signal(signal)
            if result:
                self.signals.trade_executed.emit(result)

    async def _order_confirmation_consumer(self):
        await self._order_executor.run_order_confirmation_loop(
            self._order_queue, self._stop_event, lambda: self._running,
        )

    async def _order_tracker_timeout_checker(self):
        await self._order_executor.run_order_timeout_loop(
            self._stop_event, lambda: self._running, self._rest_client,
        )

    _TASK_FACTORIES = {
        "tick_consumer": "_tick_consumer",
        "candle_consumer": "_candle_consumer",
        "signal_consumer": "_signal_consumer",
        "order_consumer": "_order_confirmation_consumer",
        "order_timeout_checker": "_order_tracker_timeout_checker",
    }

    def _health_check(self):
        try:
            if self._scheduler and not self._scheduler.running:
                logger.warning("스케줄러 죽음 감지 — 재시작 시도")
                try:
                    self._scheduler.start()
                    logger.info("스케줄러 재시작 완료")
                except Exception as e:
                    logger.error(f"스케줄러 재시작 실패: {e}")
            if self._ws_client and not self._ws_client.connected:
                logger.warning("WS 연결 끊김 감지")
            dead_tasks = [t for t in self._pipeline_tasks if t.done()]
            if dead_tasks:
                for t in dead_tasks:
                    exc = t.exception() if not t.cancelled() else None
                    logger.warning(f"파이프라인 태스크 죽음: {t.get_name()} exc={exc}")
                alive_names = {t.get_name() for t in self._pipeline_tasks if not t.done()}
                self._pipeline_tasks = [t for t in self._pipeline_tasks if not t.done()]
                for name, method_name in self._TASK_FACTORIES.items():
                    if name not in alive_names:
                        method = getattr(self, method_name)
                        self._pipeline_tasks.append(asyncio.create_task(method(), name=name))
                logger.info(f"파이프라인 태스크 재시작 완료: {len(self._pipeline_tasks)}개")
        except Exception as e:
            logger.error(f"헬스 체크 오류: {e}")

    def _on_request_stop(self):
        logger.info("엔진 종료 요청")
        self._running = False
        try: self._scheduler.shutdown(wait=False) if (self._scheduler and self._scheduler.running) else None
        except Exception: pass
        if self._loop and self._loop.is_running() and self._stop_event:
            try: self._loop.call_soon_threadsafe(self._stop_event.set)
            except Exception: pass

    def _on_request_halt(self):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_halt(), self._loop)

    async def _async_halt(self):
        if self._risk_manager:
            self._risk_manager._halted = True
            logger.warning("매매 긴급 정지 활성화")
        if self._ui_emitter:
            self._ui_emitter.emit_status()

    def _on_request_screening(self):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._screener_scheduler.run_screening(self._session_manager.refresh_ohlcv),
                self._loop,
            )

    def _on_request_force_close(self):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._session_manager.force_close(), self._loop)

    def _on_request_manual_close(self, ticker: str):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._session_manager.manual_close_one(ticker), self._loop)

    def _on_request_report(self):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._session_manager.daily_report(), self._loop)

    def _on_request_reconnect(self):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_reconnect(), self._loop)

    async def _async_reconnect(self):
        if self._ws_client:
            try:
                await self._ws_client.disconnect()
                await self._ws_client.connect()
                logger.info("WS 재연결 완료")
            except Exception as e:
                logger.error(f"WS 재연결 실패: {e}")

    def _on_request_strategy_change(self, strategy_name: str):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_strategy_change(strategy_name), self._loop)

    async def _async_strategy_change(self, strategy_name: str):
        await self._session_manager.strategy_change(strategy_name)
        if self._ui_emitter:
            self._ui_emitter.emit_status()

    def _on_request_daily_reset(self):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_daily_reset(), self._loop)

    async def _async_daily_reset(self):
        await self._session_manager.quick_reset()
        if self._ui_emitter:
            self._ui_emitter.emit_status()

    def _emit_status(self):
        if self._ui_emitter: self._ui_emitter.emit_status()

    @property
    def engine_running(self) -> bool: return self._running

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None: return self._loop
