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


class EngineWorker(QThread):
    """asyncio 매매 파이프라인을 QThread에서 실행."""

    def __init__(self, mode: str = "paper", parent=None):
        super().__init__(parent)
        self._mode = mode
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._stop_event: asyncio.Event | None = None

        # Components (initialized in _run_engine)
        self._config = None
        self._db = None
        self._notifier = None
        self._rest_client = None
        self._ws_client = None
        self._candle_builder = None
        self._risk_manager = None
        self._order_manager = None
        self._scheduler = None
        self._active_strategy = None
        self._active_strategies: dict = {}  # {ticker: {"strategy": ..., "name": ..., "score": ...}}
        self._pipeline_tasks: list[asyncio.Task] = []

        # Screener components
        self._candidate_collector = None
        self._pre_market_screener = None

        # Market filter (Phase 1 Day 3) — 코스피/코스닥 지수 MA 기반 매수 차단
        self._market_filter = None
        self._ticker_markets: dict[str, str] = {}  # {ticker: "kospi"/"kosdaq"/"unknown"}
        # 유니버스 종목명 맵 (active_strategies와 독립) — trades 조회 시 fallback
        self._ticker_names: dict[str, str] = {}
        # 상한가 맵 (전일 종가 × 1.30, 호가 절사) — OHLCV 갱신 시 재계산
        self._limit_up_map: dict[str, float] = {}

        # Queues
        self._tick_queue = None
        self._candle_queue = None
        self._signal_queue = None
        self._order_queue = None

        # Candle history for strategy
        self._candle_history: dict[str, list[dict]] = {}
        self._MAX_HISTORY = 100
        # 최신 틱 가격 (포지션 현재가 표시용)
        self._latest_prices: dict[str, float] = {}
        # 런타임 승/패 카운터
        self._rt_wins: int = 0
        self._rt_losses: int = 0
        # 포지션 변경 감지용
        self._last_pos_tickers: list[str] = []

        # Screener results cache (for UI emission)
        self._screener_results: list[dict] = []
        # 전일 종가/고가 맵 (watchlist 표시용)
        self._prev_close: dict[str, float] = {}
        self._prev_high_map: dict[str, float] = {}

        self.signals = EngineSignals()

        # UI -> Worker signal connections
        self.signals.request_stop.connect(self._on_request_stop)
        self.signals.request_halt.connect(self._on_request_halt)
        self.signals.request_screening.connect(self._on_request_screening)
        self.signals.request_force_close.connect(self._on_request_force_close)
        self.signals.request_report.connect(self._on_request_report)
        self.signals.request_reconnect.connect(self._on_request_reconnect)
        self.signals.request_daily_reset.connect(self._on_request_daily_reset)
        self.signals.request_strategy_change.connect(self._on_request_strategy_change)

        # daemon thread
        self.setTerminationEnabled(True)

    # ── QThread entry point ──

    def run(self):
        """QThread main -- asyncio loop."""
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        # Global exception handler — unhandled 예외를 로그 + GUI 에러 시그널로 라우팅
        self._install_exception_handlers()

        # Phase 3 Day 12+: 일일 손실 한도 도달 1회성 알림 플래그
        self._daily_halt_notified = False

        try:
            self._loop.run_until_complete(self._run_engine())
        except Exception as e:
            logger.error(f"EngineWorker 오류: {e}")
            try:
                self.signals.error.emit(str(e))
            except Exception:
                pass
        finally:
            logger.info("EngineWorker finally — 클린업 시작")
            self._running = False
            try:
                self._cleanup_sync()
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

    # ── Global exception handlers ──

    def _install_exception_handlers(self):
        """sys.excepthook + asyncio loop exception handler 등록.

        unhandled 예외를 로그로 남기고 GUI에 error 시그널로 전달한다.
        프로세스/이벤트 루프가 조용히 죽는 것을 방지하는 것이 목적.
        """
        import traceback

        def _emit_error(msg: str) -> None:
            try:
                self.signals.error.emit(msg)
            except Exception:
                pass

        # 1) sys.excepthook — 동기 코드의 unhandled 예외
        prev_excepthook = sys.excepthook

        def _excepthook(exc_type, exc_value, exc_tb):
            try:
                tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
                logger.error(f"[CRASH] unhandled exception:\n{tb_str}")
                _emit_error(f"unhandled: {exc_type.__name__}: {exc_value}")
            except Exception:
                pass
            try:
                prev_excepthook(exc_type, exc_value, exc_tb)
            except Exception:
                pass

        sys.excepthook = _excepthook

        # 2) asyncio loop exception handler — Task/콜백의 unhandled 예외
        def _loop_exc_handler(loop, context):
            exc = context.get("exception")
            msg = context.get("message", "")
            try:
                if exc is not None:
                    tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                    logger.error(f"[CRASH] asyncio unhandled exception ({msg}):\n{tb_str}")
                    _emit_error(f"asyncio: {type(exc).__name__}: {exc}")
                else:
                    logger.error(f"[CRASH] asyncio handler context: {context}")
                    _emit_error(f"asyncio: {msg}")
            except Exception:
                pass

        if self._loop is not None:
            self._loop.set_exception_handler(_loop_exc_handler)

    # ── Core async engine ──

    async def _run_engine(self):
        """Initialize components and start pipeline."""
        self._stop_event = asyncio.Event()

        # Lazy imports to avoid circular deps when GUI loads without full env
        from config.settings import AppConfig
        from core.auth import TokenManager
        from core.kiwoom_rest import KiwoomRestClient
        from core.kiwoom_ws import KiwoomWebSocketClient
        from core.order_manager import OrderManager
        from core.paper_order_manager import PaperOrderManager
        from core.rate_limiter import AsyncRateLimiter
        from data.candle_builder import CandleBuilder
        from data.db_manager import DbManager
        from notification.telegram_bot import TelegramNotifier
        from risk.risk_manager import RiskManager
        from screener.candidate_collector import CandidateCollector
        from screener.pre_market import PreMarketScreener
        from apscheduler.schedulers.background import BackgroundScheduler

        # 1. Config
        self._config = AppConfig.from_yaml()
        paper_mode = self._mode == "paper"

        # 2. Infrastructure
        self._db = DbManager(self._config.db_path)
        await self._db.init()

        self._notifier = TelegramNotifier(self._config.telegram)
        mode_tag = "[PAPER] " if paper_mode else ""
        if self._config.notifications.system_start:
            await self._notifier.send(f"{mode_tag}단타 매매 시스템 시작 (GUI)")

        # ADR-006: 24시간 이상 가동 감지 안전망
        await self._check_uptime_sanity()

        self._token_manager = TokenManager(
            app_key=self._config.kiwoom.app_key,
            secret_key=self._config.kiwoom.secret_key,
            base_url=self._config.kiwoom.rest_base_url,
        )
        token_manager = self._token_manager
        rate_limiter = AsyncRateLimiter(
            max_calls=self._config.kiwoom.rate_limit_calls,
            period=self._config.kiwoom.rate_limit_period,
        )
        self._rest_client = KiwoomRestClient(
            config=self._config.kiwoom,
            token_manager=token_manager,
            rate_limiter=rate_limiter,
        )

        # Queues
        self._tick_queue = asyncio.Queue(maxsize=10000)
        self._candle_queue = asyncio.Queue(maxsize=1000)
        self._signal_queue = asyncio.Queue(maxsize=100)
        self._order_queue = asyncio.Queue(maxsize=100)

        # Components
        self._ws_client = KiwoomWebSocketClient(
            ws_url=self._config.kiwoom.ws_url,
            token_manager=token_manager,
            tick_queue=self._tick_queue,
            order_queue=self._order_queue,
            notifier=self._notifier,
            notifications_config=self._config.notifications,
        )
        self._candle_builder = CandleBuilder(
            candle_queue=self._candle_queue, timeframes=["1m", "5m"],
        )
        self._risk_manager = RiskManager(
            trading_config=self._config.trading, db=self._db, notifier=self._notifier,
        )
        self._risk_manager.set_daily_capital(self._config.trading.initial_capital)

        if paper_mode:
            self._order_manager = PaperOrderManager(
                risk_manager=self._risk_manager,
                notifier=self._notifier, db=self._db,
                trading_config=self._config.trading,
                order_queue=self._order_queue,
                notifications_config=self._config.notifications,
                backtest_config=self._config.backtest,  # ADR-009 공유 비용 모델
            )
            logger.info("주문 관리자: PaperOrderManager (시뮬레이션)")
        else:
            self._order_manager = OrderManager(
                rest_client=self._rest_client,
                risk_manager=self._risk_manager,
                notifier=self._notifier, db=self._db,
                trading_config=self._config.trading,
                order_queue=self._order_queue,
                notifications_config=self._config.notifications,
            )
            logger.info("주문 관리자: OrderManager (실매매)")

        # WS에 리스크/주문 관리자 연결 (긴급 청산용)
        self._ws_client._risk_manager = self._risk_manager
        self._ws_client._order_manager = self._order_manager

        # Screener
        self._candidate_collector = CandidateCollector(self._rest_client)
        self._pre_market_screener = PreMarketScreener(
            self._rest_client, self._db, self._config.screener,
        )

        # Market filter (Phase 1 Day 3)
        if self._config.trading.market_filter_enabled:
            from core.market_filter import MarketFilter
            self._market_filter = MarketFilter(
                self._rest_client,
                ma_length=self._config.trading.market_ma_length,
            )
            logger.info(
                f"시장 필터 활성화 (MA{self._config.trading.market_ma_length})"
            )
        else:
            logger.info("시장 필터 비활성화")

        # 3. Scheduler (BackgroundScheduler — 이벤트 루프와 독립 실행)
        self._scheduler = BackgroundScheduler()

        def _schedule_async(coro_func, name):
            """BackgroundScheduler에서 async 함수를 안전하게 호출하는 래퍼."""
            def wrapper():
                if self._loop and self._loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(coro_func(), self._loop)
                    try:
                        future.result(timeout=60)
                    except TimeoutError:
                        logger.error(f"[SCHED] {name} 타임아웃 (60초) — 이벤트 루프 응답 없음")
                    except Exception as e:
                        logger.error(f"[SCHED] {name} 실행 오류: {type(e).__name__}: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
                else:
                    logger.warning(f"[SCHED] {name} 스킵 — 이벤트 루프 미실행 (loop={self._loop is not None})")
            return wrapper

        self._scheduler.add_job(
            _schedule_async(self._safe_refresh_token, "token_refresh"),
            "cron", hour=8, minute=0, misfire_grace_time=300,
        )
        self._scheduler.add_job(
            _schedule_async(self._safe_run_screening, "screening"),
            "cron", hour=8, minute=30, misfire_grace_time=300,
        )
        self._scheduler.add_job(
            _schedule_async(self._safe_force_close, "force_close"),
            "cron", hour=15, minute=10, misfire_grace_time=60,
        )
        self._scheduler.add_job(
            _schedule_async(self._safe_run_daily_report, "daily_report"),
            "cron", hour=15, minute=30, misfire_grace_time=300,
        )
        # ADR-006: 자정 일일 리셋 (운영자 재시작 안전망)
        self._scheduler.add_job(
            _schedule_async(self._safe_daily_reset, "daily_reset"),
            "cron", hour=0, minute=1, misfire_grace_time=600,
        )
        # ADR-006: 매일 08:05 전일 OHLCV 갱신 (토큰 갱신 직후)
        self._scheduler.add_job(
            _schedule_async(self._safe_refresh_ohlcv, "refresh_ohlcv"),
            "cron", hour=8, minute=5, misfire_grace_time=600,
        )
        # ADR-012: 주간 유니버스 자동 갱신 (월요일 07:30)
        self._scheduler.add_job(
            _schedule_async(self._safe_refresh_universe, "universe_refresh"),
            "cron", day_of_week="mon", hour=7, minute=30, misfire_grace_time=600,
        )
        # ADR-014: 일일 분봉 자동 수집 (평일 15:35)
        self._scheduler.add_job(
            _schedule_async(self._safe_collect_candles, "candle_collection"),
            "cron", day_of_week="mon-fri", hour=15, minute=35, misfire_grace_time=600,
        )
        self._scheduler.start()
        logger.debug(f"BackgroundScheduler 시작됨, running={self._scheduler.running}")

        # Late screening (장중 실행 시 즉시 스크리닝 — 점수 업데이트 + 현재가 초기화)
        now = datetime.now().time()
        if dt_time(8, 30) < now < dt_time(15, 10):
            logger.info("장중 실행 감지 — 즉시 스크리닝 시작")
            await self._run_screening()

        # Position reconciliation (장애 복구)
        try:
            # ADR-007: DB 오픈 포지션을 in-memory로 복원 (프로세스 재시작 장애 대비)
            restored = await self._risk_manager.restore_from_db()
            if restored and self._notifier:
                try:
                    await self._notifier.send(
                        f"[복구] DB에서 오픈 포지션 {restored}건 복원 — API 대조 진행"
                    )
                except Exception:
                    pass

            api_balance = await self._rest_client.get_account_balance()
            holdings = [
                {"ticker": h["pdno"], "qty": int(h["hldg_qty"])}
                for h in api_balance.get("output1", [])
                if int(h.get("hldg_qty", 0)) > 0
            ]
            mismatches = await self._risk_manager.reconcile_positions(holdings)
            if mismatches:
                await self._notifier.send_urgent(
                    "포지션 불일치 감지!\n" + "\n".join(mismatches)
                )
        except Exception as e:
            logger.error(f"장애 복구 점검 실패: {e}")

        await self._risk_manager.check_consecutive_losses()

        # WS connect + 유니버스 전체 구독 + 전략 등록
        try:
            await self._ws_client.connect()

            all_stocks = self._load_universe()
            all_tickers = [s["ticker"] for s in all_stocks]
            if all_tickers:
                await self._ws_client.subscribe(all_tickers)
                logger.info(f"유니버스 전체 WS 구독: {len(all_tickers)}종목")

                n_unknown = sum(1 for m in self._ticker_markets.values() if m == "unknown")
                if n_unknown:
                    logger.warning(
                        f"⚠ universe.yaml에 market 필드 없는 종목 {n_unknown}개 "
                        f"— scripts/update_universe_market.py 실행 권장"
                    )

            self._register_active_strategies(all_stocks)
            await self._refresh_prev_day_ohlcv(all_stocks)

            # 시장 필터 초기 갱신 (Phase 1 Day 3)
            if self._market_filter is not None:
                try:
                    await self._market_filter.refresh()
                    # Phase 3 Day 12+: GUI로 상태 전파
                    self.signals.market_status_updated.emit(
                        self._market_filter.kospi_strong,
                        self._market_filter.kosdaq_strong,
                    )
                    if self._notifier:
                        try:
                            k = "강세" if self._market_filter.kospi_strong else "약세"
                            q = "강세" if self._market_filter.kosdaq_strong else "약세"
                            await self._notifier.send(
                                f"[MARKET] 시장 필터 갱신 — 코스피 {k} / 코스닥 {q}"
                            )
                        except Exception:
                            pass
                except Exception as e:
                    logger.error(f"시장 필터 초기 갱신 실패: {e}")
        except Exception as e:
            logger.error(f"WS 연결/전략 등록 실패: {e}")

        # Start pipeline
        self._running = True
        self.signals.started.emit()

        self._pipeline_tasks = [
            asyncio.create_task(self._tick_consumer(), name="tick_consumer"),
            asyncio.create_task(self._candle_consumer(), name="candle_consumer"),
            asyncio.create_task(self._signal_consumer(), name="signal_consumer"),
            asyncio.create_task(self._order_confirmation_consumer(), name="order_consumer"),
        ]

        logger.info("파이프라인 시작 -- 매매 대기 중 (GUI)")

        logger.info("=== polling loop 진입 ===")

        # 4. Polling loop (2-second interval, 0.2s check for fast stop)
        import time as _time
        _last_health_check = _time.time()
        _last_heartbeat = _time.time()

        while self._running:
            now_ts = _time.time()

            # 하트비트 (5분마다)
            if now_ts - _last_heartbeat >= 300:
                _last_heartbeat = now_ts
                sched_ok = self._scheduler.running if self._scheduler else False
                alive_tasks = len([t for t in self._pipeline_tasks if not t.done()])
                pos_count = len(self._risk_manager.get_open_positions()) if self._risk_manager else 0
                logger.info(
                    f"[HEARTBEAT] 스케줄러={sched_ok}, 파이프라인={alive_tasks}/4, 포지션={pos_count}"
                )

            # 헬스 체크 (30초마다)
            if now_ts - _last_health_check >= 30:
                _last_health_check = now_ts
                self._health_check()

            for fn, label in [
                (self._emit_status, "status"),
                (self._emit_positions, "positions"),
                (self._emit_trades, "trades"),
                (self._emit_pnl, "pnl"),
                (self._emit_candidates, "candidates"),
                (self._emit_watchlist, "watchlist"),
            ]:
                try:
                    fn()
                except Exception as e:
                    logger.error(f"emit_{label} 오류: {e}")

            # stop_event 대기 (최대 2초, set되면 즉시 깨어남)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=2.0)
                logger.info("stop_event 감지 — polling loop 탈출")
                break
            except asyncio.TimeoutError:
                pass

        # 루프 탈출 후 파이프라인 태스크 취소
        logger.info("polling loop 종료 — 파이프라인 취소")
        for t in self._pipeline_tasks:
            if not t.done():
                t.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._pipeline_tasks, return_exceptions=True),
                timeout=1.0,
            )
        except asyncio.TimeoutError:
            logger.warning("파이프라인 태스크 1초 내 미종료")
        logger.info("_run_engine 종료")

    # ── Pipeline consumers ──

    async def _tick_consumer(self):
        """틱 -> 캔들 빌더 + 포지션 모니터링."""
        import time as _time
        tick_count = 0
        last_tick_log = _time.time()
        first_tick_logged = False

        while self._running and not self._stop_event.is_set():
            try:
                tick = await asyncio.wait_for(self._tick_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if _time.time() - last_tick_log >= 300 and tick_count == 0:
                    logger.warning("[TICK] 5분간 틱 수신 0건 — WS 연결 확인 필요")
                    last_tick_log = _time.time()
                continue
            except asyncio.CancelledError:
                break

            tick_count += 1
            now_ts = _time.time()
            if not first_tick_logged:
                logger.info(f"[TICK] 첫 틱 수신: {tick.get('ticker', '?')} @ {tick.get('price', 0):,}")
                first_tick_logged = True
            if now_ts - last_tick_log >= 60:
                logger.info(f"[TICK] {tick_count}건 수신 (최근 60초)")
                tick_count = 0
                last_tick_log = now_ts

            try:
                # 1. 캔들 빌더에 전달 (기존)
                await self._candle_builder.on_tick(tick)
                # 2. 최신 가격 기록 + 포지션 모니터링
                ticker = tick["ticker"]
                price = tick["price"]
                self._latest_prices[ticker] = price
                pos = self._risk_manager.get_position(ticker)
                if pos is None or pos["remaining_qty"] <= 0:
                    continue
                # 상한가 즉시 청산 (stop_loss 체크 전, 최우선)
                if self._risk_manager.check_limit_up(ticker, price):
                    qty = pos["remaining_qty"]
                    entry = pos["entry_price"]
                    pnl = (price - entry) * qty
                    pnl_pct = ((price / entry) - 1) * 100 if entry > 0 else 0
                    strategy_name = pos.get("strategy", "") or "unknown"
                    result = await self._order_manager.execute_sell_stop(
                        ticker=ticker, qty=qty, price=int(price),
                        strategy=strategy_name, pnl=pnl, pnl_pct=pnl_pct,
                        exit_reason="limit_up_exit",
                    )
                    if result is not None:
                        self._risk_manager.settle_sell(ticker, price, qty)
                        if pnl >= 0:
                            self._rt_wins += 1
                        else:
                            self._rt_losses += 1
                        logger.info(
                            f"limit_up_exit 실행: {ticker} {qty}주 @ {price:,} "
                            f"PnL={pnl:+,.0f}"
                        )
                        strat_info = self._active_strategies.get(ticker)
                        if strat_info:
                            strat_info["strategy"].on_exit()
                        self.signals.trade_executed.emit({
                            "time": datetime.now().strftime("%H:%M:%S"),
                            "side": "sell", "ticker": ticker,
                            "price": int(price), "qty": qty,
                            "pnl": int(pnl), "reason": "limit_up_exit",
                        })
                        continue
                    else:
                        # 체결 실패 → stop을 상한가 × floor_pct 로 상향 (안전장치)
                        new_stop = self._risk_manager.raise_stop_to_limit_up_floor(ticker)
                        logger.warning(
                            f"limit_up_exit 실패 → stop 상향: {ticker} "
                            f"new_stop={new_stop:,.0f}"
                        )
                        # fall-through: 이후 기존 stop_loss/trailing 로직이 처리
                # 손절 체크 (tp1_hit 후 트리거면 trailing_stop로 구분)
                if self._risk_manager.check_stop_loss(ticker, price):
                    qty = pos["remaining_qty"]
                    entry = pos["entry_price"]
                    pnl = (price - entry) * qty
                    pnl_pct = ((price / entry) - 1) * 100 if entry > 0 else 0
                    strategy_name = pos.get("strategy", "") or "unknown"
                    # ADR-010: Pure trailing 모드 시 tp1_hit 없이도 trailing 활성
                    pure_trail = not getattr(self._config.trading, "atr_tp_enabled", True)
                    is_trailing = pos.get("tp1_hit") or pure_trail
                    # ADR-017: BE 발동 후 상향된 stop에 걸린 청산이면 breakeven_stop 태깅
                    if pos.get("breakeven_active") and pos["stop_loss"] >= pos["entry_price"]:
                        reason_code = "breakeven_stop"
                    elif is_trailing and price > entry * 0.975:
                        reason_code = "trailing_stop"
                    else:
                        reason_code = "stop_loss"
                    await self._order_manager.execute_sell_stop(
                        ticker=ticker, qty=qty, price=int(price),
                        strategy=strategy_name, pnl=pnl, pnl_pct=pnl_pct,
                        exit_reason=reason_code,
                    )
                    self._risk_manager.settle_sell(ticker, price, qty)
                    if pnl >= 0:
                        self._rt_wins += 1
                    else:
                        self._rt_losses += 1
                    logger.info(f"{reason_code} 실행: {ticker} {qty}주 @ {price:,} PnL={pnl:+,.0f}")
                    strat_info = self._active_strategies.get(ticker)
                    if strat_info:
                        strat_info["strategy"].on_exit()
                    self.signals.trade_executed.emit({
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "side": "sell", "ticker": ticker,
                        "price": int(price), "qty": qty,
                        "pnl": int(pnl), "reason": reason_code,
                    })
                    continue
                # TP1 체크
                if self._risk_manager.check_tp1(ticker, price):
                    sell_qty = int(pos["remaining_qty"] * self._config.trading.tp1_sell_ratio)
                    entry = pos["entry_price"]
                    pnl = (price - entry) * sell_qty
                    pnl_pct = ((price / entry) - 1) * 100 if entry > 0 else 0
                    strategy_name = pos.get("strategy", "") or "unknown"
                    await self._order_manager.execute_sell_tp1(
                        ticker=ticker, price=int(price), remaining_qty=pos["remaining_qty"],
                        strategy=strategy_name, pnl=pnl, pnl_pct=pnl_pct,
                        exit_reason="tp1_hit",
                    )
                    self._risk_manager.mark_tp1_hit(ticker, sell_qty, sell_price=price)
                    self._rt_wins += 1
                    logger.info(f"TP1 실행: {ticker} {sell_qty}주 @ {price:,} PnL={pnl:+,.0f}")
                    self.signals.trade_executed.emit({
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "side": "sell", "ticker": ticker,
                        "price": int(price), "qty": sell_qty,
                        "pnl": int(pnl), "reason": "tp1_hit",
                    })
                    continue
                # 트레일링 스톱 갱신
                self._risk_manager.update_trailing_stop(ticker, price)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"tick_consumer 오류: {e}")

    async def _candle_consumer(self):
        """캔들 -> 전략 엔진. 롤링 DataFrame 유지."""
        import pandas as pd
        import time as _time
        candle_count = 0
        signal_eval_count = 0
        last_candle_log = _time.time()
        while self._running and not self._stop_event.is_set():
            try:
                candle = await asyncio.wait_for(self._candle_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            candle_count += 1
            now_ts = _time.time()
            if now_ts - last_candle_log >= 300:
                logger.info(f"[CANDLE] {candle_count}건 생성, {signal_eval_count}건 평가 (최근 5분)")
                candle_count = 0
                signal_eval_count = 0
                last_candle_log = now_ts

            try:
                ticker = candle["ticker"]

                # 캔들 히스토리는 모든 종목에 대해 유지 (장중 재스크리닝 대비)
                self._candle_history.setdefault(ticker, [])
                self._candle_history[ticker].append(candle)
                if len(self._candle_history[ticker]) > self._MAX_HISTORY:
                    self._candle_history[ticker] = self._candle_history[ticker][-self._MAX_HISTORY:]

                # 전략 판단은 active_strategies에 등록된 종목만
                if not self._active_strategies:
                    continue
                if self._risk_manager.is_trading_halted():
                    # Phase 3 Day 12+: 일일 손실 한도 도달 — 최초 1회 텔레그램 알림
                    if not self._daily_halt_notified and self._notifier:
                        self._daily_halt_notified = True
                        try:
                            loss = self._risk_manager._daily_pnl
                            limit = self._config.trading.daily_max_loss_pct * 100
                            await self._notifier.send_urgent(
                                f"[HALT] 일일 손실 한도 도달\n"
                                f"일일 PnL: {loss:+,.0f}원\n"
                                f"한도: {limit:.1f}%\n"
                                f"오늘 추가 매수 차단"
                            )
                        except Exception as e:
                            logger.warning(f"halt 텔레그램 실패: {e}")
                    continue
                if ticker not in self._active_strategies:
                    continue
                # Phase 2 Day 10: 블랙리스트 체크 (신호 평가 자체를 차단)
                if self._risk_manager.is_ticker_blacklisted(ticker):
                    continue
                # Phase 3 Day 11.5: 연속 손실 휴식
                if self._risk_manager.is_in_loss_rest():
                    continue

                # 동시 포지션 한도
                open_pos = self._risk_manager.get_open_positions()
                if len(open_pos) >= self._config.trading.max_positions and ticker not in open_pos:
                    continue
                if self._risk_manager.get_position(ticker):
                    continue

                strat_info = self._active_strategies[ticker]
                strategy = strat_info["strategy"]

                if candle.get("tf") == "5m" and hasattr(strategy, "on_candle_5m"):
                    strategy.on_candle_5m(candle)

                candle["price"] = candle.get("close", 0)
                df = pd.DataFrame(self._candle_history[ticker])
                signal_eval_count += 1
                signal = strategy.generate_signal(df, candle)
                if signal:
                    await self._signal_queue.put(signal)
            except Exception as e:
                logger.error(f"candle_consumer 오류: {e}")

    async def _signal_consumer(self):
        """신호 -> 주문 실행."""
        while self._running and not self._stop_event.is_set():
            try:
                signal = await asyncio.wait_for(self._signal_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                if signal.side != "buy" or signal.ticker not in self._active_strategies:
                    continue

                # 시장 필터 (Phase 1 Day 3) — 해당 시장 약세 시 매수 차단
                if self._market_filter is not None:
                    market = self._ticker_markets.get(signal.ticker, "unknown")
                    if not self._market_filter.is_allowed(market):
                        logger.debug(
                            f"[MARKET] 매수 차단 ({market} 약세): {signal.ticker}"
                        )
                        continue

                # 포지션 한도 재확인
                open_pos = self._risk_manager.get_open_positions()
                if len(open_pos) >= self._config.trading.max_positions:
                    logger.info(f"포지션 한도 ({self._config.trading.max_positions}), 무시: {signal.ticker}")
                    continue

                strategy = self._active_strategies[signal.ticker]["strategy"]
                sl = strategy.get_stop_loss(signal.price)
                tp1 = strategy.get_take_profit(signal.price)

                capital = self._risk_manager.available_capital
                if capital <= 0:
                    capital = self._config.trading.initial_capital
                position_capital = capital / self._config.trading.max_positions
                # ADR-013 페이퍼 시뮬(grid_maxpos_capital.py)과 동일한 전량 투자 사이징
                max_qty = int(position_capital / signal.price)
                total_qty = int(max_qty * self._risk_manager.position_scale)
                total_qty = max(total_qty, 1)

                cost = signal.price * total_qty
                if cost > self._risk_manager.available_capital:
                    logger.warning(f"자본 부족 — 매수 스킵: {signal.ticker} 필요={cost:,.0f} 가용={self._risk_manager.available_capital:,.0f}")
                    continue

                result = await self._order_manager.execute_buy(
                    ticker=signal.ticker,
                    price=int(signal.price),
                    total_qty=total_qty,
                    strategy=signal.strategy,
                )
                if result:
                    # trailing_pct는 None으로 두면 register_position이
                    # 글로벌 trailing_stop_pct를 사용 (실전 ↔ 백테스트 통일)
                    self._risk_manager.register_position(
                        ticker=signal.ticker,
                        entry_price=signal.price,
                        qty=result["qty"],
                        stop_loss=sl,
                        tp1_price=tp1,
                        strategy=signal.strategy or "",
                        limit_up_price=self._limit_up_map.get(signal.ticker),
                    )
                    strategy.on_entry()
                    self.signals.trade_executed.emit({
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "side": "buy",
                        "ticker": signal.ticker,
                        "price": int(signal.price),
                        "qty": result["qty"],
                        "pnl": None, "reason": signal.strategy or "entry",
                    })
            except Exception as e:
                logger.error(f"signal_consumer 오류: {e}")

    async def _order_confirmation_consumer(self):
        """WS 체결통보 처리."""
        while self._running and not self._stop_event.is_set():
            try:
                exec_data = await asyncio.wait_for(self._order_queue.get(), timeout=0.5)
                logger.info(f"체결통보: {exec_data}")
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"order_confirmation_consumer 오류: {e}")

    # ── Screening & force close ──

    async def _refresh_token(self):
        """매일 08:00 토큰 사전 갱신."""
        try:
            token = await self._token_manager.get_token()
            logger.info(f"토큰 사전 갱신 완료: {token[:10]}...")
        except Exception as e:
            logger.error(f"토큰 갱신 실패: {e}")
            if self._notifier and self._config.notifications.token_refresh_failure:
                await self._notifier.send_urgent(f"토큰 갱신 실패: {e}")

    async def _run_screening(self):
        """08:30 장 전 스크리닝 — score 업데이트 + UI 정보 제공 (전략 등록은 _run_engine에서 완료)."""
        today = datetime.now().strftime("%Y-%m-%d")
        logger.info(f"스크리닝 시작 ({today})")

        try:
            # 1. Candidates 수집
            candidates = await self._candidate_collector.collect()
            if not candidates:
                logger.warning("candidates 없음")
                await self._notifier.send("스크리닝: candidates 없음")
                return

            # 2. 4단계 필터 적용
            screened = await self._pre_market_screener.screen(candidates)
            if not screened:
                logger.warning("스크리닝 통과 종목 없음")
                await self._notifier.send("스크리닝: 통과 종목 없음")
                return

            # Cache for UI
            self._screener_results = screened

            # 3. 스크리닝 결과 DB 저장
            await self._pre_market_screener.save_results(today, screened)

            # 4. score 업데이트 (active_strategies는 유지)
            for s in screened:
                ticker = s["ticker"]
                if ticker in self._active_strategies:
                    self._active_strategies[ticker]["score"] = s.get("score", 0)

            # 5. 상위 N종목 현재가 초기화 (REST 1회 조회)
            top_n = self._config.trading.screening_top_n
            selected = screened[:top_n]
            for s in selected:
                tk = s["ticker"]
                try:
                    price_data = await self._rest_client.get_current_price(tk)
                    output = price_data.get("output1", {})
                    cur_price = abs(int(output.get("cur_pric", 0)))
                    if cur_price > 0:
                        self._latest_prices[tk] = cur_price
                except Exception as e:
                    logger.warning(f"현재가 초기화 실패 ({tk}): {e}")

            force = getattr(self._config, 'force_strategy', '') or 'auto'
            logger.info(f"스크리닝 완료: {len(screened)}종목 통과, 감시: {len(self._active_strategies)}종목 유지")
            await self._notifier.send(
                f"스크리닝 완료 — {force}\n"
                f"필터 통과: {len(screened)}종목\n"
                f"전체 감시: {len(self._active_strategies)}종목\n"
                f"상위:\n"
                + "\n".join(
                    f"  {s.get('name','')} ({s['ticker']}) 점수:{s.get('score',0):.1f}"
                    for s in selected
                )
            )

        except Exception as exc:
            import traceback
            logger.error(f"스크리닝 실패: {exc}\n{traceback.format_exc()}")
            try:
                await self._notifier.send_urgent(f"스크리닝 오류: {exc}")
            except Exception:
                pass

    async def _force_close(self):
        """15:10 강제 청산."""
        logger.warning("15:10 강제 청산 시작")
        for ticker, pos in list(self._risk_manager.get_open_positions().items()):
            if pos.get("remaining_qty", 0) > 0:
                close_price = int(self._latest_prices.get(ticker, pos.get("entry_price", 0)))
                qty = pos["remaining_qty"]
                entry = pos.get("entry_price", 0)
                pnl = (close_price - entry) * qty if entry > 0 else 0
                pnl_pct = ((close_price / entry) - 1) * 100 if entry > 0 else 0
                strategy_name = pos.get("strategy", "") or "unknown"
                await self._order_manager.execute_sell_force_close(
                    ticker=ticker, qty=qty, price=close_price,
                    strategy=strategy_name, pnl=pnl, pnl_pct=pnl_pct,
                    exit_reason="forced_close",
                )
                self._risk_manager.settle_sell(ticker, float(close_price), qty)
                strat_info = self._active_strategies.get(ticker)
                if strat_info:
                    strat_info["strategy"].on_exit()
        await self._candle_builder.flush()
        self._candle_builder.reset()
        await self._risk_manager.save_daily_summary()
        self._risk_manager.reset_daily()
        # Phase 3 Day 12+: 다음 날 다시 halt 알림 가능하도록 플래그 리셋
        self._daily_halt_notified = False
        self._active_strategy = None
        self._active_strategies = {}
        self._candle_history.clear()

    async def _run_daily_report(self):
        """15:30 일일 보고서 텔레그램 발송."""
        today = datetime.now().strftime("%Y-%m-%d")
        logger.info("15:30 일일 보고서 생성 시작")

        try:
            summary = await self._db.fetch_one(
                "SELECT * FROM daily_pnl WHERE date = ?", (today,),
            )
        except Exception as e:
            logger.warning(f"daily_pnl 조회 실패: {e}")
            summary = None

        if summary is None:
            summary = await self._risk_manager.save_daily_summary()

        if not self._config.notifications.daily_report:
            logger.info("일일 보고서 — 알림 비활성")
        elif summary:
            await self._notifier.send_daily_report(
                date=summary["date"],
                total_trades=summary["total_trades"],
                wins=summary["wins"],
                losses=summary.get("losses", summary["total_trades"] - summary["wins"]),
                total_pnl=int(summary["total_pnl"]),
                win_rate=summary["win_rate"],
                strategy=summary["strategy"],
                max_drawdown=summary.get("max_drawdown", 0),
            )
            logger.info("일일 보고서 발송 완료")
        else:
            await self._notifier.send_no_trade("당일 매매 기록 없음")
            logger.info("당일 매매 없음 -- 무거래 알림 발송")

    # ── Universe/strategies/OHLCV helpers (startup + daily_reset 공용) ──

    def _load_universe(self) -> list[dict]:
        """universe.yaml 로드 + _ticker_markets 매핑 갱신."""
        import yaml
        from pathlib import Path
        uni_path = Path("config/universe.yaml")
        if not uni_path.exists():
            logger.error(f"universe.yaml 없음: {uni_path}")
            return []
        uni = yaml.safe_load(open(uni_path, encoding="utf-8")) or {}
        stocks = uni.get("stocks", [])
        self._ticker_markets = {
            s["ticker"]: s.get("market", "unknown") for s in stocks
        }
        self._ticker_names = {
            s["ticker"]: s.get("name", s["ticker"]) for s in stocks
        }
        return stocks

    def _register_active_strategies(self, stocks: list[dict]) -> None:
        """유니버스 종목에 Momentum 전략 인스턴스 등록 (기존 인스턴스 교체)."""
        from strategy.momentum_strategy import MomentumStrategy

        force = getattr(self._config, 'force_strategy', '') or 'momentum'
        if force != 'momentum':
            logger.warning(f"force_strategy={force} 무시 — momentum만 지원")

        self._active_strategies = {}
        for s in stocks:
            ticker = s["ticker"]
            strat = MomentumStrategy(self._config.trading)
            strat.configure_multi_trade(
                max_trades=self._config.trading.max_trades_per_day,
                cooldown_minutes=self._config.trading.cooldown_minutes,
            )
            if hasattr(strat, "set_ticker"):
                strat.set_ticker(ticker)
            self._active_strategies[ticker] = {
                "strategy": strat,
                "name": s.get("name", ticker),
                "score": 0,
            }
        self._active_strategy = (
            list(self._active_strategies.values())[0]["strategy"]
            if self._active_strategies else None
        )
        logger.info(f"유니버스 전체 전략 등록: {len(self._active_strategies)}종목 ({force})")

    async def _refresh_prev_day_ohlcv(self, stocks: list[dict] | None = None) -> None:
        """각 strategy에 전일 OHLCV 주입. startup + 08:05 cron + daily_reset 공용."""
        if stocks is None:
            stocks = self._load_universe()
        if not stocks:
            return
        logger.info(f"전일 OHLCV 갱신 시작 — {len(stocks)}종목")
        init_count = 0
        lu_api_count = 0
        lu_fallback_count = 0
        for s in stocks:
            ticker = s["ticker"]
            try:
                daily = await self._rest_client.get_daily_ohlcv(
                    ticker, base_dt=datetime.now().strftime('%Y%m%d'),
                )
                items = (
                    daily.get("stk_dt_pole_chart_qry")
                    or daily.get("output2")
                    or daily.get("output")
                    or []
                )
                if items and len(items) >= 2:
                    prev = items[1]
                    prev_high = abs(float(prev.get("high_pric", 0)))
                    prev_vol = abs(int(
                        prev.get("trde_qty",
                        prev.get("acml_vol",
                        prev.get("acml_vlmn", 0)))
                    ))
                    logger.info(
                        f"[OHLCV-DBG] {ticker} prev_high={prev_high} prev_vol={prev_vol} "
                        f"raw_keys={list(prev.keys())[:10]}"
                    )
                    prev_close = abs(float(prev.get("cur_prc", prev.get("stck_clpr", 0))))
                    if prev_high > 0 and ticker in self._active_strategies:
                        strat = self._active_strategies[ticker]["strategy"]
                        if hasattr(strat, "set_prev_day_data"):
                            strat.set_prev_day_data(prev_high, prev_vol)
                            init_count += 1
                        self._prev_high_map[ticker] = prev_high
                    if prev_close > 0:
                        self._prev_close[ticker] = prev_close
                        # 상한가: 1차 ka10001 upl_pric 사용, 실패 시 전일종가 × 1.30 호가 절사
                        lu_val: float | None = None
                        try:
                            api_lu = await self._rest_client.get_limit_up_price(ticker)
                            if api_lu and api_lu > 0:
                                lu_val = float(api_lu)
                                lu_api_count += 1
                        except Exception as e:
                            logger.debug(f"상한가 API 실패 ({ticker}): {e}")
                        if lu_val is None:
                            try:
                                from core.price_utils import calculate_limit_up_price
                                lu_pct = getattr(self._config.trading, "limit_up_pct", 0.30)
                                calc = calculate_limit_up_price(prev_close, lu_pct)
                                if calc > 0:
                                    lu_val = float(calc)
                                    lu_fallback_count += 1
                            except Exception as e:
                                logger.debug(f"상한가 계산 실패 ({ticker}): {e}")
                        if lu_val is not None:
                            self._limit_up_map[ticker] = lu_val
            except Exception as e:
                logger.debug(f"전일 OHLCV 실패 ({ticker}): {e}")
            await asyncio.sleep(0.1)
        logger.info(
            f"전일 OHLCV 갱신 완료: {init_count}/{len(stocks)} "
            f"(상한가 {len(self._limit_up_map)}종 "
            f"— API {lu_api_count} / fallback {lu_fallback_count})"
        )

    async def _check_uptime_sanity(self) -> None:
        """GUI 24시간 이상 가동 시 안내 알림 — ADR-006 안전망.

        logs/.last_startup 파일에 이전 시작 시각 기록. 현재 시각과
        비교하여 24시간 이상 경과했으면 텔레그램으로 안내. 항상 현재
        시각을 파일에 갱신.
        """
        from datetime import datetime as _dt, timedelta as _td
        from pathlib import Path as _Path
        marker = _Path("logs/.last_startup")
        now = _dt.now()
        prev_str = None
        if marker.exists():
            try:
                prev_str = marker.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        try:
            marker.parent.mkdir(exist_ok=True)
            marker.write_text(now.isoformat(), encoding="utf-8")
        except Exception as e:
            logger.warning(f"last_startup 기록 실패: {e}")
        if not prev_str:
            return
        try:
            prev = _dt.fromisoformat(prev_str)
        except Exception:
            return
        elapsed = now - prev
        if elapsed >= _td(hours=24):
            hours = int(elapsed.total_seconds() / 3600)
            logger.warning(f"[SANITY] GUI {hours}시간 이상 가동 중 (마지막 시작: {prev_str})")
            if self._notifier and self._config.notifications.uptime_sanity:
                try:
                    await self._notifier.send(
                        f"[안내] GUI {hours}시간 이상 가동 중\n마지막 시작: {prev_str}"
                    )
                except Exception as e:
                    logger.warning(f"uptime sanity 알림 실패: {e}")

    async def _daily_reset(self) -> None:
        """00:01 자동 일일 리셋 — 운영자 재시작 안전망 (ADR-006).

        - 리스크 카운터 리셋 (포지션 보존)
        - active_strategies 재등록 또는 기존 인스턴스 reset()
        - 전일 OHLCV 갱신
        """
        logger.info("[자동] 일일 리셋 시작")
        self._risk_manager.reset_daily_counters()
        self._daily_halt_notified = False

        stocks = self._load_universe()
        if not self._active_strategies:
            self._register_active_strategies(stocks)
        else:
            for strat_info in self._active_strategies.values():
                strat_info["strategy"].reset()

        await self._refresh_prev_day_ohlcv(stocks)

        logger.info("[자동] 일일 리셋 완료")
        if self._notifier and self._config.notifications.daily_reset:
            try:
                await self._notifier.send(
                    f"[자동] 일일 리셋 완료 — {len(self._active_strategies)}종목, 카운터 초기화"
                )
            except Exception as e:
                logger.warning(f"일일 리셋 알림 실패: {e}")

    # ── Scheduler safe wrappers ──

    async def _safe_refresh_token(self):
        try:
            await self._refresh_token()
        except Exception as e:
            logger.error(f"[SCHED] 토큰 갱신 실패: {e}")

    async def _safe_run_screening(self):
        try:
            await self._run_screening()
        except Exception as e:
            logger.error(f"[SCHED] 스크리닝 실패: {e}")

    async def _safe_force_close(self):
        try:
            await self._force_close()
        except Exception as e:
            logger.error(f"[SCHED] 강제 청산 실패: {e}")

    async def _safe_run_daily_report(self):
        try:
            await self._run_daily_report()
        except Exception as e:
            logger.error(f"[SCHED] 일일 보고서 실패: {e}")

    async def _safe_daily_reset(self):
        try:
            await self._daily_reset()
        except Exception as e:
            logger.error(f"[SCHED] 일일 리셋 실패: {e}")

    async def _safe_refresh_ohlcv(self):
        try:
            await self._refresh_prev_day_ohlcv()
            # ADR-008: 성공 알림
            if self._notifier and self._config.notifications.ohlcv_refresh:
                try:
                    await self._notifier.send(
                        f"[자동] 08:05 전일 OHLCV 갱신 완료 — {len(self._active_strategies)}종목"
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"[SCHED] OHLCV 갱신 실패: {e}")
            if self._notifier and self._config.notifications.ohlcv_refresh:
                try:
                    await self._notifier.send_urgent(
                        f"[경고] 전일 OHLCV 갱신 실패 — {type(e).__name__}: {e}"
                    )
                except Exception:
                    pass

    async def _safe_refresh_universe(self):
        """ADR-012: 주간 유니버스 자동 갱신 (월 07:30).

        임시 비활성화 (2026-04-17): 추세 필터 검증 대기.
        Wilder ATR만으로 갱신 시 PF 3.41 → 2.24로 악화 확인.
        추세 필터 + 시총 상한 백테스트 PF ≥ 3.0 확인 후 재활성화.
        """
        logger.warning(
            "[UNIVERSE] 주간 자동 갱신 건너뜀 — 추세 필터 구현/검증 대기"
        )
        if self._notifier and self._config.notifications.universe_refresh:
            try:
                await self._notifier.send_urgent(
                    "[알림] 주간 유니버스 갱신 건너뜀\n"
                    "사유: 추세 필터 구현/검증 대기 (PF 유효성 확인 후 재활성화)"
                )
            except Exception:
                pass
        return

        try:
            await self._refresh_universe()
        except Exception as e:
            logger.error(f"[SCHED] 유니버스 갱신 실패: {e}")
            if self._notifier and self._config.notifications.universe_refresh:
                try:
                    await self._notifier.send_urgent(
                        f"[경고] 유니버스 갱신 실패 — {type(e).__name__}: {e}"
                    )
                except Exception:
                    pass

    async def _refresh_universe(self):
        """유니버스 재생성 + 전략 재등록 + 신규 종목 분봉 수집."""
        import subprocess
        import yaml
        from pathlib import Path

        logger.info("[UNIVERSE] 주간 유니버스 갱신 시작")

        # 1. 기존 유니버스 백업
        uni_path = Path("config/universe.yaml")
        old_stocks = []
        if uni_path.exists():
            old_data = yaml.safe_load(open(uni_path, encoding="utf-8")) or {}
            old_stocks = old_data.get("stocks", [])
        old_tickers = {s["ticker"] for s in old_stocks}

        # 2. generate_universe.py subprocess 실행
        result = subprocess.run(
            ["python", "scripts/generate_universe.py", "--min-atr", "0.06", "--max-stocks", "40"],
            capture_output=True, text=True, timeout=300, encoding="utf-8",
        )
        if result.returncode != 0:
            raise RuntimeError(f"generate_universe.py 실패: {result.stderr[-500:]}")

        # 3. 새 유니버스 로드 + 변경 종목 식별
        new_stocks = self._load_universe()
        new_tickers = {s["ticker"] for s in new_stocks}
        added = new_tickers - old_tickers
        removed = old_tickers - new_tickers

        # 4. 신규 종목 분봉 수집 (batch_collector)
        collected_count = 0
        if added:
            try:
                from backtest.data_collector import DataCollector
                collector = DataCollector(self._rest_client, self._db)
                for ticker in added:
                    try:
                        saved = await collector.collect_minute_candles(ticker, days=30)
                        collected_count += saved
                    except Exception as e:
                        logger.warning(f"[UNIVERSE] 분봉 수집 실패 ({ticker}): {e}")
            except Exception as e:
                logger.error(f"[UNIVERSE] batch 분봉 수집 실패: {e}")

        # 4.5 ticker_atr 갱신 — universe 변경 시 필수
        # generate_universe(KRX API)와 ticker_atr(intraday_candles)의 소스 차이가
        # 있으나 계산식은 동일(Wilder, core.indicators.calculate_atr).
        try:
            atr_result = subprocess.run(
                ["python", "scripts/calculate_atr.py"],
                capture_output=True, text=True, timeout=600, encoding="utf-8",
            )
            if atr_result.returncode != 0:
                logger.warning(
                    f"[UNIVERSE] calculate_atr.py 실패: {atr_result.stderr[-300:]}"
                )
            else:
                logger.info("[UNIVERSE] ticker_atr 갱신 완료")
        except Exception as e:
            logger.warning(f"[UNIVERSE] ticker_atr 갱신 오류: {e}")

        # 5. 전략 재등록 + WS 재구독
        self._register_active_strategies(new_stocks)
        all_tickers = [s["ticker"] for s in new_stocks]
        if self._ws_client and all_tickers:
            try:
                await self._ws_client.subscribe(all_tickers)
            except Exception as e:
                logger.warning(f"[UNIVERSE] WS 재구독 실패: {e}")

        # 6. 전일 OHLCV 갱신
        await self._refresh_prev_day_ohlcv(new_stocks)

        # 7. 텔레그램 알림
        logger.info(
            f"[UNIVERSE] 갱신 완료: {len(new_stocks)}종목 "
            f"(+{len(added)} -{len(removed)})"
        )
        if self._notifier and self._config.notifications.universe_refresh:
            added_names = []
            new_map = {s["ticker"]: s.get("name", s["ticker"]) for s in new_stocks}
            for t in sorted(added):
                added_names.append(f"  +{new_map.get(t, t)}")
            removed_names = []
            old_map = {s["ticker"]: s.get("name", s["ticker"]) for s in old_stocks}
            for t in sorted(removed):
                removed_names.append(f"  -{old_map.get(t, t)}")

            msg_lines = [
                f"[UNIVERSE] 주간 갱신 완료",
                f"종목 수: {len(old_stocks)} → {len(new_stocks)}",
                f"추가: {len(added)} / 제거: {len(removed)}",
            ]
            if added_names:
                msg_lines.extend(added_names[:10])
            if removed_names:
                msg_lines.extend(removed_names[:10])
            if collected_count > 0:
                msg_lines.append(f"신규 분봉: {collected_count:,}개 수집")
            try:
                await self._notifier.send("\n".join(msg_lines))
            except Exception:
                pass

    async def _safe_collect_candles(self):
        """ADR-014: 일일 분봉 자동 수집 (평일 15:35)."""
        try:
            await self._collect_daily_candles()
        except Exception as e:
            logger.error(f"[SCHED] 분봉 수집 실패: {e}")
            if self._notifier and self._config.notifications.candle_collection:
                try:
                    await self._notifier.send_urgent(
                        f"[경고] 분봉 수집 실패 — {type(e).__name__}: {e}"
                    )
                except Exception:
                    pass

    async def _collect_daily_candles(self):
        """유니버스 전체 당일 분봉 수집."""
        from backtest.data_collector import DataCollector

        logger.info("[CANDLE] 일일 분봉 수집 시작")

        stocks = self._load_universe()
        if not stocks:
            logger.warning("[CANDLE] 유니버스 비어 있음")
            return

        collector = DataCollector(self._rest_client, self._db)
        success = 0
        failed = 0
        total_saved = 0

        for s in stocks:
            ticker = s["ticker"]
            try:
                saved = await collector.collect_minute_candles(ticker, days=1)
                total_saved += saved
                success += 1
            except Exception as e:
                logger.warning(f"[CANDLE] {ticker} 수집 실패: {e}")
                failed += 1

        logger.info(
            f"[CANDLE] 수집 완료: {success}/{len(stocks)}종목, "
            f"{total_saved:,}개 캔들, 실패 {failed}"
        )

        if self._notifier and self._config.notifications.candle_collection:
            try:
                await self._notifier.send(
                    f"[CANDLE] 분봉 수집 완료\n"
                    f"성공: {success}/{len(stocks)}종목\n"
                    f"캔들: {total_saved:,}개\n"
                    f"실패: {failed}종목"
                )
            except Exception:
                pass

    # ── Health check ──

    _TASK_FACTORIES = {
        "tick_consumer": "_tick_consumer",
        "candle_consumer": "_candle_consumer",
        "signal_consumer": "_signal_consumer",
        "order_consumer": "_order_confirmation_consumer",
    }

    def _health_check(self):
        """스케줄러 + WS + 파이프라인 태스크 생존 확인 (polling loop에서 30초마다 호출)."""
        try:
            # 스케줄러 생존 확인
            if self._scheduler and not self._scheduler.running:
                logger.warning("스케줄러 죽음 감지 — 재시작 시도")
                try:
                    self._scheduler.start()
                    logger.info("스케줄러 재시작 완료")
                except Exception as e:
                    logger.error(f"스케줄러 재시작 실패: {e}")

            # WS 연결 확인
            if self._ws_client and not self._ws_client.connected:
                logger.warning("WS 연결 끊김 감지")

            # 파이프라인 태스크 생존 확인
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
                        self._pipeline_tasks.append(
                            asyncio.create_task(method(), name=name)
                        )
                logger.info(f"파이프라인 태스크 재시작 완료: {len(self._pipeline_tasks)}개")
        except Exception as e:
            logger.error(f"헬스 체크 오류: {e}")

    # ── UI -> Worker command handlers (thread-safe) ──

    def _on_request_stop(self):
        """엔진 정상 종료."""
        logger.info("엔진 종료 요청 수신 (UI thread)")
        self._running = False

        # 스케줄러 즉시 정지
        try:
            if self._scheduler and self._scheduler.running:
                self._scheduler.shutdown(wait=False)
        except Exception:
            pass

        # asyncio.Event를 이벤트 루프 스레드에서 set — 즉시 깨어남
        if self._loop and self._loop.is_running() and self._stop_event:
            try:
                self._loop.call_soon_threadsafe(self._stop_event.set)
            except Exception:
                pass

    def _on_request_halt(self):
        """매매 긴급 정지 (포지션 유지, 신규 매매만 중단)."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_halt(), self._loop)

    async def _async_halt(self):
        """halt 처리."""
        if self._risk_manager:
            self._risk_manager._halted = True
            logger.warning("매매 긴급 정지 활성화")
            self._emit_status()

    def _on_request_screening(self):
        """수동 스크리닝."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._run_screening(), self._loop)

    def _on_request_force_close(self):
        """전체 포지션 강제 청산."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._force_close(), self._loop)

    def _on_request_report(self):
        """일일 리포트 수동 발송."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._run_daily_report(), self._loop)

    def _on_request_reconnect(self):
        """WS 재연결."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_reconnect(), self._loop)

    async def _async_reconnect(self):
        """WS disconnect + reconnect."""
        if self._ws_client:
            try:
                await self._ws_client.disconnect()
                await self._ws_client.connect()
                logger.info("WS 재연결 완료")
            except Exception as e:
                logger.error(f"WS 재연결 실패: {e}")

    def _on_request_strategy_change(self, strategy_name: str):
        """전략 변경 요청 처리."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._async_strategy_change(strategy_name), self._loop,
            )

    async def _async_strategy_change(self, strategy_name: str):
        """force_strategy 변경 — 현재는 momentum만 지원. 이외 요청은 무시."""
        from strategy.momentum_strategy import MomentumStrategy

        if self._config:
            object.__setattr__(self._config, "force_strategy", strategy_name)

        if strategy_name and strategy_name != "momentum":
            logger.warning(f"전략 변경 요청 무시: {strategy_name} — momentum만 지원")
        elif strategy_name == "momentum":
            # 기존 인스턴스 교체 (prev_day_data 보존)
            for ticker, info in self._active_strategies.items():
                old_strat = info["strategy"]
                new_strat = MomentumStrategy(self._config.trading)
                new_strat.configure_multi_trade(
                    max_trades=self._config.trading.max_trades_per_day,
                    cooldown_minutes=self._config.trading.cooldown_minutes,
                )
                if hasattr(new_strat, "set_prev_day_data"):
                    prev_high = getattr(old_strat, "_prev_day_high", 0.0)
                    prev_vol = getattr(old_strat, "_prev_day_volume", 0)
                    if prev_high > 0:
                        new_strat.set_prev_day_data(prev_high, prev_vol)
                info["strategy"] = new_strat
            self._active_strategy = (
                list(self._active_strategies.values())[0]["strategy"]
                if self._active_strategies else MomentumStrategy(self._config.trading)
            )
            logger.info("전략 수동 변경: momentum")
        elif not strategy_name:
            logger.info("전략 Auto 모드로 전환 — 다음 스크리닝에서 자동 선택")

        self._emit_status()

    def _on_request_daily_reset(self):
        """일일 리셋."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_daily_reset(), self._loop)

    async def _async_daily_reset(self):
        """risk_manager + candle_builder 리셋."""
        if self._risk_manager:
            self._risk_manager.reset_daily()
        self._daily_halt_notified = False
        if self._candle_builder:
            self._candle_builder.reset()
        self._candle_history.clear()
        self._active_strategy = None
        logger.info("일일 리셋 완료")
        self._emit_status()

    # ── Data emission (2-second polling) ──

    def _emit_status(self):
        """현재 엔진 상태를 시그널로 전송."""
        strategy_name = ""
        target_ticker = ""
        target_name = ""
        if self._active_strategy:
            strategy_name = type(self._active_strategy).__name__
        if self._ws_client and hasattr(self._ws_client, "_subscriptions"):
            from core.kiwoom_ws import WS_TYPE_TICK
            subs = self._ws_client._subscriptions.get(WS_TYPE_TICK, [])
            if subs:
                target_ticker = subs[0]

        force = ""
        if self._config:
            force = getattr(self._config, "force_strategy", "")

        # 대시보드 서머리용 데이터
        rm = self._risk_manager
        daily_pnl = rm._daily_pnl if rm else 0.0
        capital = rm._daily_capital if rm and rm._daily_capital > 0 else 1
        daily_pnl_pct = (daily_pnl / capital) * 100 if capital else 0
        max_trades = self._config.trading.max_trades_per_day if self._config else 3
        # 전략의 거래 카운트 사용
        strat = self._active_strategy
        trades_count = strat._trade_count if strat else 0
        # DB 기반이 아닌 런타임 추적용
        wins = getattr(self, "_rt_wins", 0)
        losses = getattr(self, "_rt_losses", 0)
        win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

        active_count = len(self._active_strategies)
        positions_count = len(rm.get_open_positions()) if rm else 0
        max_pos = self._config.trading.max_positions if self._config else 3

        available_cap = rm.available_capital if rm else 0
        initial_cap = self._config.trading.initial_capital if self._config else 0

        self.signals.status_updated.emit({
            "mode": self._mode,
            "running": self._running,
            "halted": rm._halted if rm else False,
            "strategy": strategy_name,
            "target": target_ticker,
            "target_name": target_name,
            "force_strategy": force,
            "positions_count": positions_count,
            "max_positions": max_pos,
            "active_count": active_count,
            "watched_tickers": list(self._active_strategies.keys())[:5],
            "ws_connected": self._ws_client.connected if self._ws_client else False,
            "daily_pnl": daily_pnl,
            "daily_pnl_pct": daily_pnl_pct,
            "trades_count": trades_count,
            "max_trades": max_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "available_capital": available_cap,
            "initial_capital": initial_cap,
            "open_positions_count": positions_count,
        })

    def _emit_positions(self):
        """포지션 목록을 시그널로 전송."""
        if not self._risk_manager:
            return
        try:
            open_pos = self._risk_manager.get_open_positions()
            current_tickers = sorted(open_pos.keys())
            if current_tickers != self._last_pos_tickers:
                if current_tickers:
                    logger.info(f"[POS] 보유 포지션: {len(current_tickers)}건 — {current_tickers}")
                else:
                    logger.info("[POS] 보유 포지션: 0건")
                self._last_pos_tickers = current_tickers
            positions = []
            for ticker, pos in open_pos.items():
                entry = pos["entry_price"]
                current = self._latest_prices.get(ticker, entry)
                pnl_pct = ((current - entry) / entry * 100) if entry > 0 else 0
                status = "TP1 hit" if pos.get("tp1_hit") else "보유 중"
                name = self._active_strategies.get(ticker, {}).get("name", "")
                positions.append({
                    "ticker": ticker,
                    "name": name,
                    "strategy": pos.get("strategy", ""),
                    "entry_price": entry,
                    "current_price": current,
                    "pnl_pct": pnl_pct,
                    "qty": pos["qty"],
                    "remaining_qty": pos["remaining_qty"],
                    "stop_loss": pos["stop_loss"],
                    "tp1_price": pos.get("tp1_price"),
                    "tp1_hit": pos.get("tp1_hit", False),
                    "breakeven_active": pos.get("breakeven_active", False),
                    "highest_price": pos.get("highest_price", entry),
                    "entry_time": pos.get("entry_time"),
                    "status": status,
                })
            self.signals.positions_updated.emit(positions)
        except Exception as e:
            logger.error(f"포지션 emit 실패: {e}")

    def _emit_trades(self):
        """당일 체결 내역을 시그널로 전송."""
        if not self._db or not self._loop:
            return
        if getattr(self, "_trades_fetch_running", False):
            return  # 이전 조회가 아직 진행 중
        try:
            self._trades_fetch_running = True
            asyncio.run_coroutine_threadsafe(
                self._fetch_and_emit_trades(), self._loop,
            )
        except Exception as e:
            logger.debug(f"체결 내역 조회 스케줄 실패: {e}")
            self._trades_fetch_running = False

    async def _fetch_and_emit_trades(self):
        """DB에서 당일 체결 내역 조회 후 시그널 전송."""
        try:
            trades = await asyncio.wait_for(self._fetch_today_trades(), timeout=5.0)
            self.signals.trades_updated.emit(trades)
        except asyncio.TimeoutError:
            logger.warning("당일 체결 조회 타임아웃")
        except Exception as e:
            logger.error(f"당일 체결 조회 오류: {e}")
        finally:
            self._trades_fetch_running = False

    async def _fetch_today_trades(self) -> list[dict]:
        """DB에서 당일 체결 내역 조회 + 종목명 매핑."""
        today = datetime.now().strftime("%Y-%m-%d")
        trades = await self._db.fetch_all(
            "SELECT * FROM trades WHERE traded_at LIKE ? || '%' ORDER BY traded_at DESC",
            (today,),
        )
        # 종목명 매핑: active_strategies 우선, fallback으로 유니버스 전체 맵
        for trade in trades:
            ticker = trade.get("ticker", "")
            if ticker in self._active_strategies:
                trade["name"] = self._active_strategies[ticker].get("name", "")
            elif ticker in self._ticker_names:
                trade["name"] = self._ticker_names[ticker]
        return trades

    def _emit_pnl(self):
        """일일 손익을 시그널로 전송."""
        if not self._risk_manager:
            return
        try:
            self.signals.pnl_updated.emit(self._risk_manager._daily_pnl)
        except Exception as e:
            logger.debug(f"PnL emit 실패: {e}")

    def _emit_watchlist(self):
        """유니버스 전체를 watchlist로 emit (현재가, 등락%, 돌파% 포함)."""
        if not self._active_strategies:
            return
        try:
            open_pos_tickers: set[str] = set()
            if self._risk_manager:
                open_pos_tickers = set(self._risk_manager.get_open_positions().keys())

            items = []
            for ticker, info in self._active_strategies.items():
                current = self._latest_prices.get(ticker, 0)
                prev_close = self._prev_close.get(ticker, 0)
                prev_high = self._prev_high_map.get(ticker, 0)

                change_pct = ((current / prev_close) - 1) * 100 if prev_close > 0 and current > 0 else 0
                breakout_pct = ((current / prev_high) - 1) * 100 if prev_high > 0 and current > 0 else -999

                items.append({
                    "ticker": ticker,
                    "name": info.get("name", ticker),
                    "current_price": current,
                    "change_pct": change_pct,
                    "prev_high": prev_high,
                    "breakout_pct": breakout_pct,
                    "has_position": ticker in open_pos_tickers,
                })

            # 돌파% 내림차순 (신호 임박 순)
            items.sort(key=lambda x: x["breakout_pct"], reverse=True)
            self.signals.watchlist_updated.emit(items)
        except Exception as e:
            logger.debug(f"watchlist emit 실패: {e}")

    def _emit_candidates(self):
        """스크리너 후보 목록 + 실시간 가격을 시그널로 전송."""
        try:
            enriched = []
            for c in self._screener_results:
                ticker = c.get("ticker", "")
                current_price = self._latest_prices.get(ticker, 0)
                prev_close = c.get("prev_close", 0)
                if prev_close > 0 and current_price > 0:
                    change_pct = ((current_price - prev_close) / prev_close * 100)
                else:
                    change_pct = 0
                enriched.append({
                    **c,
                    "current_price": current_price,
                    "change_pct": round(change_pct, 2),
                })
            self.signals.candidates_updated.emit(enriched)
        except Exception as e:
            logger.debug(f"후보 종목 emit 실패: {e}")

    # ── Cleanup ──

    def _cleanup_sync(self):
        """최대 3초 내 클린업 완료."""
        if not self._loop or self._loop.is_closed():
            return

        import time as _time
        deadline = _time.time() + 3.0

        def _safe_run(coro, label: str):
            remaining = deadline - _time.time()
            if remaining <= 0:
                logger.warning(f"클린업 시간 초과, {label} 스킵")
                return
            timeout = min(remaining, 1.0)
            try:
                self._loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout))
            except asyncio.TimeoutError:
                logger.warning(f"클린업 타임아웃 ({label})")
            except Exception as e:
                logger.warning(f"클린업 오류 ({label}): {e}")

        # 1. 잔여 태스크 취소 + 취소 처리
        try:
            for t in asyncio.all_tasks(self._loop):
                t.cancel()
            self._loop.run_until_complete(asyncio.sleep(0.1))
        except Exception:
            pass

        # 2. 스케줄러
        try:
            if self._scheduler and self._scheduler.running:
                self._scheduler.shutdown(wait=False)
        except Exception:
            pass

        # 3. WS
        if self._ws_client:
            _safe_run(self._ws_client.disconnect(), "ws")

        # 4. 텔레그램
        if self._notifier:
            if self._config and self._config.notifications.system_stop:
                mode_tag = "[PAPER] " if self._mode == "paper" else ""
                _safe_run(self._notifier.send(f"{mode_tag}시스템 종료 (GUI)"), "notify")
            _safe_run(self._notifier.aclose(), "notifier_close")

        # 5. REST / DB
        if self._rest_client:
            _safe_run(self._rest_client.aclose(), "rest")
        if self._db:
            _safe_run(self._db.close(), "db")

        logger.info("클린업 완료")

    @property
    def engine_running(self) -> bool:
        """엔진 실행 중 여부."""
        return self._running

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        """asyncio 이벤트 루프 (외부 thread-safe 호출용)."""
        return self._loop
