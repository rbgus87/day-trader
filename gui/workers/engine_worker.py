"""TradingEngine을 별도 스레드에서 asyncio로 실행하는 QThread 래퍼.

매매 파이프라인(tick/candle/signal/order consumer + APScheduler)을
QThread 내 asyncio 이벤트 루프에서 실행.
모든 cross-thread 호출은 Qt signal 또는 asyncio.run_coroutine_threadsafe로 처리.
"""

import asyncio
import sys
from collections import deque
from datetime import datetime, time as dt_time
from pathlib import Path

from PyQt6.QtCore import QThread
from loguru import logger

from gui.workers.signals import EngineSignals


# TODO: 키움 WS '00'(주문체결) 메시지 필드 코드는 미검증.
# 실 페이로드 캡처 후 확정 필요. 운영 전 raw 로그 1회 수집 필수.
_WS_FIELD_ORDER_NO = "9001"      # 주문번호 (추정)
_WS_FIELD_FILLED_PRICE = "10"    # 체결가 (추정)
_WS_FIELD_FILLED_QTY = "900"     # 체결량 (추정)


def _write_universe_yaml(
    top: list[dict],
    path: Path | str = "config/universe.yaml",
) -> None:
    """조건검색 결과 top을 universe.yaml에 atomic write.

    원본은 `.bak`로 보존하고 새 파일을 임시 경로에 쓴 뒤 원자적으로 교체한다.
    실패 시 예외를 그대로 올리며, 호출자가 try/except로 처리해야 한다.
    """
    import yaml

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    bak = p.with_suffix(p.suffix + ".bak")

    header = (
        "# ============================================================================\n"
        f"# universe.yaml — 조건검색 자동 갱신 "
        f"({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})\n"
        "# 생성: gui.workers.engine_worker._write_universe_yaml\n"
        "# ============================================================================\n\n"
    )
    body = yaml.safe_dump(
        {
            "stocks": [
                {
                    "ticker": str(s["ticker"]),
                    "name": s.get("name", ""),
                    "market": s.get("market", "unknown"),
                }
                for s in top
            ],
        },
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    tmp.write_text(header + body, encoding="utf-8")
    if p.exists():
        if bak.exists():
            bak.unlink()
        p.replace(bak)
    tmp.replace(p)


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
        self._vi_handler = None  # VI 휴리스틱 핸들러 (config 로드 후 _run_engine에서 초기화)
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
        # 조건검색 추가 종목의 market 분류용 KOSPI/KOSDAQ 종목코드 캐시 (ka10099, 1회 조회).
        # _apply_condition_search_universe가 처음 호출될 때 채워지고 이후 재사용.
        self._market_codes_cache: dict[str, set[str]] | None = None
        # 조건검색 추가 종목의 ATR%(소수, 예: 3.4) — universe.yaml에 없어 ticker_atr
        # 테이블에 미등록인 종목용. enrichment 시 일봉 응답으로 계산 후 watchlist에 동봉.
        self._ticker_atr_pct: dict[str, float] = {}
        # 상한가 맵 (전일 종가 × 1.30, 호가 절사) — OHLCV 갱신 시 재계산
        self._limit_up_map: dict[str, float] = {}

        # Queues
        self._tick_queue = None
        self._candle_queue = None
        self._signal_queue = None
        self._order_queue = None

        # 강제 청산 재진입 가드 (스케줄 + 수동 시그널 동시 트리거 시 중복 실행 방지)
        self._force_close_in_progress = False

        # Candle history for strategy
        # 50봉 전일 시드 + 장중 ~390봉(09:00~15:30 1분봉) + 여유 60 = 500
        # deque(maxlen=N) — append만으로 자동 truncate, list 슬라이스 비용 제거.
        self._candle_history: dict[str, deque] = {}
        self._MAX_HISTORY = 500
        # 실시간 ATR% 캐시 — candle_history 길이가 변하지 않으면 직전 값 재사용.
        # tick마다 wilder_atr 재계산 비용 회피.
        self._atr_pct_cache: dict[str, tuple[int, float | None]] = {}
        # 시작 시퀀스 캐시 — startup에서 _fetch_condition_search_top이 채우면
        # _apply_condition_search_universe가 그대로 사용해 조건검색 중복 호출 방지.
        # 1회 사용 후 None으로 초기화. 일반 cron 경로에서는 항상 None.
        self._pending_cond_top: list[dict] | None = None
        # _fetch_condition_search_top이 320종목 일봉을 조회한 응답을 _refresh_prev_day_ohlcv가
        # 재사용하도록 캐시. startup 1회용 — _refresh_prev_day_ohlcv 끝에서 clear.
        self._daily_ohlcv_cache: dict[str, list] = {}
        # 분봉 pre-load 시드 봉 수 (장 초반 ADX 즉시 활성화 — adx_length+20=34 충족)
        self._INTRADAY_SEED_BARS = 50
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

        self._order_tracker = None  # _run_engine에서 인스턴스화
        self._timeout_counters: dict[str, int] = {}    # ticker → 연속 TIMEOUT 카운터
        self._limit_up_exit_pending: set[str] = set()  # limit_up_exit submit된 ticker

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
        from core.vi_handler import VIHandler, VIState
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

        # VI 휴리스틱 핸들러 (spec §5.5)
        self._vi_handler = VIHandler(
            static_pct=self._config.trading.vi_static_pct,
            assumed_duration_sec=self._config.trading.vi_assumed_duration_sec,
            suspected_duration_sec=self._config.trading.vi_suspected_duration_sec,
        )

        from core.order_tracker import OrderTracker
        self._order_tracker = OrderTracker(
            timeout_seconds=self._config.trading.order_confirmation_timeout_sec,
        )

        # 2. Infrastructure
        self._db = DbManager(self._config.db_path)
        await self._db.init()

        self._notifier = TelegramNotifier(self._config.telegram)
        mode_tag = "[PAPER] " if paper_mode else ""
        if self._config.notifications.system_start:
            self._notifier.send(f"{mode_tag}단타 매매 시스템 시작 (GUI)")

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
        # WS 재연결 시 universe.yaml 코어가 아닌 현재 감시 목록 전체로 복원.
        # condition_search가 추가한 종목까지 포함되도록 _active_strategies 키를 사용.
        self._ws_client.set_subscription_provider(
            lambda: list(self._active_strategies.keys())
        )
        # 5분봉은 사용처가 없고(ADR-010 이후 on_candle_5m 미구현), candle_history에
        # 혼입되면 ADX 등 1m 기반 지표가 백테스트와 다르게 계산됨 → 1m only.
        self._candle_builder = CandleBuilder(
            candle_queue=self._candle_queue, timeframes=["1m"],
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
            id="force_close", replace_existing=True,
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
        # 시장 필터 장중 재갱신 — 09:05 (시초가 직후 교정), 10:00 (중간 체크)
        self._scheduler.add_job(
            _schedule_async(self._safe_market_filter_refresh, "market_filter_refresh"),
            "cron", day_of_week="mon-fri", hour=9, minute=5, misfire_grace_time=300,
        )
        self._scheduler.add_job(
            _schedule_async(self._safe_market_filter_refresh, "market_filter_refresh_mid"),
            "cron", day_of_week="mon-fri", hour=10, minute=0, misfire_grace_time=300,
            id="market_filter_refresh_mid", replace_existing=True,
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
                    self._notifier.send(
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
                self._notifier.send_urgent(
                    "포지션 불일치 감지!\n" + "\n".join(mismatches)
                )
        except Exception as e:
            logger.error(f"장애 복구 점검 실패: {e}")

        await self._risk_manager.check_consecutive_losses()

        # 시작 시퀀스: 종목 확정 → 전략 등록 → WS 구독 (순서 통일로 strategies/WS 불일치 방지)
        try:
            core_stocks = self._load_universe()

            # 1. 조건검색으로 최종 감시 종목 확정 (실패 시 코어 fallback).
            # startup에서 한 번 호출한 결과는 _pending_cond_top에 캐시 — 직후 _run_screening의
            # _apply_condition_search_universe가 같은 결과를 재사용해 중복 REST 호출 방지.
            final_stocks = core_stocks
            source = "core"
            if self._config.condition_search.enabled:
                try:
                    cond_top = await self._fetch_condition_search_top()
                    if cond_top:
                        final_stocks = cond_top
                        source = "condition_search"
                        self._pending_cond_top = cond_top
                except Exception as e:
                    logger.error(f"[COND] 시작 시 조건검색 실패: {e} — 코어 유니버스 사용")
            logger.info(
                f"시작 시 감시 종목 확정: {len(final_stocks)}종목 (source={source})"
            )

            # 2. 확정된 리스트로 전략 등록
            self._register_active_strategies(final_stocks)

            # 3. WS connect + 동일 리스트로 구독 (strategies와 WS 1:1 일치)
            await self._ws_client.connect()
            final_tickers = [s["ticker"] for s in final_stocks]
            if final_tickers:
                await self._ws_client.subscribe(final_tickers)
                logger.info(
                    f"WS 구독: {len(final_tickers)}종목 (source={source})"
                )

                n_unknown = sum(
                    1 for s in final_stocks if s.get("market") == "unknown"
                )
                if n_unknown:
                    logger.warning(
                        f"⚠ market 미상 종목 {n_unknown}개 "
                        f"— scripts/update_universe_market.py 실행 권장"
                    )

            # 4. 전일 OHLCV — 확정 리스트 기준
            await self._refresh_prev_day_ohlcv(final_stocks)

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
                            self._notifier.send(
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
            asyncio.create_task(
                self._order_tracker_timeout_checker(),
                name="order_timeout_checker",
            ),
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
                    f"[HEARTBEAT] 스케줄러={sched_ok}, 파이프라인={alive_tasks}/5, 포지션={pos_count}"
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

    def _intraday_atr_pct(self, ticker: str, length: int = 14) -> float | None:
        """candle_history(1분봉)에서 wilder_atr 계산해 종가 대비 ATR% 반환.

        ticker_atr DB 의존을 제거하기 위한 실시간 경로. candle_history 길이가
        직전 호출과 동일하면 캐시된 값을 재사용 (tick당 재계산 비용 회피).
        None 반환 시 [ATR-CALC] DEBUG 로그로 사유 기록 (캐시 미스 시점만).
        """
        hist = self._candle_history.get(ticker)
        if hist is None:
            return None  # 미등록 종목 — 정상, 로그 스팸 방지
        if len(hist) < length + 1:
            logger.info(
                f"[ATR-CALC] {ticker} reason=short len={len(hist)} need={length + 1}"
            )
            return None
        cur_len = len(hist)
        cached = self._atr_pct_cache.get(ticker)
        if cached is not None and cached[0] == cur_len:
            return cached[1]
        atr_pct: float | None = None
        reason: str | None = None
        try:
            import pandas as pd
            from core.indicators import wilder_atr
            df = pd.DataFrame(list(hist))
            cols_needed = {"high", "low", "close"}
            missing = cols_needed - set(df.columns)
            if missing:
                reason = f"cols_missing={sorted(missing)}"
            else:
                # 시드 캔들(float) + 라이브 캔들(int) mixed dtype 방어 —
                # object dtype으로 떨어지면 wilder_atr 내부 .astype(float)이
                # 막아주지만, 명시적 to_numeric으로 NaN/오타 strings도 coerce.
                h = pd.to_numeric(df["high"], errors="coerce")
                l = pd.to_numeric(df["low"], errors="coerce")
                c = pd.to_numeric(df["close"], errors="coerce")
                nan_rows = int((h.isna() | l.isna() | c.isna()).sum())
                zero_rows = int(((h <= 0) | (l <= 0) | (c <= 0)).sum())
                atr = wilder_atr(h, l, c, length=length)
                if atr.empty:
                    reason = f"empty (nan={nan_rows}, zero={zero_rows})"
                else:
                    last_atr = atr.iloc[-1]
                    last_close = float(c.iloc[-1]) if not pd.isna(c.iloc[-1]) else 0.0
                    if pd.isna(last_atr):
                        reason = (
                            f"nan_last_atr (rows={len(df)} nan={nan_rows} "
                            f"zero={zero_rows} last_h={float(h.iloc[-1]) if not pd.isna(h.iloc[-1]) else 'NaN'} "
                            f"last_l={float(l.iloc[-1]) if not pd.isna(l.iloc[-1]) else 'NaN'} "
                            f"last_c={last_close})"
                        )
                    elif last_close <= 0:
                        reason = f"close<=0({last_close})"
                    else:
                        atr_pct = float(last_atr) / last_close
        except Exception as e:
            reason = f"exc={type(e).__name__}:{e}"
        if atr_pct is None and reason:
            # _tick_consumer에서 보유 포지션 종목에만 호출 (max 3종목).
            # 캐시는 cur_len 기준이라 새 분봉이 들어와야 재계산 → 종목당 분당 1회.
            logger.info(f"[ATR-CALC] {ticker} reason={reason} len={cur_len}")
        self._atr_pct_cache[ticker] = (cur_len, atr_pct)
        return atr_pct

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
                # VI 휴리스틱 업데이트 (prev_close 캐시 미스 시 조용히 스킵)
                _prev = self._prev_close.get(ticker)
                if _prev:
                    try:
                        self._vi_handler.update_from_tick(ticker, price, _prev)
                    except Exception as _e:
                        logger.warning(f"[VI] {ticker} update_from_tick 예외: {_e}")
                pos = self._risk_manager.get_position(ticker)
                if pos is None or pos["remaining_qty"] <= 0:
                    continue
                # 주문 진행 중이면 highest_price만 갱신, exit 스킵 (재진입 가드)
                if self._order_tracker is not None:
                    _pending = self._order_tracker.get_pending(ticker)
                    if _pending is not None:
                        if pos.get("highest_price", 0) < price:
                            pos["highest_price"] = price
                        logger.debug(
                            f"[ORDER-TRACK] {ticker} pending {_pending.side} — exit 스킵"
                        )
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
                        is_paper = self._mode == "paper"
                        if is_paper:
                            # 페이퍼: 즉시 settle (현 동작)
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
                        else:
                            # real_mode: tracker에 등록, 체결 확인 후 _handle_fill에서 settle
                            self._order_tracker.submit(
                                result["order_no"], ticker, "sell", qty,
                            )
                            self._limit_up_exit_pending.add(ticker)
                            logger.info(
                                f"[ORDER-TRACK] {result['order_no']} SUBMIT "
                                f"{ticker} sell {qty} (limit_up_exit)"
                            )
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
                    prefer_best = self._vi_handler.should_use_best_limit(ticker)
                    result = await self._order_manager.execute_sell_stop(
                        ticker=ticker, qty=qty, price=int(price),
                        strategy=strategy_name, pnl=pnl, pnl_pct=pnl_pct,
                        exit_reason=reason_code,
                        prefer_best_limit=prefer_best,
                        on_rejection=lambda tk, rt: self._vi_handler.flag_suspected(tk, f"주문 거부 (rt_cd={rt})"),
                    )
                    if result is None:
                        continue  # 주문 자체 실패 (VI 등)
                    is_paper = self._mode == "paper"
                    if is_paper:
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
                    else:
                        self._order_tracker.submit(
                            result["order_no"], ticker, "sell", qty,
                        )
                        logger.info(
                            f"[ORDER-TRACK] {result['order_no']} SUBMIT {ticker} sell {qty} "
                            f"({reason_code})"
                        )
                    continue
                # 모멘텀 둔화 청산 (수익 포지션 + 보유 min_hold_min+ + ROC ≤ threshold)
                hist = self._candle_history.get(ticker)
                if hist and self._risk_manager.check_momentum_fade(
                    ticker, price, hist, now=datetime.now(),
                ):
                    qty = pos["remaining_qty"]
                    entry = pos["entry_price"]
                    pnl = (price - entry) * qty
                    pnl_pct = ((price / entry) - 1) * 100 if entry > 0 else 0
                    strategy_name = pos.get("strategy", "") or "unknown"
                    prefer_best = self._vi_handler.should_use_best_limit(ticker)
                    result = await self._order_manager.execute_sell_stop(
                        ticker=ticker, qty=qty, price=int(price),
                        strategy=strategy_name, pnl=pnl, pnl_pct=pnl_pct,
                        exit_reason="momentum_fade",
                        prefer_best_limit=prefer_best,
                        on_rejection=lambda tk, rt: self._vi_handler.flag_suspected(tk, f"주문 거부 (rt_cd={rt})"),
                    )
                    if result is None:
                        continue
                    is_paper = self._mode == "paper"
                    if is_paper:
                        self._risk_manager.settle_sell(ticker, price, qty)
                        if pnl >= 0:
                            self._rt_wins += 1
                        else:
                            self._rt_losses += 1
                        logger.info(
                            f"momentum_fade 실행: {ticker} {qty}주 @ {price:,} "
                            f"PnL={pnl:+,.0f}"
                        )
                        strat_info = self._active_strategies.get(ticker)
                        if strat_info:
                            strat_info["strategy"].on_exit()
                        self.signals.trade_executed.emit({
                            "time": datetime.now().strftime("%H:%M:%S"),
                            "side": "sell", "ticker": ticker,
                            "price": int(price), "qty": qty,
                            "pnl": int(pnl), "reason": "momentum_fade",
                        })
                    else:
                        self._order_tracker.submit(
                            result["order_no"], ticker, "sell", qty,
                        )
                        logger.info(
                            f"[ORDER-TRACK] {result['order_no']} SUBMIT {ticker} sell {qty} "
                            f"(momentum_fade)"
                        )
                    continue
                # TP1 체크 (현재 atr_tp_enabled:false로 비활성 — dead path)
                # TODO(real_mode): TP1 재활성 시 OrderTracker 통합 필요.
                # 현재는 paper/real 모두 즉시 mark_tp1_hit 호출 — real_mode에서 미체결 시
                # 포지션 상태 분리 위험.
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
                # 트레일링 스톱 갱신 — 일봉 ATR%를 우선 사용 (백테스트와 동일 스케일).
                # 1분봉 wilder_atr은 0.1~0.5%로 너무 작아 min_pct=2% 클램프에 항상 걸렸음.
                # _ticker_atr_pct는 calculate_atr_pct(×100) 결과라 백분율(예: 5.00).
                # calculate_atr_trailing_stop은 소수점(0.05)을 기대하므로 / 100 필요.
                # _ticker_atr_pct가 비면 candle_history 폴백 (이미 소수점 단위).
                daily_pct = self._ticker_atr_pct.get(ticker)
                if daily_pct:
                    atr_pct = daily_pct / 100.0
                else:
                    atr_pct = self._intraday_atr_pct(ticker)
                self._risk_manager.update_trailing_stop(
                    ticker, price, atr_pct=atr_pct, now=datetime.now(),
                )
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
        gate_counts = {
            "tf_skip": 0,
            "no_strategy": 0,
            "halted": 0,
            "blacklist": 0,
            "loss_rest": 0,
            "max_pos": 0,
            "has_pos": 0,
        }
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
                logger.info(
                    f"[CANDLE-GATE] tf_skip={gate_counts['tf_skip']}, "
                    f"no_strategy={gate_counts['no_strategy']}, "
                    f"halted={gate_counts['halted']}, "
                    f"blacklist={gate_counts['blacklist']}, "
                    f"loss_rest={gate_counts['loss_rest']}, "
                    f"max_pos={gate_counts['max_pos']}, "
                    f"has_pos={gate_counts['has_pos']}, "
                    f"eval={signal_eval_count}"
                )
                self._emit_signal_summary(signal_eval_count)
                candle_count = 0
                signal_eval_count = 0
                for _k in gate_counts:
                    gate_counts[_k] = 0
                last_candle_log = now_ts

            try:
                ticker = candle["ticker"]

                # 1m 외 타임프레임 안전장치 — 백테스트와 동일하게 1m만 history/시그널에 사용
                if candle.get("tf", "1m") != "1m":
                    gate_counts["tf_skip"] += 1
                    continue

                # 캔들 히스토리는 모든 종목에 대해 유지 (장중 재스크리닝 대비)
                # deque(maxlen=N)이 append만으로 자동 truncate — 슬라이스 불필요.
                hist = self._candle_history.get(ticker)
                if hist is None:
                    hist = deque(maxlen=self._MAX_HISTORY)
                    self._candle_history[ticker] = hist
                hist.append(candle)

                # 전략 판단은 active_strategies에 등록된 종목만
                if not self._active_strategies:
                    gate_counts["no_strategy"] += 1
                    continue
                if self._risk_manager.is_trading_halted():
                    gate_counts["halted"] += 1
                    # Phase 3 Day 12+: 일일 손실 한도 도달 — 최초 1회 텔레그램 알림
                    if not self._daily_halt_notified and self._notifier:
                        self._daily_halt_notified = True
                        try:
                            loss = self._risk_manager._daily_pnl
                            limit = self._config.trading.daily_max_loss_pct * 100
                            self._notifier.send_urgent(
                                f"[HALT] 일일 손실 한도 도달\n"
                                f"일일 PnL: {loss:+,.0f}원\n"
                                f"한도: {limit:.1f}%\n"
                                f"오늘 추가 매수 차단"
                            )
                        except Exception as e:
                            logger.warning(f"halt 텔레그램 실패: {e}")
                    continue
                if ticker not in self._active_strategies:
                    gate_counts["no_strategy"] += 1
                    continue
                # Phase 2 Day 10: 블랙리스트 체크 (신호 평가 자체를 차단)
                if self._risk_manager.is_ticker_blacklisted(ticker):
                    gate_counts["blacklist"] += 1
                    continue
                # Phase 3 Day 11.5: 연속 손실 휴식
                if self._risk_manager.is_in_loss_rest():
                    gate_counts["loss_rest"] += 1
                    continue

                # 동시 포지션 한도
                open_pos = self._risk_manager.get_open_positions()
                if len(open_pos) >= self._config.trading.max_positions and ticker not in open_pos:
                    gate_counts["max_pos"] += 1
                    continue
                if self._risk_manager.get_position(ticker):
                    gate_counts["has_pos"] += 1
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

                # VI 활성 종목 매수 차단 (spec §5.5.3) — single get_vi_state로 lazy expiry race 회피
                from core.vi_handler import VIState
                vi_state = self._vi_handler.get_vi_state(signal.ticker)
                if vi_state != VIState.NORMAL:
                    logger.info(f"[VI] {signal.ticker} 매수 차단 — state={vi_state.value}")
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
                    is_paper = self._mode == "paper"
                    initial_status = "confirmed" if is_paper else "pending"
                    self._risk_manager.register_position(
                        ticker=signal.ticker,
                        entry_price=signal.price,
                        qty=result["qty"],
                        stop_loss=sl,
                        tp1_price=tp1,
                        strategy=signal.strategy or "",
                        limit_up_price=self._limit_up_map.get(signal.ticker),
                        status=initial_status,
                    )
                    if not is_paper and self._order_tracker is not None:
                        self._order_tracker.submit(
                            order_no=result["order_no"],
                            ticker=signal.ticker,
                            side="buy",
                            qty=result["qty"],
                        )
                        logger.info(
                            f"[ORDER-TRACK] {result['order_no']} SUBMIT "
                            f"{signal.ticker} buy {result['qty']}"
                        )
                    strategy.on_entry()
                    # 진입 직후 [ATR-DBG] 1회 dump — trailing ATR 소스 검증용.
                    # daily는 _fetch_condition_search_top 캐시 (백테스트와 동일 일봉 스케일),
                    # intraday는 candle_history 1분봉 폴백 (스케일 다름).
                    try:
                        hist = self._candle_history.get(signal.ticker)
                        hist_len = len(hist) if hist is not None else 0
                        # daily는 _ticker_atr_pct 캐시(백분율, 5.00 형태).
                        # intraday는 _intraday_atr_pct(소수점, 0.0034 형태) — ×100.
                        daily = self._ticker_atr_pct.get(signal.ticker)
                        intra = self._intraday_atr_pct(signal.ticker)
                        daily_str = f"{daily:.2f}%" if daily else "None"
                        intra_str = f"{intra * 100:.4f}%" if intra is not None else "None"
                        logger.info(
                            f"[ATR-DBG] {signal.ticker} entry "
                            f"hist_len={hist_len} "
                            f"daily_atr={daily_str} intraday_atr={intra_str} "
                            f"min_clamp={self._config.trading.atr_trail_min_pct * 100:.2f}%"
                        )
                    except Exception as e:
                        logger.debug(f"[ATR-DBG] {signal.ticker} dump 실패: {e}")
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

    async def _handle_fill(self, order_no: str) -> None:
        """FILLED 상태 도달 시 risk_manager 상태 갱신 + 알림 emit.

        매수: mark_confirmed
        매도: settle_sell + trade_executed emit
        공통: _timeout_counters 리셋 + _limit_up_exit_pending 정리
        """
        if self._order_tracker is None:
            return
        order = self._order_tracker.get_by_order_no(order_no)
        if order is None:
            logger.warning(f"[ORDER-TRACK] _handle_fill {order_no} 알 수 없음")
            return
        ticker = order.ticker
        # limit_up_exit 추적 set 정리 (FILLED 시점 — 필수)
        self._limit_up_exit_pending.discard(ticker)
        # 연속 TIMEOUT 카운터 리셋
        self._timeout_counters[ticker] = 0
        if order.side == "buy":
            self._risk_manager.mark_confirmed(ticker)
            logger.info(
                f"[ORDER-TRACK] {order_no} FILLED → mark_confirmed {ticker}"
            )
        elif order.side == "sell":
            pos = self._risk_manager.get_position(ticker)
            entry = pos.get("entry_price", 0) if pos else 0
            pnl = (order.filled_price - entry) * order.filled_qty if entry > 0 else 0
            pnl_pct = ((order.filled_price / entry) - 1) if entry > 0 else 0
            self._risk_manager.settle_sell(
                ticker, order.filled_price, order.filled_qty,
            )
            if pnl >= 0:
                self._rt_wins += 1
            else:
                self._rt_losses += 1
            logger.info(
                f"[ORDER-TRACK] {order_no} FILLED → settle_sell {ticker} "
                f"@ {order.filled_price:,.0f} PnL={pnl:+,.0f}"
            )
            strat_info = self._active_strategies.get(ticker)
            if strat_info:
                strat_info["strategy"].on_exit()
            self.signals.trade_executed.emit({
                "time": datetime.now().strftime("%H:%M:%S"),
                "side": "sell", "ticker": ticker,
                "price": int(order.filled_price), "qty": order.filled_qty,
                "pnl": int(pnl), "reason": "ws_filled",
            })

    async def _order_confirmation_consumer(self):
        """WS '00' 체결통보 → OrderTracker 갱신 → FILLED 시 _handle_fill."""
        from core.order_tracker import OrderStatus
        while self._running and not self._stop_event.is_set():
            try:
                exec_data = await asyncio.wait_for(
                    self._order_queue.get(), timeout=0.5,
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            try:
                if self._order_tracker is None:
                    logger.debug(f"[ORDER-TRACK] tracker 미초기화 — skip: {exec_data}")
                    continue
                values = exec_data.get("values", {})
                order_no = str(values.get(_WS_FIELD_ORDER_NO, ""))
                filled_qty = abs(int(values.get(_WS_FIELD_FILLED_QTY, 0) or 0))
                filled_price = abs(float(values.get(_WS_FIELD_FILLED_PRICE, 0) or 0))
                if not order_no or filled_qty == 0:
                    logger.warning(
                        f"[ORDER-TRACK] 무효 체결 메시지 무시: order_no={order_no} qty={filled_qty}"
                    )
                    continue
                updated = self._order_tracker.on_fill(
                    order_no, filled_qty, filled_price,
                )
                if updated is None:
                    continue  # 알 수 없는 주문 (on_fill에서 이미 warning 로그)
                logger.info(
                    f"[ORDER-TRACK] {order_no} FILL "
                    f"{updated.filled_qty}/{updated.requested_qty} "
                    f"@ {filled_price:,.0f} (status={updated.status.value})"
                )
                if updated.status == OrderStatus.FILLED:
                    await self._handle_fill(order_no)
            except Exception as e:
                logger.error(f"[ORDER-TRACK] _order_confirmation_consumer 오류: {e}")

    async def _verify_fill_via_rest(self, order) -> dict | None:
        """REST ka10070 잔고 폴백 1회. 체결 확인 시 {qty, price} 반환.

        잔고에서 해당 ticker의 보유 수량으로 체결 여부 추론. 정밀한 매핑이
        불가능하므로 보수적: 매수→qty>=requested, 매도→qty==0.
        실 응답 구조 확정 후 정교화 필요.
        """
        try:
            raw = await self._rest_client.get_account_balance()
        except Exception as e:
            logger.error(f"[ORDER-TRACK] ka10070 폴백 실패: {e}")
            return None
        # TODO: 실 응답 구조 확정 필요. 키움 ka10070은 output 리스트 형태 추정.
        items = (raw or {}).get("output", []) or (raw or {}).get("output1", [])
        if not isinstance(items, list):
            return None
        ticker_found = False
        for item in items:
            if str(item.get("stk_cd", "")).strip() == order.ticker:
                ticker_found = True
                try:
                    qty = abs(int(item.get("hldn_qty", 0) or 0))
                    price = abs(float(item.get("avg_pric", 0) or 0))
                except (ValueError, TypeError):
                    return None
                if order.side == "buy" and qty >= order.requested_qty:
                    return {"qty": order.requested_qty, "price": price}
                if order.side == "sell" and qty == 0:
                    # 잔량 0 (희귀 — 일반적으론 ticker 자체가 잔고에서 제거됨)
                    # TODO(real_mode): fallback_price가 0.0이 될 가능성 있음
                    # (해당 ticker에 tick이 도착하지 않은 경우). settle_sell PnL 부정확.
                    # 운영 전 _latest_prices 최소값(entry_price * 0.5 등)으로 sanity check 추가 권장.
                    fallback_price = self._latest_prices.get(order.ticker, 0.0)
                    return {"qty": order.requested_qty, "price": fallback_price}
        # sell + ticker가 잔고에 없음 → 매도 완료로 간주 (일반적 경로)
        if order.side == "sell" and not ticker_found:
            fallback_price = self._latest_prices.get(order.ticker, 0.0)
            return {"qty": order.requested_qty, "price": fallback_price}
        return None

    async def _order_tracker_timeout_checker(self):
        """1초 주기 타임아웃 감지 + REST 폴백 + cancel/알림."""
        from core.order_tracker import OrderStatus
        while self._running and not self._stop_event.is_set():
            try:
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            try:
                if self._order_tracker is None:
                    continue
                timeout_sec = self._config.trading.order_confirmation_timeout_sec
                stale = self._order_tracker.get_unfilled_older_than(timeout_sec)
                for order in stale:
                    # await 가능성 있는 작업 전 재확인 (이미 다른 경로에서 처리됐을 수 있음)
                    current = self._order_tracker.get_by_order_no(order.order_no)
                    if current is None:
                        continue
                    if current.status in (
                        OrderStatus.FILLED, OrderStatus.FAILED, OrderStatus.TIMEOUT,
                    ):
                        continue  # 이미 다른 경로(WS 등)에서 종결 — skip
                    logger.warning(
                        f"[ORDER-TRACK] {order.order_no} TIMEOUT — REST 폴백"
                    )
                    confirmed = await self._verify_fill_via_rest(order)
                    if confirmed is not None:
                        self._order_tracker.on_fill(
                            order.order_no,
                            confirmed["qty"],
                            confirmed["price"],
                        )
                        updated = self._order_tracker.get_by_order_no(order.order_no)
                        if updated and updated.status == OrderStatus.FILLED:
                            await self._handle_fill(order.order_no)
                    else:
                        # 미체결 확정
                        self._order_tracker.mark_timeout(order.order_no)
                        # limit_up_exit 정리 — 자연 재시도 경로 (필수)
                        if order.ticker in self._limit_up_exit_pending:
                            self._limit_up_exit_pending.discard(order.ticker)
                            new_stop = self._risk_manager.raise_stop_to_limit_up_floor(
                                order.ticker
                            )
                            logger.warning(
                                f"[ORDER-TRACK] limit_up_exit TIMEOUT → stop 상향: "
                                f"{order.ticker} new_stop={new_stop:,.0f}"
                            )
                        # 매수 TIMEOUT: 취소 시도
                        if order.side == "buy":
                            try:
                                await self._rest_client.cancel_order(
                                    order.order_no, order.ticker, order.requested_qty,
                                )
                            except Exception as e:
                                logger.error(
                                    f"[ORDER-TRACK] cancel_order 실패 "
                                    f"{order.order_no}: {e}"
                                )
                        # 연속 TIMEOUT 카운터 + 텔레그램
                        self._timeout_counters[order.ticker] = (
                            self._timeout_counters.get(order.ticker, 0) + 1
                        )
                        if self._notifier:
                            self._notifier.send_urgent(
                                f"[ORDER-TRACK] {order.ticker} {order.side} TIMEOUT "
                                f"({order.order_no})"
                            )
                        threshold = self._config.trading.order_timeout_consecutive_threshold
                        if self._timeout_counters[order.ticker] >= threshold and self._notifier:
                            self._notifier.send_urgent(
                                f"[ORDER-TRACK][CRITICAL] {order.ticker} 연속 TIMEOUT "
                                f"{self._timeout_counters[order.ticker]}회"
                            )
            except Exception as e:
                logger.error(f"[ORDER-TRACK] timeout_checker 오류: {e}")

    # ── Screening & force close ──

    async def _refresh_token(self):
        """매일 08:00 토큰 사전 갱신."""
        try:
            token = await self._token_manager.get_token()
            logger.info(f"토큰 사전 갱신 완료: {token[:10]}...")
        except Exception as e:
            logger.error(f"토큰 갱신 실패: {e}")
            if self._notifier and self._config.notifications.token_refresh_failure:
                self._notifier.send_urgent(f"토큰 갱신 실패: {e}")

    async def _run_screening(self):
        """08:30 장 전 스크리닝 — score 업데이트 + UI 정보 제공 (전략 등록은 _run_engine에서 완료)."""
        today = datetime.now().strftime("%Y-%m-%d")
        logger.info(f"스크리닝 시작 ({today})")

        # 조건검색 결과를 코어 유니버스에 합산하여 감시 종목 갱신 (실패 시 코어 유지)
        try:
            await self._apply_condition_search_universe()
        except Exception as e:
            logger.error(f"[COND] 조건검색 통합 실패: {e} — 코어 유니버스 유지")

        try:
            # 1. Candidates 수집
            candidates = await self._candidate_collector.collect()
            if not candidates:
                logger.warning("candidates 없음")
                self._notifier.send("스크리닝: candidates 없음")
                return

            # 2. 4단계 필터 적용
            screened = await self._pre_market_screener.screen(candidates)
            if not screened:
                logger.warning("스크리닝 통과 종목 없음")
                self._notifier.send("스크리닝: 통과 종목 없음")
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
            self._notifier.send(
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
                self._notifier.send_urgent(f"스크리닝 오류: {exc}")
            except Exception:
                pass

    async def _ensure_market_codes_cache(self) -> dict[str, set[str]] | None:
        """KOSPI/KOSDAQ 전종목 코드 set을 ka10099로 1회 조회해 캐시.

        조건검색이 추가하는 종목의 market 필드를 정확히 채우기 위함.
        실패/빈 응답이면 None 반환(캐시 미저장)하여 다음 호출 때 재시도.
        """
        if self._market_codes_cache is not None:
            return self._market_codes_cache
        try:
            kospi = await self._rest_client.get_stock_list_by_market("0")
            kosdaq = await self._rest_client.get_stock_list_by_market("10")
        except Exception as e:
            logger.error(f"[MARKET-CODES] ka10099 조회 실패: {e}")
            return None

        def _codes(rows: list[dict]) -> set[str]:
            return {
                (s.get("code") or s.get("stk_cd") or s.get("shcode") or "").strip()
                for s in rows
            } - {""}

        kospi_codes = _codes(kospi)
        kosdaq_codes = _codes(kosdaq)
        if not kospi_codes or not kosdaq_codes:
            logger.warning(
                f"[MARKET-CODES] 응답 비어있음: KOSPI {len(kospi_codes)}, "
                f"KOSDAQ {len(kosdaq_codes)} — 캐시 미저장"
            )
            return None
        self._market_codes_cache = {"kospi": kospi_codes, "kosdaq": kosdaq_codes}
        logger.info(
            f"[MARKET-CODES] 캐시 구축: KOSPI {len(kospi_codes)}, "
            f"KOSDAQ {len(kosdaq_codes)}"
        )
        return self._market_codes_cache

    @staticmethod
    def _resolve_market(
        ticker: str, market_codes: dict[str, set[str]] | None
    ) -> str:
        """ticker → 'kospi' / 'kosdaq' / 'unknown'."""
        if not market_codes:
            return "unknown"
        if ticker in market_codes.get("kospi", set()):
            return "kospi"
        if ticker in market_codes.get("kosdaq", set()):
            return "kosdaq"
        return "unknown"

    async def _fetch_condition_search_top(self) -> list[dict] | None:
        """조건검색 실행 → 거래대금 정렬 → top N 종목 리스트 반환.

        Returns:
            성공: [{"ticker", "name", "market"}, ...] (max_watch_stocks 이하)
            실패/비활성/결과 없음: None
        """
        cs_cfg = self._config.condition_search
        if not cs_cfg.enabled:
            return None

        try:
            from core.condition_search import run_condition_search
            token = await self._token_manager.get_token()
            cs_results = await run_condition_search(
                ws_url=self._config.kiwoom.ws_url,
                access_token=token,
                condition_name=cs_cfg.condition_name,
            )
        except Exception as e:
            logger.error(f"[COND] 조건검색 실행 실패: {e}")
            return None

        if not cs_results:
            logger.warning("[COND] 조건검색 결과 비어있음")
            return None

        # 전일 거래대금 = 전일 종가 × 전일 거래량. ATR%도 같은 응답에서 캐시.
        from core.indicators import calculate_atr, calculate_atr_pct
        import pandas as pd

        base_dt = datetime.now().strftime("%Y%m%d")
        enriched: list[dict] = []
        for stock in cs_results:
            ticker = stock.get("code", "").strip()
            if not ticker:
                continue
            try:
                daily = await self._rest_client.get_daily_ohlcv(ticker, base_dt=base_dt)
                items = (
                    daily.get("stk_dt_pole_chart_qry")
                    or daily.get("output2")
                    or daily.get("output")
                    or []
                )
                if len(items) < 2:
                    continue
                # _refresh_prev_day_ohlcv가 직후 같은 일봉을 다시 조회하지 않도록 캐시
                self._daily_ohlcv_cache[ticker] = items
                prev = items[1]
                prev_close = abs(float(prev.get("cur_prc", prev.get("stck_clpr", 0))))
                prev_volume = abs(int(
                    prev.get("trde_qty",
                    prev.get("acml_vol",
                    prev.get("acml_vlmn", 0)))
                ))
                amount = prev_close * prev_volume

                if len(items) >= 15:
                    try:
                        rows = []
                        for it in items[:30]:
                            h = abs(float(it.get("high_pric", it.get("stck_hgpr", 0)) or 0))
                            l = abs(float(it.get("low_pric", it.get("stck_lwpr", 0)) or 0))
                            c = abs(float(it.get("cur_prc", it.get("stck_clpr", 0)) or 0))
                            if h > 0 and l > 0 and c > 0:
                                rows.append((h, l, c))
                        if len(rows) >= 15:
                            rows.reverse()
                            df = pd.DataFrame(rows, columns=["high", "low", "close"])
                            atr = calculate_atr(df, length=14)
                            atr_pct_series = calculate_atr_pct(atr, df["close"])
                            latest = atr_pct_series.dropna()
                            if len(latest) > 0:
                                self._ticker_atr_pct[ticker] = float(latest.iloc[-1])
                    except Exception as e:
                        logger.debug(f"[COND] {ticker} ATR 계산 실패: {e}")

                if amount > 0:
                    enriched.append({
                        "ticker": ticker,
                        "name": stock.get("name", ticker),
                        "_amount": amount,
                    })
            except Exception as e:
                logger.debug(f"[COND] {ticker} 일봉 조회 실패: {e}")
            # rate_limiter(5 cps)가 이미 흐름을 제어하므로 별도 sleep 불필요

        enriched.sort(key=lambda x: x["_amount"], reverse=True)
        top = enriched[: cs_cfg.max_watch_stocks]
        logger.info(
            f"[COND] 조건검색 결과: {len(cs_results)}종목, 필터 후 {len(top)}종목"
        )

        if not top:
            return None

        market_codes = await self._ensure_market_codes_cache()
        result = [
            {
                "ticker": s["ticker"],
                "name": s["name"],
                "market": self._resolve_market(s["ticker"], market_codes),
            }
            for s in top
        ]
        # 성공 시 universe.yaml 자동 갱신 — 다음 조건검색 실패 시 fallback이 최신화됨.
        try:
            _write_universe_yaml(result)
            logger.info(f"[UNIVERSE] 유니버스 갱신: {len(result)}종목 저장")
        except Exception as e:
            logger.warning(f"[UNIVERSE] 저장 실패: {e}")
        return result

    async def _apply_condition_search_universe(self) -> None:
        """08:30 cron / 장중 갱신 — 조건검색 결과로 _active_strategies + WS 구독 동기화.

        실패/비활성/결과 없음: 기존 감시 종목 유지 (no-op) → 코어 fallback.
        startup 직후 _run_screening 즉시 호출 시에는 _pending_cond_top 캐시(1회)를
        그대로 사용해 같은 조건검색을 두 번 실행하지 않는다.
        """
        if self._pending_cond_top is not None:
            top = self._pending_cond_top
            self._pending_cond_top = None
            logger.info(f"[COND] startup 캐시 재사용: {len(top)}종목")
        else:
            top = await self._fetch_condition_search_top()
        if top is None:
            logger.warning("[COND] 조건검색 결과 없음 — 기존 감시 종목 유지")
            return

        old_tickers = set(self._active_strategies.keys())
        new_tickers = {s["ticker"] for s in top}
        added = new_tickers - old_tickers
        removed = old_tickers - new_tickers
        logger.info(
            f"[COND] 감시 종목 갱신: 기존 {len(old_tickers)} → 신규 {len(new_tickers)}"
        )

        self._register_active_strategies(top)

        # WS 구독 delta 갱신 — 장외 시간 send 실패 시에도 _active_strategies는 위에서
        # 이미 갱신되었으므로 다음 WS 재연결 시 subscription_provider가 자동 복원한다.
        try:
            if removed:
                await self._ws_client.unsubscribe(list(removed))
            if added:
                await self._ws_client.subscribe(list(added))
        except Exception as e:
            logger.warning(
                f"[COND] WS 구독 갱신 실패: {e} — 다음 재연결 시 자동 복원"
            )

        # 신규 추가 종목에 대해 전일 OHLCV 갱신
        if added:
            new_stock_dicts = [s for s in top if s["ticker"] in added]
            try:
                await self._refresh_prev_day_ohlcv(new_stock_dicts)
            except Exception as e:
                logger.error(f"[COND] 신규 종목 OHLCV 갱신 실패: {e}")

    async def _force_close(self):
        """15:10 강제 청산."""
        if self._force_close_in_progress:
            logger.warning("강제 청산 이미 실행 중 — 중복 호출 무시")
            return
        self._force_close_in_progress = True
        try:
            logger.warning("15:10 강제 청산 시작")
            for ticker, pos in list(self._risk_manager.get_open_positions().items()):
                if pos.get("remaining_qty", 0) > 0:
                    close_price = int(self._latest_prices.get(ticker, pos.get("entry_price", 0)))
                    qty = pos["remaining_qty"]
                    entry = pos.get("entry_price", 0)
                    pnl = (close_price - entry) * qty if entry > 0 else 0
                    pnl_pct = ((close_price / entry) - 1) * 100 if entry > 0 else 0
                    strategy_name = pos.get("strategy", "") or "unknown"
                    prefer_best = self._vi_handler.should_use_best_limit(ticker)
                    result = await self._order_manager.execute_sell_force_close(
                        ticker=ticker, qty=qty, price=close_price,
                        strategy=strategy_name, pnl=pnl, pnl_pct=pnl_pct,
                        exit_reason="forced_close",
                        prefer_best_limit=prefer_best,
                        on_rejection=lambda tk, rt: self._vi_handler.flag_suspected(tk, f"주문 거부 (rt_cd={rt})"),
                    )
                    if result is None:
                        logger.error(f"[ORDER-TRACK] force_close 주문 실패: {ticker}")
                        continue
                    is_paper = self._mode == "paper"
                    if is_paper:
                        self._risk_manager.settle_sell(ticker, float(close_price), qty)
                        strat_info = self._active_strategies.get(ticker)
                        if strat_info:
                            strat_info["strategy"].on_exit()
                    else:
                        self._order_tracker.submit(
                            result["order_no"], ticker, "sell", qty,
                        )
                        logger.info(
                            f"[ORDER-TRACK] {result['order_no']} SUBMIT "
                            f"{ticker} sell {qty} (forced_close)"
                        )
                        # forced_close은 다음 _handle_fill에서 settle (정상 흐름)
                        # strategy.on_exit()는 _active_strategies 클리어 전에 즉시 호출
                        # (real_mode에서 _handle_fill이 호출될 때는 _active_strategies가
                        # 이미 비워져 있을 가능성)
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
        finally:
            self._force_close_in_progress = False

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
            self._notifier.send_daily_report(
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
            self._notifier.send_no_trade("당일 매매 기록 없음")
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

        # 08:30 재등록 등 동일 ticker 재생성 케이스에서 기존 인스턴스의
        # 전일 데이터(_prev_day_high/_prev_day_volume)를 새 인스턴스에 복사.
        # 미보존 시 added=∅ 경로에서 _refresh_prev_day_ohlcv가 호출되지 않아
        # _prev_day_high=0 → generate_signal early return 누적.
        prev_data: dict[str, tuple[float, int]] = {}
        for ticker, info in (self._active_strategies or {}).items():
            old = info.get("strategy") if isinstance(info, dict) else None
            high = getattr(old, "_prev_day_high", 0.0)
            vol = getattr(old, "_prev_day_volume", 0)
            if high > 0:
                prev_data[ticker] = (float(high), int(vol))

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
            if ticker in prev_data and hasattr(strat, "set_prev_day_data"):
                ph, pv = prev_data[ticker]
                strat.set_prev_day_data(ph, pv)
            self._active_strategies[ticker] = {
                "strategy": strat,
                "name": s.get("name", ticker),
                "score": 0,
            }
            # 시장/이름 매핑 동기화 — 조건검색 추가 종목까지 _ticker_markets에 반영되어
            # market_filter.is_allowed가 정확한 시장으로 판정.
            if "market" in s:
                self._ticker_markets[ticker] = s["market"]
            self._ticker_names[ticker] = s.get("name", ticker)
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
                # _fetch_condition_search_top이 방금 같은 일봉을 조회했으면 재사용 (REST 절약)
                cached = self._daily_ohlcv_cache.pop(ticker, None)
                if cached is not None:
                    items = cached
                else:
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
        # startup용 일봉 캐시는 1회 사용 후 정리 (잔여분 — 다음 호출 오염 방지)
        if self._daily_ohlcv_cache:
            self._daily_ohlcv_cache.clear()
        # 장 초반 ADX 즉시 활성화 — 직전 영업일 마지막 N개 1분봉을 candle_history에 시드
        try:
            await self._seed_intraday_candles(stocks)
        except Exception as e:
            logger.warning(f"분봉 시드 실패 — 장 초반 ADX 미작동 가능: {e}")

    async def _seed_intraday_candles(self, stocks: list[dict]) -> None:
        """직전 영업일 마지막 N개 1분봉을 _candle_history에 pre-load.

        장 시작 직후 ADX(min_candles=adx_length+20=34) 즉시 활성화 목적.
        매 호출마다 해당 종목 history를 시드로 교체한다 (idempotent —
        장중에는 호출 안 함, 장 시작 전 _refresh_prev_day_ohlcv 경로에서만 호출).
        """
        if not stocks:
            return
        n = self._INTRADAY_SEED_BARS
        seeded = 0
        for s in stocks:
            ticker = s["ticker"]
            try:
                data = await self._rest_client.get_minute_ohlcv(ticker, tic_scope=1)
                items = (
                    data.get("stk_min_pole_chart_qry")
                    or data.get("output2")
                    or []
                )
                if not items:
                    continue
                # 키움 응답: 최신 → 과거 순. 시간순(오름차순)으로 뒤집고 마지막 N개
                seed: list[dict] = []
                for item in reversed(items):
                    raw_ts = str(item.get("cntr_tm", ""))
                    if len(raw_ts) < 14:
                        continue
                    ts = (
                        f"{raw_ts[:4]}-{raw_ts[4:6]}-{raw_ts[6:8]}T"
                        f"{raw_ts[8:10]}:{raw_ts[10:12]}:{raw_ts[12:14]}"
                    )
                    try:
                        seed.append({
                            "ticker": ticker,
                            "tf": "1m",
                            "ts": ts,
                            "open": abs(float(item.get("open_pric") or 0)),
                            "high": abs(float(item.get("high_pric") or 0)),
                            "low": abs(float(item.get("low_pric") or 0)),
                            "close": abs(float(item.get("cur_prc") or 0)),
                            "volume": int(item.get("trde_qty") or 0),
                            "vwap": None,
                        })
                    except (ValueError, TypeError):
                        continue
                if not seed:
                    continue
                # 마지막 N개만 보관 + 이후 append 자동 truncate를 위해 maxlen 설정
                self._candle_history[ticker] = deque(
                    seed[-n:], maxlen=self._MAX_HISTORY
                )
                seeded += 1
            except Exception as e:
                logger.debug(f"분봉 시드 ({ticker}) 실패: {e}")
            # rate_limiter(5 cps)가 이미 흐름을 제어하므로 별도 sleep 불필요
        logger.info(f"분봉 시드 완료: {seeded}/{len(stocks)}종 — N={n}봉")

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
                    self._notifier.send(
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

        # candle_builder의 _vwap_accum/_building/_min1_buffer를 비워 익일 VWAP 오염 방지
        if self._candle_builder is not None:
            self._candle_builder.reset()
        self._candle_history.clear()

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
                self._notifier.send(
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

    def _emit_signal_summary(self, eval_count: int) -> None:
        """active_strategies의 단계별 진단 카운터를 합산해 [SIGNAL-SUMMARY] 1줄
        출력 후 모든 인스턴스 카운터를 리셋한다. 카운터가 없는 전략은 스킵.
        """
        agg: dict[str, int] = {}
        any_strategy = False
        for info in self._active_strategies.values():
            strat = info.get("strategy") if isinstance(info, dict) else None
            counters = getattr(strat, "diag_counters", None)
            if not isinstance(counters, dict):
                continue
            any_strategy = True
            for k, v in counters.items():
                agg[k] = agg.get(k, 0) + int(v)
            reset = getattr(strat, "reset_diag_counters", None)
            if callable(reset):
                reset()
        if not any_strategy:
            return
        logger.info(
            f"[SIGNAL-SUMMARY] 평가={eval_count}, "
            f"전일데이터누락={agg.get('prev_day_missing', 0)}, "
            f"BREAKOUT통과={agg.get('breakout_pass', 0)}, "
            f"BREAKOUT미달={agg.get('breakout_fail', 0)}, "
            f"VOLUME미달={agg.get('volume_fail', 0)}, "
            f"BREAKOUT_LAST미달={agg.get('breakout_last_fail', 0)}, "
            f"ADX봉부족={agg.get('adx_no_bars', 0)}, "
            f"ADX미달={agg.get('adx_fail', 0)}, "
            f"ADX통과={agg.get('adx_pass', 0)}, "
            f"RVOL탈락={agg.get('rvol_fail', 0)}, "
            f"VWAP탈락={agg.get('vwap_fail', 0)}, "
            f"신호발생={agg.get('signal_emit', 0)}"
        )

    async def _safe_market_filter_refresh(self):
        if self._market_filter is None:
            return
        try:
            await self._market_filter.refresh()
            self.signals.market_status_updated.emit(
                self._market_filter.kospi_strong,
                self._market_filter.kosdaq_strong,
            )
            if self._notifier:
                try:
                    hhmm = datetime.now().strftime("%H:%M")
                    k = "강세" if self._market_filter.kospi_strong else "약세"
                    q = "강세" if self._market_filter.kosdaq_strong else "약세"
                    self._notifier.send(
                        f"[MARKET] {hhmm} 재갱신 — 코스피 {k} / 코스닥 {q}"
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"[SCHED] 시장 필터 재갱신 실패: {e}")

    async def _refresh_index_candles(self) -> None:
        """KOSPI(001)/KOSDAQ(101) 지수 일봉을 index_candles에 갱신 (INSERT OR REPLACE).

        08:05 _safe_refresh_ohlcv에서 호출 — 시장 필터 MA5 계산이 최신 데이터를 쓰도록 보장.
        """
        import sqlite3 as _sqlite3
        db_path = self._config.db_path
        for code in ("001", "101"):
            try:
                data = await self._rest_client.get_index_daily(code)
                items = data.get("inds_dt_pole_qry") or []
                if not items:
                    logger.warning(f"[INDEX] {code} 응답 없음")
                    continue
                conn = _sqlite3.connect(db_path)
                try:
                    for c in items:
                        conn.execute(
                            "INSERT OR REPLACE INTO index_candles "
                            "(index_code, dt, open, high, low, close, volume) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                code,
                                c["dt"],
                                float(c["open_pric"]) / 100,
                                float(c["high_pric"]) / 100,
                                float(c["low_pric"]) / 100,
                                float(c["cur_prc"]) / 100,
                                int(c["trde_qty"]),
                            ),
                        )
                    conn.commit()
                finally:
                    conn.close()
                logger.info(f"[INDEX] {code} 갱신 완료: {len(items)}건")
            except Exception as exc:
                logger.error(f"[INDEX] {code} 갱신 실패: {exc}")

    async def _safe_refresh_ohlcv(self):
        try:
            await self._refresh_index_candles()
            await self._refresh_prev_day_ohlcv()
            # ADR-008: 성공 알림
            if self._notifier and self._config.notifications.ohlcv_refresh:
                try:
                    self._notifier.send(
                        f"[자동] 08:05 전일 OHLCV 갱신 완료 — {len(self._active_strategies)}종목"
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"[SCHED] OHLCV 갱신 실패: {e}")
            if self._notifier and self._config.notifications.ohlcv_refresh:
                try:
                    self._notifier.send_urgent(
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
                self._notifier.send_urgent(
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
                    self._notifier.send_urgent(
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
                self._notifier.send("\n".join(msg_lines))
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
                    self._notifier.send_urgent(
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
                self._notifier.send(
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
        "order_timeout_checker": "_order_tracker_timeout_checker",
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
                    # 조건검색 추가 종목은 universe.yaml에 없으므로 dashboard의
                    # _market_map만으로는 판별 불가 — engine의 _ticker_markets로
                    # 정확한 분류 전달 ("kospi"/"kosdaq"/"unknown").
                    "market": self._ticker_markets.get(ticker, "unknown"),
                    # 조건검색 enrichment에서 계산한 ATR%. ticker_atr 테이블에
                    # 없는 종목용. 코어 fallback 종목은 None이고 dashboard가
                    # _atr_cache(ticker_atr)로 보완.
                    "atr_pct": self._ticker_atr_pct.get(ticker),
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

        # 4. 텔레그램 (sync 호출 — _safe_run 불필요)
        if self._notifier:
            if self._config and self._config.notifications.system_stop:
                mode_tag = "[PAPER] " if self._mode == "paper" else ""
                try:
                    self._notifier.send(f"{mode_tag}시스템 종료 (GUI)", retries=1)
                except Exception as e:
                    logger.warning(f"클린업 오류 (notify): {e}")
            try:
                self._notifier.aclose()
            except Exception as e:
                logger.warning(f"클린업 오류 (notifier_close): {e}")

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
