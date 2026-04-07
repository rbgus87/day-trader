"""TradingEngine을 별도 스레드에서 asyncio로 실행하는 QThread 래퍼.

main.py의 파이프라인 로직을 QThread 내에서 실행.
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
        self._strategy_selector = None

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

    # ── Core async engine ──

    async def _run_engine(self):
        """Initialize components and start pipeline (ported from main.py)."""
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
        from screener.strategy_selector import StrategySelector
        from apscheduler.schedulers.background import BackgroundScheduler

        # 1. Config
        self._config = AppConfig.from_yaml()
        paper_mode = self._mode == "paper"

        # 2. Infrastructure
        self._db = DbManager(self._config.db_path)
        await self._db.init()

        self._notifier = TelegramNotifier(self._config.telegram)
        mode_tag = "[PAPER] " if paper_mode else ""
        await self._notifier.send(f"{mode_tag}단타 매매 시스템 시작 (GUI)")

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
            )
            logger.info("주문 관리자: PaperOrderManager (시뮬레이션)")
        else:
            self._order_manager = OrderManager(
                rest_client=self._rest_client,
                risk_manager=self._risk_manager,
                notifier=self._notifier, db=self._db,
                trading_config=self._config.trading,
                order_queue=self._order_queue,
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
        self._strategy_selector = StrategySelector(self._config, self._rest_client)

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
        self._scheduler.start()
        logger.debug(f"BackgroundScheduler 시작됨, running={self._scheduler.running}")

        # Late screening (장중 실행 시 즉시 스크리닝 — 점수 업데이트 + 현재가 초기화)
        now = datetime.now().time()
        if dt_time(8, 30) < now < dt_time(15, 10):
            logger.info("장중 실행 감지 — 즉시 스크리닝 시작")
            await self._run_screening()

        # Position reconciliation (장애 복구)
        try:
            api_balance = await self._rest_client.get_account_balance()
            holdings = [
                {"ticker": h["pdno"], "qty": int(h["hldg_qty"])}
                for h in api_balance.get("output1", [])
                if int(h.get("hldg_qty", 0)) > 0
            ]
            mismatches = await self._risk_manager.reconcile_positions(holdings)
            if mismatches:
                await self._notifier.send_urgent(
                    f"포지션 불일치 감지!\n" + "\n".join(mismatches)
                )
        except Exception as e:
            logger.error(f"장애 복구 점검 실패: {e}")

        await self._risk_manager.check_consecutive_losses()

        # WS connect + 유니버스 전체 구독 + 전략 등록
        try:
            await self._ws_client.connect()
            import yaml
            from pathlib import Path
            from strategy.momentum_strategy import MomentumStrategy
            from strategy.pullback_strategy import PullbackStrategy
            from strategy.flow_strategy import FlowStrategy
            from strategy.gap_strategy import GapStrategy
            from strategy.open_break_strategy import OpenBreakStrategy
            from strategy.big_candle_strategy import BigCandleStrategy

            uni_path = Path("config/universe.yaml")
            all_stocks = []
            if uni_path.exists():
                uni = yaml.safe_load(open(uni_path, encoding="utf-8")) or {}
                all_stocks = uni.get("stocks", [])
                all_tickers = [s["ticker"] for s in all_stocks]
                if all_tickers:
                    await self._ws_client.subscribe(all_tickers)
                    logger.info(f"유니버스 전체 WS 구독: {len(all_tickers)}종목")

            # 유니버스 전체에 전략 인스턴스 생성
            force = getattr(self._config, 'force_strategy', '') or 'momentum'
            strategy_classes = {
                "momentum": MomentumStrategy,
                "pullback": PullbackStrategy,
                "flow": FlowStrategy,
                "gap": GapStrategy,
                "open_break": OpenBreakStrategy,
                "big_candle": BigCandleStrategy,
            }
            StratClass = strategy_classes.get(force, MomentumStrategy)

            self._active_strategies = {}
            for s in all_stocks:
                ticker = s["ticker"]
                strat = StratClass(self._config.trading)
                strat.configure_multi_trade(
                    max_trades=self._config.trading.max_trades_per_day,
                    cooldown_minutes=self._config.trading.cooldown_minutes,
                )
                self._active_strategies[ticker] = {
                    "strategy": strat,
                    "name": s.get("name", ticker),
                    "score": 0,
                }
            self._active_strategy = list(self._active_strategies.values())[0]["strategy"] if self._active_strategies else None
            logger.info(f"유니버스 전체 전략 등록: {len(self._active_strategies)}종목 ({force})")

            # 전일 고가/거래량 초기화 (모멘텀 전략 등에 필요)
            logger.info("전일 고가 초기화 시작...")
            init_count = 0
            for s in all_stocks:
                ticker = s["ticker"]
                try:
                    daily = await self._rest_client.get_daily_ohlcv(ticker, base_dt=datetime.now().strftime('%Y%m%d'))
                    items = (
                        daily.get("stk_dt_pole_chart_qry")
                        or daily.get("output2")
                        or daily.get("output")
                        or []
                    )
                    if items and len(items) >= 2:
                        prev = items[1]
                        prev_high = abs(float(prev.get("high_pric", 0)))
                        prev_vol = abs(int(prev.get("acml_vol", prev.get("acml_vlmn", 0))))
                        if prev_high > 0 and ticker in self._active_strategies:
                            strat = self._active_strategies[ticker]["strategy"]
                            if hasattr(strat, "set_prev_day_data"):
                                strat.set_prev_day_data(prev_high, prev_vol)
                                init_count += 1
                except Exception as e:
                    logger.debug(f"전일 고가 조회 실패 ({ticker}): {e}")
                await asyncio.sleep(0.1)
            logger.info(f"전일 고가 초기화 완료: {init_count}/{len(self._active_strategies)}종목")
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

        # 일일 성과 히스토리 전송 (1회)
        try:
            await asyncio.wait_for(self._emit_daily_history(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("일일 성과 히스토리 조회 타임아웃 — 스킵")
        except Exception as e:
            logger.warning(f"일일 성과 히스토리 조회 실패: {e}")

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

    # ── Pipeline consumers (ported from main.py) ──

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
                # 손절 체크
                if self._risk_manager.check_stop_loss(ticker, price):
                    qty = pos["remaining_qty"]
                    await self._order_manager.execute_sell_stop(ticker=ticker, qty=qty)
                    pnl = (price - pos["entry_price"]) * qty
                    self._risk_manager.record_pnl(pnl)
                    self._risk_manager.remove_position(ticker)
                    if pnl >= 0:
                        self._rt_wins += 1
                    else:
                        self._rt_losses += 1
                    logger.info(f"손절 실행: {ticker} {qty}주 @ {price:,} PnL={pnl:+,.0f}")
                    self.signals.trade_executed.emit({
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "side": "sell", "ticker": ticker,
                        "price": int(price), "qty": qty,
                        "pnl": int(pnl), "reason": "stop_loss",
                    })
                    continue
                # TP1 체크
                if self._risk_manager.check_tp1(ticker, price):
                    sell_qty = int(pos["remaining_qty"] * self._config.trading.tp1_sell_ratio)
                    await self._order_manager.execute_sell_tp1(
                        ticker=ticker, price=int(price), remaining_qty=pos["remaining_qty"],
                    )
                    pnl = (price - pos["entry_price"]) * sell_qty
                    self._risk_manager.record_pnl(pnl)
                    self._risk_manager.mark_tp1_hit(ticker, sell_qty)
                    self._rt_wins += 1
                    logger.info(f"TP1 실행: {ticker} {sell_qty}주 @ {price:,} PnL={pnl:+,.0f}")
                    self.signals.trade_executed.emit({
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "side": "sell", "ticker": ticker,
                        "price": int(price), "qty": sell_qty,
                        "pnl": int(pnl), "reason": "tp1",
                    })
                    continue
                # 시간 손절
                if self._risk_manager.check_time_stop(
                    ticker, price,
                    self._config.trading.time_stop_minutes,
                    self._config.trading.time_stop_min_profit,
                ):
                    qty = pos["remaining_qty"]
                    await self._order_manager.execute_sell_force_close(ticker=ticker, qty=qty)
                    pnl = (price - pos["entry_price"]) * qty
                    self._risk_manager.record_pnl(pnl)
                    self._risk_manager.remove_position(ticker)
                    if pnl >= 0:
                        self._rt_wins += 1
                    else:
                        self._rt_losses += 1
                    logger.info(f"시간 손절: {ticker} {qty}주 @ {price:,} PnL={pnl:+,.0f}")
                    if self._notifier:
                        await self._notifier.send(
                            f"⏰ 시간 손절: {ticker} {self._config.trading.time_stop_minutes}분 경과"
                        )
                    self.signals.trade_executed.emit({
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "side": "sell", "ticker": ticker,
                        "price": int(price), "qty": qty,
                        "pnl": int(pnl), "reason": "time_stop",
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
                    continue
                if ticker not in self._active_strategies:
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

                # 포지션 한도 재확인
                open_pos = self._risk_manager.get_open_positions()
                if len(open_pos) >= self._config.trading.max_positions:
                    logger.info(f"포지션 한도 ({self._config.trading.max_positions}), 무시: {signal.ticker}")
                    continue

                strategy = self._active_strategies[signal.ticker]["strategy"]
                sl = strategy.get_stop_loss(signal.price)
                tp1, tp2 = strategy.get_take_profit(signal.price)

                capital = self._risk_manager.available_capital
                if capital <= 0:
                    capital = self._config.trading.initial_capital
                position_capital = capital / self._config.trading.max_positions
                stop_dist = abs(signal.price - sl)
                if stop_dist > 0:
                    risk_amount = position_capital * 0.02
                    max_qty = int(risk_amount / stop_dist)
                else:
                    max_qty = int(position_capital * 0.3 / signal.price)
                total_qty = int(max_qty * self._risk_manager.position_scale)
                total_qty = max(total_qty, 1)

                result = await self._order_manager.execute_buy(
                    ticker=signal.ticker,
                    price=int(signal.price),
                    total_qty=total_qty,
                    strategy=signal.strategy,
                )
                if result:
                    self._risk_manager.register_position(
                        ticker=signal.ticker,
                        entry_price=signal.price,
                        qty=result["qty"],
                        stop_loss=sl,
                        tp1_price=tp1,
                        strategy=signal.strategy or "",
                    )
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

    # ── Screening & force close (ported from main.py) ──

    async def _refresh_token(self):
        """매일 08:00 토큰 사전 갱신."""
        try:
            token = await self._token_manager.get_token()
            logger.info(f"토큰 사전 갱신 완료: {token[:10]}...")
        except Exception as e:
            logger.error(f"토큰 갱신 실패: {e}")
            if self._notifier:
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
                await self._order_manager.execute_sell_force_close(
                    ticker=ticker, qty=pos["remaining_qty"],
                )
        await self._candle_builder.flush()
        self._candle_builder.reset()
        await self._risk_manager.save_daily_summary()
        self._risk_manager.reset_daily()
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

        if summary:
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
        """force_strategy 변경 + 전략 인스턴스 교체."""
        from strategy.momentum_strategy import MomentumStrategy
        from strategy.pullback_strategy import PullbackStrategy
        from strategy.flow_strategy import FlowStrategy
        from strategy.gap_strategy import GapStrategy
        from strategy.open_break_strategy import OpenBreakStrategy
        from strategy.big_candle_strategy import BigCandleStrategy

        # config의 force_strategy 갱신 (frozen dataclass이므로 런타임만 반영)
        if self._config:
            object.__setattr__(self._config, "force_strategy", strategy_name)

        strategies = {
            "momentum": MomentumStrategy(self._config.trading),
            "pullback": PullbackStrategy(self._config.trading),
            "flow": FlowStrategy(self._config.trading),
            "gap": GapStrategy(self._config.trading),
            "open_break": OpenBreakStrategy(self._config.trading),
            "big_candle": BigCandleStrategy(self._config.trading),
        }

        if strategy_name and strategy_name in strategies:
            self._active_strategy = strategies[strategy_name]
            # 기존 멀티 종목 전략도 교체 (prev_day_data 보존)
            for ticker, info in self._active_strategies.items():
                old_strat = info["strategy"]
                StratClass = type(strategies[strategy_name])
                new_strat = StratClass(self._config.trading)
                new_strat.configure_multi_trade(
                    max_trades=self._config.trading.max_trades_per_day,
                    cooldown_minutes=self._config.trading.cooldown_minutes,
                )
                # 전일 고가/거래량 복사 (Momentum 등에서 필요)
                if hasattr(new_strat, "set_prev_day_data"):
                    prev_high = getattr(old_strat, "_prev_day_high", 0.0)
                    prev_vol = getattr(old_strat, "_prev_day_volume", 0)
                    if prev_high > 0:
                        new_strat.set_prev_day_data(prev_high, prev_vol)
                info["strategy"] = new_strat
            logger.info(f"전략 수동 변경: {strategy_name}")
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
                    "entry_time": pos.get("entry_time"),
                    "time_stop_minutes": self._config.trading.time_stop_minutes if self._config else 60,
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
        """DB에서 당일 체결 내역 조회."""
        today = datetime.now().strftime("%Y-%m-%d")
        return await self._db.fetch_all(
            "SELECT * FROM trades WHERE traded_at LIKE ? || '%' ORDER BY traded_at DESC",
            (today,),
        )

    def _emit_pnl(self):
        """일일 손익을 시그널로 전송."""
        if not self._risk_manager:
            return
        try:
            self.signals.pnl_updated.emit(self._risk_manager._daily_pnl)
        except Exception as e:
            logger.debug(f"PnL emit 실패: {e}")

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

    async def _emit_daily_history(self):
        """최근 5일 일일 PnL을 DB에서 조회하여 전송."""
        if not self._db:
            return
        try:
            rows = await self._db.fetch_all(
                "SELECT date, total_pnl FROM daily_pnl ORDER BY date DESC LIMIT 5"
            )
            if rows:
                data = [{"date": r["date"][-5:], "pnl": r["total_pnl"]} for r in reversed(rows)]
                self.signals.daily_history_updated.emit(data)
        except Exception as e:
            logger.debug(f"일일 히스토리 emit 실패: {e}")

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
