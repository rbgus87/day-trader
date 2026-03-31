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
            self.signals.error.emit(str(e))
        finally:
            self._cleanup_sync()
            self._loop.close()
            self._loop = None
            self.signals.stopped.emit()

    # ── Core async engine ──

    async def _run_engine(self):
        """Initialize components and start pipeline (ported from main.py)."""
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
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

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

        # 3. Scheduler
        self._scheduler = AsyncIOScheduler()
        self._scheduler.add_job(self._refresh_token, "cron", hour=8, minute=0)
        self._scheduler.add_job(self._run_screening, "cron", hour=8, minute=30)
        self._scheduler.add_job(self._force_close, "cron", hour=15, minute=10)
        self._scheduler.add_job(self._run_daily_report, "cron", hour=15, minute=30)
        self._scheduler.start()

        # Late screening (장중 실행 시 즉시 스크리닝)
        now = datetime.now().time()
        if dt_time(8, 30) < now < dt_time(15, 10) and not self._active_strategies:
            logger.info("장중 실행 감지 -- 즉시 스크리닝 시작")
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

        # WS connect
        try:
            await self._ws_client.connect()
        except Exception as e:
            logger.error(f"WS 연결 실패: {e}")

        # Start pipeline
        self._running = True
        self.signals.started.emit()

        self._pipeline_tasks = [
            asyncio.create_task(self._tick_consumer()),
            asyncio.create_task(self._candle_consumer()),
            asyncio.create_task(self._signal_consumer()),
            asyncio.create_task(self._order_confirmation_consumer()),
        ]

        logger.info("파이프라인 시작 -- 매매 대기 중 (GUI)")

        # 4. Polling loop (2-second interval, 0.2s check for fast stop)
        while self._running:
            self._emit_status()
            self._emit_positions()
            self._emit_trades()
            self._emit_pnl()
            self._emit_candidates()
            # 0.2초 간격으로 _running 체크 → 정지 요청 시 최대 0.2초 내 반응
            for _ in range(10):
                if not self._running:
                    break
                await asyncio.sleep(0.2)

    # ── Pipeline consumers (ported from main.py) ──

    async def _tick_consumer(self):
        """틱 -> 캔들 빌더 + 포지션 모니터링."""
        while self._running:
            try:
                tick = await asyncio.wait_for(self._tick_queue.get(), timeout=5.0)
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

        while self._running:
            try:
                candle = await asyncio.wait_for(self._candle_queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                if not self._active_strategies:
                    continue
                if self._risk_manager.is_trading_halted():
                    continue

                ticker = candle["ticker"]
                if ticker not in self._active_strategies:
                    continue

                # 동시 포지션 한도
                open_pos = self._risk_manager.get_open_positions()
                if len(open_pos) >= self._config.trading.max_positions and ticker not in open_pos:
                    continue
                # 이미 포지션 있으면 스킵
                if self._risk_manager.get_position(ticker):
                    continue

                strat_info = self._active_strategies[ticker]
                strategy = strat_info["strategy"]

                # 5분봉이면 Flow 거래량 히스토리 업데이트
                if candle.get("tf") == "5m" and hasattr(strategy, "on_candle_5m"):
                    strategy.on_candle_5m(candle)

                self._candle_history.setdefault(ticker, [])
                self._candle_history[ticker].append(candle)
                if len(self._candle_history[ticker]) > self._MAX_HISTORY:
                    self._candle_history[ticker] = self._candle_history[ticker][-self._MAX_HISTORY:]

                df = pd.DataFrame(self._candle_history[ticker])
                signal = strategy.generate_signal(df, candle)
                if signal:
                    await self._signal_queue.put(signal)
            except Exception as e:
                logger.error(f"candle_consumer 오류: {e}")

    async def _signal_consumer(self):
        """신호 -> 주문 실행."""
        while self._running:
            try:
                signal = await asyncio.wait_for(self._signal_queue.get(), timeout=5.0)
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
                    )
                    self.signals.trade_executed.emit({
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "side": "buy",
                        "ticker": signal.ticker,
                        "price": int(signal.price),
                        "qty": result["qty"],
                        "pnl": 0, "reason": "entry",
                    })
            except Exception as e:
                logger.error(f"signal_consumer 오류: {e}")

    async def _order_confirmation_consumer(self):
        """WS 체결통보 처리."""
        while self._running:
            try:
                exec_data = await asyncio.wait_for(self._order_queue.get(), timeout=5.0)
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
        """08:30 장 전 스크리닝 -- candidates 수집 -> 필터 -> 전략 선택."""
        from strategy.momentum_strategy import MomentumStrategy
        from strategy.pullback_strategy import PullbackStrategy
        from strategy.flow_strategy import FlowStrategy
        from strategy.gap_strategy import GapStrategy
        from strategy.open_break_strategy import OpenBreakStrategy
        from strategy.big_candle_strategy import BigCandleStrategy

        today = datetime.now().strftime("%Y-%m-%d")
        logger.info(f"08:30 스크리닝 시작 ({today})")

        try:
            # 1. Candidates 수집
            candidates = await self._candidate_collector.collect()
            if not candidates:
                logger.warning("candidates 없음 -- 당일 매매 없음")
                await self._notifier.send("스크리닝 결과: candidates 없음 -- 당일 매매 없음")
                return

            # 2. 4단계 필터 적용
            screened = await self._pre_market_screener.screen(candidates)
            if not screened:
                logger.warning("스크리닝 통과 종목 없음 -- 당일 매매 없음")
                await self._notifier.send("스크리닝 결과: 통과 종목 없음 -- 당일 매매 없음")
                return

            # Cache for UI
            self._screener_results = screened

            # 3. 스크리닝 결과 DB 저장
            await self._pre_market_screener.save_results(today, screened)

            # 4. 전략 선택 (상위 1종목 + 시장 데이터 자동 수집)
            top = screened[0]
            strategy_name, ticker = await self._strategy_selector.select(
                candidate_ticker=top["ticker"],
            )

            if not strategy_name:
                await self._notifier.send("전략 선택 없음 — 당일 매매 없음")
                return

            # 5. 상위 N종목 멀티 전략 설정
            top_n = self._config.trading.screening_top_n
            selected = screened[:top_n]
            tickers = [s["ticker"] for s in selected]

            strategy_classes = {
                "momentum": MomentumStrategy,
                "pullback": PullbackStrategy,
                "flow": FlowStrategy,
                "gap": GapStrategy,
                "open_break": OpenBreakStrategy,
                "big_candle": BigCandleStrategy,
            }
            StratClass = strategy_classes.get(strategy_name)
            if not StratClass:
                logger.error(f"알 수 없는 전략: {strategy_name}")
                return

            self._active_strategies = {}
            for s in selected:
                strat = StratClass(self._config.trading)
                strat.configure_multi_trade(
                    max_trades=self._config.trading.max_trades_per_day,
                    cooldown_minutes=self._config.trading.cooldown_minutes,
                )
                self._active_strategies[s["ticker"]] = {
                    "strategy": strat,
                    "name": s.get("name", s["ticker"]),
                    "score": s.get("score", 0),
                }
            self._active_strategy = self._active_strategies.get(
                tickers[0], {}
            ).get("strategy")  # 대표 전략 (상태 표시용)

            await self._ws_client.subscribe(tickers)
            logger.info(f"멀티 종목 감시: {len(selected)}종목 전략={strategy_name}")
            await self._notifier.send(
                f"스크리닝 완료 — {strategy_name}\n"
                f"감시: {len(selected)}종목\n"
                + "\n".join(
                    f"  {s.get('name','')} ({s['ticker']}) 점수:{s.get('score',0):.1f}"
                    for s in selected
                )
            )

        except Exception as exc:
            logger.error(f"스크리닝 실패: {exc}")
            await self._notifier.send_urgent(f"스크리닝 오류: {exc}")

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
        except Exception:
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

    # ── UI -> Worker command handlers (thread-safe) ──

    def _on_request_stop(self):
        """엔진 정상 종료."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_stop(), self._loop)

    async def _async_stop(self):
        """파이프라인 중지 + running 플래그 해제."""
        logger.info("엔진 종료 요청 수신")
        self._running = False
        for t in self._pipeline_tasks:
            if not t.done():
                t.cancel()

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
            # 기존 멀티 종목 전략도 교체
            for ticker, info in self._active_strategies.items():
                StratClass = type(strategies[strategy_name])
                new_strat = StratClass(self._config.trading)
                new_strat.configure_multi_trade(
                    max_trades=self._config.trading.max_trades_per_day,
                    cooldown_minutes=self._config.trading.cooldown_minutes,
                )
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

        self.signals.status_updated.emit({
            "mode": self._mode,
            "running": self._running,
            "halted": rm._halted if rm else False,
            "strategy": strategy_name,
            "target": target_ticker,
            "target_name": target_name,
            "force_strategy": force,
            "positions_count": len(rm._positions) if rm else 0,
            "ws_connected": self._ws_client.connected if self._ws_client else False,
            "daily_pnl": daily_pnl,
            "daily_pnl_pct": daily_pnl_pct,
            "trades_count": trades_count,
            "max_trades": max_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
        })

    def _emit_positions(self):
        """포지션 목록을 시그널로 전송."""
        if not self._risk_manager:
            return
        try:
            positions = []
            for ticker, pos in self._risk_manager._positions.items():
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
                    "status": status,
                })
            self.signals.positions_updated.emit(positions)
        except Exception:
            pass

    def _emit_trades(self):
        """당일 체결 내역을 시그널로 전송."""
        if not self._db or not self._loop:
            return
        try:
            asyncio.ensure_future(self._fetch_and_emit_trades(), loop=self._loop)
        except Exception:
            pass

    async def _fetch_and_emit_trades(self):
        """DB에서 당일 체결 내역 조회 후 시그널 전송."""
        try:
            trades = await self._fetch_today_trades()
            self.signals.trades_updated.emit(trades)
        except Exception:
            pass

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
        except Exception:
            pass

    def _emit_candidates(self):
        """스크리너 후보 목록을 시그널로 전송."""
        try:
            self.signals.candidates_updated.emit(self._screener_results)
        except Exception:
            pass

    # ── Cleanup ──

    def _run_with_timeout(self, coro, timeout: float = 3.0, label: str = "") -> None:
        """run_until_complete + timeout 래퍼. hang 방지."""
        try:
            self._loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout))
        except asyncio.TimeoutError:
            logger.warning(f"Cleanup timeout ({label})")
        except Exception as e:
            logger.error(f"Cleanup error ({label}): {e}")

    def _cleanup_sync(self):
        """Synchronous cleanup in finally block."""
        if not self._loop:
            return

        # Cancel pipeline tasks
        for t in self._pipeline_tasks:
            if not t.done():
                t.cancel()
        if self._pipeline_tasks:
            self._run_with_timeout(
                asyncio.gather(*self._pipeline_tasks, return_exceptions=True),
                timeout=3.0, label="pipeline",
            )

        try:
            if self._scheduler and self._scheduler.running:
                self._scheduler.shutdown(wait=False)
        except Exception as e:
            logger.error(f"Scheduler cleanup error: {e}")

        if self._ws_client:
            self._run_with_timeout(self._ws_client.disconnect(), label="ws")

        if self._rest_client:
            self._run_with_timeout(self._rest_client.aclose(), label="rest")

        if self._notifier:
            mode_tag = "[PAPER] " if self._mode == "paper" else ""
            self._run_with_timeout(
                self._notifier.send(f"{mode_tag}시스템 종료 (GUI)"),
                timeout=2.0, label="notify",
            )
            self._run_with_timeout(self._notifier.aclose(), label="notifier_close")

        if self._db:
            self._run_with_timeout(self._db.close(), label="db")

        # Cancel remaining tasks
        try:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._run_with_timeout(
                    asyncio.gather(*pending, return_exceptions=True),
                    timeout=2.0, label="pending",
                )
        except Exception:
            pass

    @property
    def engine_running(self) -> bool:
        """엔진 실행 중 여부."""
        return self._running

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        """asyncio 이벤트 루프 (외부 thread-safe 호출용)."""
        return self._loop
