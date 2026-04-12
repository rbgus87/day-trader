"""main.py — 단타 자동매매 시스템 엔트리포인트."""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

from config.settings import AppConfig
from core.auth import TokenManager
from core.kiwoom_rest import KiwoomRestClient
from core.kiwoom_ws import KiwoomWebSocketClient
from core.order_manager import OrderManager
from core.rate_limiter import AsyncRateLimiter
from data.candle_builder import CandleBuilder
from data.db_manager import DbManager
from notification.telegram_bot import TelegramNotifier
from core.paper_order_manager import PaperOrderManager
from risk.risk_manager import RiskManager
from screener.candidate_collector import CandidateCollector
from screener.pre_market import PreMarketScreener
from screener.strategy_selector import StrategySelector


def _mask_secrets(message: str) -> str:
    """로그 메시지에서 민감 정보 마스킹 (PRD 4.3)."""
    import re
    import os
    secrets = [
        os.getenv("KIWOOM_APP_KEY", ""),
        os.getenv("KIWOOM_SECRET_KEY", ""),
        os.getenv("TELEGRAM_BOT_TOKEN", ""),
    ]
    for secret in secrets:
        if secret and len(secret) > 4:
            message = message.replace(secret, secret[:4] + "****")
    # Bearer 토큰 마스킹
    message = re.sub(r"(Bearer\s+)\S{8,}", r"\1****", message)
    return message


def _log_filter(record):
    """loguru 필터: 민감 정보 마스킹."""
    record["message"] = _mask_secrets(record["message"])
    return True


_TRADE_KEYWORDS = re.compile(
    r"매수|매도|체결|주문|청산|손절|TP1|트레일링|신호|포지션|손익|PnL|"
    r"승률|TRADE-LIMIT|일일 실적|일일 손실|PAPER",
    re.IGNORECASE,
)


def _trade_log_filter(record):
    """매매 관련 로그만 trade.log에 기록."""
    record["message"] = _mask_secrets(record["message"])
    return bool(_TRADE_KEYWORDS.search(record["message"]))


async def main():
    config = AppConfig.from_yaml()

    # 로깅 설정 (마스킹 필터 포함)
    logger.remove()
    logger.add(
        sys.stderr, level=config.log_level,
        format="{time:HH:mm:ss} | {level:<7} | {message}",
        filter=_log_filter,
    )
    logger.add(
        "logs/day.log",
        rotation="10 MB",
        retention=5,
        level="DEBUG",
        encoding="utf-8",
        filter=_log_filter,
        compression="zip",
    )
    logger.add(
        "logs/trade.log",
        rotation="5 MB",
        retention=10,
        level="INFO",
        encoding="utf-8",
        filter=_trade_log_filter,
        compression="zip",
    )

    # 모드 표시
    mode_label = "PAPER" if config.paper_mode else "LIVE"
    logger.info(f"모드: {mode_label}")
    logger.info(f"API URL: {config.kiwoom.rest_base_url}")

    # 인프라 초기화
    db = DbManager(config.db_path)
    await db.init()

    notifier = TelegramNotifier(config.telegram)
    mode_tag = "[PAPER] " if config.paper_mode else ""
    await notifier.send(f"{mode_tag}단타 매매 시스템 시작")

    token_manager = TokenManager(
        app_key=config.kiwoom.app_key,
        secret_key=config.kiwoom.secret_key,
        base_url=config.kiwoom.rest_base_url,
    )
    rate_limiter = AsyncRateLimiter(
        max_calls=config.kiwoom.rate_limit_calls,
        period=config.kiwoom.rate_limit_period,
    )
    rest_client = KiwoomRestClient(
        config=config.kiwoom,
        token_manager=token_manager,
        rate_limiter=rate_limiter,
    )

    # Queues (크기 제한으로 backpressure 적용)
    tick_queue = asyncio.Queue(maxsize=10000)
    candle_queue = asyncio.Queue(maxsize=1000)
    signal_queue = asyncio.Queue(maxsize=100)
    order_queue = asyncio.Queue(maxsize=100)

    # 컴포넌트
    ws_client = KiwoomWebSocketClient(
        ws_url=config.kiwoom.ws_url,
        token_manager=token_manager,
        tick_queue=tick_queue,
        order_queue=order_queue,
        notifier=notifier,
    )
    candle_builder = CandleBuilder(candle_queue=candle_queue, timeframes=["1m", "5m"])
    risk_manager = RiskManager(
        trading_config=config.trading, db=db, notifier=notifier,
    )
    risk_manager.set_daily_capital(config.trading.initial_capital)

    if config.paper_mode:
        order_manager = PaperOrderManager(
            risk_manager=risk_manager,
            notifier=notifier, db=db, trading_config=config.trading,
            order_queue=order_queue,
        )
        logger.info("주문 관리자: PaperOrderManager (시뮬레이션)")
    else:
        order_manager = OrderManager(
            rest_client=rest_client, risk_manager=risk_manager,
            notifier=notifier, db=db, trading_config=config.trading,
            order_queue=order_queue,
        )
        logger.info("주문 관리자: OrderManager (실매매)")

    # WS에 리스크/주문 관리자 연결 (긴급 청산용)
    ws_client._risk_manager = risk_manager
    ws_client._order_manager = order_manager

    # 스크리닝 컴포넌트
    candidate_collector = CandidateCollector(rest_client)
    pre_market_screener = PreMarketScreener(rest_client, db, config.screener)
    strategy_selector = StrategySelector(config, rest_client)

    # 멀티 종목 활성 전략 (스크리닝 후 설정)
    active_strategies: dict = {}  # {ticker: {"strategy": ..., "name": ..., "score": ...}}

    # 스케줄러 (BackgroundScheduler — 이벤트 루프와 독립 실행)
    scheduler = BackgroundScheduler()

    # --- 파이프라인 태스크 ---

    async def tick_consumer():
        """틱 → 캔들 빌더 + 포지션 모니터링 (멀티 종목)."""
        while True:
            tick = await tick_queue.get()
            await candle_builder.on_tick(tick)
            ticker = tick["ticker"]
            price = tick["price"]
            pos = risk_manager.get_position(ticker)
            if pos is None or pos["remaining_qty"] <= 0:
                continue
            # 손절 체크
            if risk_manager.check_stop_loss(ticker, price):
                qty = pos["remaining_qty"]
                await order_manager.execute_sell_stop(ticker=ticker, qty=qty, price=int(price))
                pnl = (price - pos["entry_price"]) * qty
                risk_manager.record_pnl(pnl)
                risk_manager.remove_position(ticker)
                logger.info(f"손절 실행: {ticker} {qty}주 @ {price:,} PnL={pnl:+,.0f}")
                continue
            # TP1 체크
            if risk_manager.check_tp1(ticker, price):
                sell_qty = int(pos["remaining_qty"] * config.trading.tp1_sell_ratio)
                await order_manager.execute_sell_tp1(ticker=ticker, price=int(price), remaining_qty=pos["remaining_qty"])
                pnl = (price - pos["entry_price"]) * sell_qty
                risk_manager.record_pnl(pnl)
                risk_manager.mark_tp1_hit(ticker, sell_qty)
                logger.info(f"TP1 실행: {ticker} {sell_qty}주 @ {price:,} PnL={pnl:+,.0f}")
                continue
            # 시간 손절
            if risk_manager.check_time_stop(
                ticker, price,
                config.trading.time_stop_minutes,
                config.trading.time_stop_min_profit,
            ):
                qty = pos["remaining_qty"]
                await order_manager.execute_sell_force_close(ticker=ticker, qty=qty, price=int(price))
                pnl = (price - pos["entry_price"]) * qty
                risk_manager.record_pnl(pnl)
                risk_manager.remove_position(ticker)
                logger.info(f"시간 손절: {ticker} {qty}주 @ {price:,} PnL={pnl:+,.0f} ({config.trading.time_stop_minutes}분)")
                continue
            # 트레일링 스톱 갱신
            risk_manager.update_trailing_stop(ticker, price)

    async def candle_consumer():
        """캔들 → 전략 엔진 (멀티 종목)."""
        nonlocal active_strategies
        import pandas as pd
        candle_history: dict[str, list[dict]] = {}
        MAX_HISTORY = 100

        while True:
            candle = await candle_queue.get()
            if not active_strategies:
                continue
            if risk_manager.is_trading_halted():
                continue

            ticker = candle["ticker"]
            if ticker not in active_strategies:
                continue
            # Phase 2 Day 10: 블랙리스트 체크
            if risk_manager.is_ticker_blacklisted(ticker):
                continue

            # 동시 포지션 한도
            open_pos = risk_manager.get_open_positions()
            if len(open_pos) >= config.trading.max_positions and ticker not in open_pos:
                continue

            # 이미 포지션 있으면 신호 생성 스킵
            if risk_manager.get_position(ticker):
                continue

            strat_info = active_strategies[ticker]
            strategy = strat_info["strategy"]

            # 5분봉이면 Flow 거래량 히스토리 업데이트
            if candle.get("tf") == "5m" and hasattr(strategy, "on_candle_5m"):
                strategy.on_candle_5m(candle)

            candle_history.setdefault(ticker, [])
            candle_history[ticker].append(candle)
            if len(candle_history[ticker]) > MAX_HISTORY:
                candle_history[ticker] = candle_history[ticker][-MAX_HISTORY:]

            candle["price"] = candle.get("close", 0)
            df = pd.DataFrame(candle_history[ticker])
            signal = strategy.generate_signal(df, candle)
            if signal:
                await signal_queue.put(signal)

    async def signal_consumer():
        """신호 → 주문 실행 (멀티 종목, 자본 분배)."""
        nonlocal active_strategies
        while True:
            signal = await signal_queue.get()
            if signal.side != "buy" or signal.ticker not in active_strategies:
                continue

            # 포지션 한도 재확인
            open_pos = risk_manager.get_open_positions()
            if len(open_pos) >= config.trading.max_positions:
                logger.info(f"포지션 한도 ({config.trading.max_positions}), 무시: {signal.ticker}")
                continue

            strategy = active_strategies[signal.ticker]["strategy"]
            sl = strategy.get_stop_loss(signal.price)
            tp1, tp2 = strategy.get_take_profit(signal.price)

            # 자본 분배
            capital = risk_manager.available_capital
            if capital <= 0:
                capital = config.trading.initial_capital
            position_capital = capital / config.trading.max_positions

            stop_dist = abs(signal.price - sl)
            if stop_dist > 0:
                risk_amount = position_capital * 0.02
                max_qty = int(risk_amount / stop_dist)
            else:
                max_qty = int(position_capital * 0.3 / signal.price)
            total_qty = int(max_qty * risk_manager.position_scale)
            total_qty = max(total_qty, 1)

            result = await order_manager.execute_buy(
                ticker=signal.ticker,
                price=int(signal.price),
                total_qty=total_qty,
                strategy=signal.strategy,
            )
            if result:
                risk_manager.register_position(
                    ticker=signal.ticker,
                    entry_price=signal.price,
                    qty=result["qty"],
                    stop_loss=sl,
                    tp1_price=tp1,
                    strategy=signal.strategy or "",
                )

    async def order_confirmation_consumer():
        """WS 체결통보 처리."""
        while True:
            exec_data = await order_queue.get()
            logger.info(f"체결통보: {exec_data}")

    # --- 스케줄 등록 ---

    async def run_screening():
        """08:30 장 전 스크리닝 — score 업데이트 + 텔레그램 알림 (전략 등록은 초기화에서 완료)."""
        nonlocal active_strategies
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        logger.info(f"스크리닝 시작 ({today})")

        try:
            candidates = await candidate_collector.collect()
            if not candidates:
                logger.warning("candidates 없음")
                await notifier.send("스크리닝: candidates 없음")
                return

            screened = await pre_market_screener.screen(candidates)
            if not screened:
                logger.warning("스크리닝 통과 종목 없음")
                await notifier.send("스크리닝: 통과 종목 없음")
                return

            await pre_market_screener.save_results(today, screened)

            # score 업데이트 (active_strategies는 유지)
            for s in screened:
                ticker = s["ticker"]
                if ticker in active_strategies:
                    active_strategies[ticker]["score"] = s.get("score", 0)

            _force = getattr(config, 'force_strategy', '') or 'auto'
            top_n = config.trading.screening_top_n
            selected = screened[:top_n]
            logger.info(f"스크리닝 완료: {len(screened)}종목 통과, 감시: {len(active_strategies)}종목 유지")
            await notifier.send(
                f"스크리닝 완료 — {_force}\n"
                f"필터 통과: {len(screened)}종목\n"
                f"전체 감시: {len(active_strategies)}종목\n"
                f"상위:\n"
                + "\n".join(
                    f"  {s.get('name','')} ({s['ticker']}) 점수:{s.get('score',0):.1f}"
                    for s in selected
                )
            )

        except Exception as exc:
            logger.error(f"스크리닝 실패: {exc}")
            await notifier.send_urgent(f"스크리닝 오류: {exc}")

    async def force_close():
        """15:10 강제 청산."""
        nonlocal active_strategies
        logger.warning("15:10 강제 청산 시작")
        for ticker, pos in list(risk_manager.get_open_positions().items()):
            if pos.get("remaining_qty", 0) > 0:
                await order_manager.execute_sell_force_close(
                    ticker=ticker, qty=pos["remaining_qty"],
                    price=int(pos.get("entry_price", 0)),
                )
        await candle_builder.flush()
        candle_builder.reset()
        await risk_manager.save_daily_summary()
        risk_manager.reset_daily()
        active_strategies = {}

    async def run_daily_report():
        """15:30 일일 보고서 텔레그램 발송."""
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        logger.info("15:30 일일 보고서 생성 시작")

        try:
            summary = await db.fetch_one(
                "SELECT * FROM daily_pnl WHERE date = ?", (today,),
            )
        except Exception:
            summary = None

        # DB에 없으면 직접 집계 시도
        if summary is None:
            summary = await risk_manager.save_daily_summary()

        if summary:
            await notifier.send_daily_report(
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
            await notifier.send_no_trade("당일 매매 기록 없음")
            logger.info("당일 매매 없음 — 무거래 알림 발송")

    async def refresh_token():
        """매일 08:00 토큰 사전 갱신."""
        try:
            token = await token_manager.get_token()
            logger.info(f"토큰 사전 갱신 완료: {token[:10]}...")
        except Exception as e:
            logger.error(f"토큰 갱신 실패: {e}")
            await notifier.send_urgent(f"토큰 갱신 실패: {e}")

    async def backup_db():
        """15:35 DB 백업 (7일 보관)."""
        import shutil
        from datetime import datetime
        from pathlib import Path

        backup_dir = Path("backups")
        backup_dir.mkdir(exist_ok=True)

        db_path = Path(config.db_path)
        if not db_path.exists():
            return

        backup_name = f"daytrader_backup_{datetime.now():%Y%m%d}.db"
        shutil.copy2(db_path, backup_dir / backup_name)
        logger.info(f"DB 백업 완료: {backup_name}")

        # 7일 이상 된 백업 삭제
        cutoff = datetime.now() - timedelta(days=7)
        for f in backup_dir.glob("daytrader_backup_*.db"):
            try:
                date_str = f.stem.split("_")[-1]
                file_date = datetime.strptime(date_str, "%Y%m%d")
                if file_date < cutoff:
                    f.unlink()
                    logger.info(f"오래된 백업 삭제: {f.name}")
            except (ValueError, OSError):
                continue

    async def _safe(coro_func, name: str):
        """스케줄러 job 예외 안전 래퍼."""
        try:
            await coro_func()
        except Exception as e:
            logger.error(f"[SCHED] {name} 실패: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _schedule_async(coro_func, name):
        """BackgroundScheduler에서 async 함수를 안전하게 호출하는 래퍼."""
        def wrapper():
            loop = asyncio.get_event_loop()
            if loop.is_running():
                future = asyncio.run_coroutine_threadsafe(coro_func(), loop)
                try:
                    future.result(timeout=60)
                except TimeoutError:
                    logger.error(f"[SCHED] {name} 타임아웃 (60초) — 이벤트 루프 응답 없음")
                except Exception as e:
                    logger.error(f"[SCHED] {name} 실행 오류: {type(e).__name__}: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
            else:
                logger.warning(f"[SCHED] {name} 스킵 — 이벤트 루프 미실행")
        return wrapper

    scheduler.add_job(
        _schedule_async(lambda: _safe(refresh_token, "토큰 갱신"), "token_refresh"),
        "cron", hour=8, minute=0, misfire_grace_time=300,
    )
    scheduler.add_job(
        _schedule_async(lambda: _safe(run_screening, "스크리닝"), "screening"),
        "cron", hour=8, minute=30, misfire_grace_time=300,
    )
    scheduler.add_job(
        _schedule_async(lambda: _safe(force_close, "강제 청산"), "force_close"),
        "cron", hour=15, minute=10, misfire_grace_time=60,
    )
    scheduler.add_job(
        _schedule_async(lambda: _safe(run_daily_report, "일일 보고서"), "daily_report"),
        "cron", hour=15, minute=30, misfire_grace_time=300,
    )
    scheduler.add_job(
        _schedule_async(lambda: _safe(backup_db, "DB 백업"), "backup"),
        "cron", hour=15, minute=35, misfire_grace_time=300,
    )
    scheduler.start()

    # 08:30 이후 실행 시 즉시 스크리닝 (이미 지나간 스케줄 보상)
    from datetime import datetime, time as dt_time
    now = datetime.now().time()
    if dt_time(8, 30) < now < dt_time(15, 10):
        logger.info("장중 실행 감지 — 즉시 스크리닝 시작")
        await run_screening()

    # 장애 복구
    try:
        api_balance = await rest_client.get_account_balance()
        holdings = [
            {"ticker": h["pdno"], "qty": int(h["hldg_qty"])}
            for h in api_balance.get("output1", [])
            if int(h.get("hldg_qty", 0)) > 0
        ]
        mismatches = await risk_manager.reconcile_positions(holdings)
        if mismatches:
            await notifier.send_urgent(
                f"포지션 불일치 감지!\n" + "\n".join(mismatches)
            )
    except Exception as e:
        logger.error(f"장애 복구 점검 실패: {e}")

    await risk_manager.check_consecutive_losses()

    # WS 연결 + 유니버스 전체 구독 + 전략 등록
    try:
        await ws_client.connect()
        import yaml as _yaml
        from pathlib import Path as _Path
        from strategy.momentum_strategy import MomentumStrategy
        from strategy.pullback_strategy import PullbackStrategy
        from strategy.flow_strategy import FlowStrategy
        from strategy.gap_strategy import GapStrategy
        from strategy.open_break_strategy import OpenBreakStrategy
        from strategy.big_candle_strategy import BigCandleStrategy

        _uni_path = _Path("config/universe.yaml")
        _all_stocks = []
        if _uni_path.exists():
            _uni = _yaml.safe_load(open(_uni_path, encoding="utf-8")) or {}
            _all_stocks = _uni.get("stocks", [])
            _all_tickers = [s["ticker"] for s in _all_stocks]
            if _all_tickers:
                await ws_client.subscribe(_all_tickers)
                logger.info(f"유니버스 전체 WS 구독: {len(_all_tickers)}종목")

        # 유니버스 전체에 전략 인스턴스 생성
        _force = getattr(config, 'force_strategy', '') or 'momentum'
        _strategy_classes = {
            "momentum": MomentumStrategy,
            "pullback": PullbackStrategy,
            "flow": FlowStrategy,
            "gap": GapStrategy,
            "open_break": OpenBreakStrategy,
            "big_candle": BigCandleStrategy,
        }
        _StratClass = _strategy_classes.get(_force, MomentumStrategy)

        active_strategies = {}
        for s in _all_stocks:
            _ticker = s["ticker"]
            _strat = _StratClass(config.trading)
            _strat.configure_multi_trade(
                max_trades=config.trading.max_trades_per_day,
                cooldown_minutes=config.trading.cooldown_minutes,
            )
            # Phase 2 Day 6: ATR 손절용 ticker 주입
            if hasattr(_strat, "set_ticker"):
                _strat.set_ticker(_ticker)
            active_strategies[_ticker] = {
                "strategy": _strat,
                "name": s.get("name", _ticker),
                "score": 0,
            }
        logger.info(f"유니버스 전체 전략 등록: {len(active_strategies)}종목 ({_force})")

        # 전일 고가/거래량 초기화 (모멘텀 전략 등에 필요)
        logger.info("전일 고가 초기화 시작...")
        init_count = 0
        for s in _all_stocks:
            _ticker = s["ticker"]
            try:
                daily = await rest_client.get_daily_ohlcv(_ticker)
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
                    if prev_high > 0 and _ticker in active_strategies:
                        strat = active_strategies[_ticker]["strategy"]
                        if hasattr(strat, "set_prev_day_data"):
                            strat.set_prev_day_data(prev_high, prev_vol)
                            init_count += 1
            except Exception as e:
                logger.debug(f"전일 고가 조회 실패 ({_ticker}): {e}")
            await asyncio.sleep(0.1)
        logger.info(f"전일 고가 초기화 완료: {init_count}/{len(active_strategies)}종목")
    except Exception as e:
        logger.error(f"WS 연결/전략 등록 실패: {e}")
        await notifier.send_urgent(f"WS 연결 실패: {e}")

    # 파이프라인 태스크 실행
    tasks = [
        asyncio.create_task(tick_consumer()),
        asyncio.create_task(candle_consumer()),
        asyncio.create_task(signal_consumer()),
        asyncio.create_task(order_confirmation_consumer()),
    ]

    logger.info("파이프라인 시작 — 매매 대기 중 (Ctrl+C로 종료)")

    # Windows에서 Ctrl+C 감지를 위한 시그널 핸들러
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("종료 신호 수신")
        stop_event.set()

    try:
        loop = asyncio.get_running_loop()
        import signal
        loop.add_signal_handler(signal.SIGINT, _signal_handler)
    except NotImplementedError:
        # Windows SelectorEventLoop은 add_signal_handler 미지원
        # 대신 별도 감시 태스크로 처리
        pass

    async def _shutdown_watcher():
        """주기적으로 KeyboardInterrupt를 체크하는 감시 태스크."""
        while not stop_event.is_set():
            await asyncio.sleep(0.5)
        # 모든 태스크 취소
        for t in tasks:
            t.cancel()

    tasks.append(asyncio.create_task(_shutdown_watcher()))

    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        pass
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        scheduler.shutdown()
        await ws_client.disconnect()
        await rest_client.aclose()
        await db.close()
        mode_tag = "[PAPER] " if config.paper_mode else ""
        await notifier.send(f"{mode_tag}시스템 종료")
        await notifier.aclose()
        logger.info("시스템 종료 완료")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("시스템 종료")
