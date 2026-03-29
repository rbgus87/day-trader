"""main.py — 단타 자동매매 시스템 엔트리포인트."""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from apscheduler.schedulers.asyncio import AsyncIOScheduler
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
        "logs/{time:YYYY-MM-DD}.log",
        rotation="1 day", retention="30 days",
        level="DEBUG", encoding="utf-8",
        filter=_log_filter,
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

    # 스크리닝 컴포넌트
    candidate_collector = CandidateCollector(rest_client)
    pre_market_screener = PreMarketScreener(rest_client, db, config.screener)
    strategy_selector = StrategySelector(config, rest_client)

    # 활성 전략 (스크리닝 후 설정)
    active_strategy = None

    # 스케줄러
    scheduler = AsyncIOScheduler()

    # --- 파이프라인 태스크 ---

    async def tick_consumer():
        """틱 → 캔들 빌더 + 포지션 모니터링."""
        while True:
            tick = await tick_queue.get()
            # 1. 캔들 빌더에 전달 (기존)
            await candle_builder.on_tick(tick)
            # 2. 포지션 모니터링 (신규)
            ticker = tick["ticker"]
            price = tick["price"]
            pos = risk_manager.get_position(ticker)
            if pos is None or pos["remaining_qty"] <= 0:
                continue
            # 손절 체크
            if risk_manager.check_stop_loss(ticker, price):
                qty = pos["remaining_qty"]
                await order_manager.execute_sell_stop(ticker=ticker, qty=qty)
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
            # 트레일링 스톱 갱신
            risk_manager.update_trailing_stop(ticker, price)

    async def candle_consumer():
        """캔들 → 전략 엔진. 롤링 DataFrame 유지."""
        nonlocal active_strategy
        import pandas as pd
        candle_history: dict[str, list[dict]] = {}
        MAX_HISTORY = 100

        while True:
            candle = await candle_queue.get()
            if active_strategy is None:
                continue
            if risk_manager.is_trading_halted():
                continue

            ticker = candle["ticker"]
            candle_history.setdefault(ticker, [])
            candle_history[ticker].append(candle)
            if len(candle_history[ticker]) > MAX_HISTORY:
                candle_history[ticker] = candle_history[ticker][-MAX_HISTORY:]

            df = pd.DataFrame(candle_history[ticker])
            signal = active_strategy.generate_signal(df, candle)
            if signal:
                await signal_queue.put(signal)

    async def signal_consumer():
        """신호 → 주문 실행."""
        nonlocal active_strategy
        while True:
            signal = await signal_queue.get()
            if signal.side == "buy" and active_strategy:
                sl = active_strategy.get_stop_loss(signal.price)
                tp1, tp2 = active_strategy.get_take_profit(signal.price)

                # 포지션 사이즈 계산
                capital = risk_manager.available_capital
                if capital <= 0:
                    logger.warning("available_capital이 0 이하 — config.trading.initial_capital로 대체")
                    capital = config.trading.initial_capital
                stop_dist = abs(signal.price - sl)
                if stop_dist > 0:
                    risk_amount = capital * 0.02
                    max_qty = int(risk_amount / stop_dist)
                else:
                    max_qty = int(capital * 0.3 / signal.price)
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
                    )

    async def order_confirmation_consumer():
        """WS 체결통보 처리."""
        while True:
            exec_data = await order_queue.get()
            logger.info(f"체결통보: {exec_data}")

    # --- 스케줄 등록 ---

    async def run_screening():
        """08:30 장 전 스크리닝 — candidates 수집 → 필터 → 전략 선택."""
        nonlocal active_strategy
        from datetime import datetime
        from strategy.orb_strategy import OrbStrategy
        from strategy.vwap_strategy import VwapStrategy
        from strategy.momentum_strategy import MomentumStrategy
        from strategy.pullback_strategy import PullbackStrategy

        today = datetime.now().strftime("%Y-%m-%d")
        logger.info(f"08:30 스크리닝 시작 ({today})")

        try:
            # 1. Candidates 수집
            candidates = await candidate_collector.collect()
            if not candidates:
                logger.warning("candidates 없음 — 당일 매매 없음")
                await notifier.send("스크리닝 결과: candidates 없음 — 당일 매매 없음")
                return

            # 2. 4단계 필터 적용
            screened = await pre_market_screener.screen(candidates)
            if not screened:
                logger.warning("스크리닝 통과 종목 없음 — 당일 매매 없음")
                await notifier.send("스크리닝 결과: 통과 종목 없음 — 당일 매매 없음")
                return

            # 3. 스크리닝 결과 DB 저장
            await pre_market_screener.save_results(today, screened)

            # 4. 전략 선택 (상위 1종목 + 시장 데이터 자동 수집)
            top = screened[0]
            strategy_name, ticker = await strategy_selector.select(
                candidate_ticker=top["ticker"],
            )

            # 5. 전략 인스턴스 설정
            strategies = {
                "orb": OrbStrategy(config.trading, min_range_pct=config.trading.orb_min_range_pct),
                "vwap": VwapStrategy(config.trading),
                "momentum": MomentumStrategy(config.trading),
                "pullback": PullbackStrategy(config.trading),
            }
            active_strategy = strategies.get(strategy_name)

            if active_strategy and ticker:
                # WS 구독 등록
                await ws_client.subscribe([ticker])
                logger.info(f"전략 활성화: {strategy_name} → {ticker} ({top['name']})")
                await notifier.send(
                    f"스크리닝 완료\n"
                    f"선정: {top['name']} ({ticker})\n"
                    f"전략: {strategy_name}\n"
                    f"점수: {top.get('score', 0):.1f}\n"
                    f"후보: {len(screened)}종목"
                )
            else:
                logger.info("전략 선택 없음 — 당일 매매 없음")
                await notifier.send("스크리닝 완료 — 조건 미달, 당일 매매 없음")

        except Exception as exc:
            logger.error(f"스크리닝 실패: {exc}")
            await notifier.send_urgent(f"스크리닝 오류: {exc}")

    async def force_close():
        """15:10 강제 청산."""
        nonlocal active_strategy
        logger.warning("15:10 강제 청산 시작")
        for ticker, pos in risk_manager.get_open_positions().items():
                await order_manager.execute_sell_force_close(
                    ticker=ticker, qty=pos["remaining_qty"],
                )
        await candle_builder.flush()
        candle_builder.reset()
        # 일일 실적 저장 (reset 전에 수행)
        await risk_manager.save_daily_summary()
        risk_manager.reset_daily()
        active_strategy = None  # 청산 후 전략 비활성화

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

    scheduler.add_job(run_screening, "cron", hour=8, minute=30)
    scheduler.add_job(force_close, "cron", hour=15, minute=10)
    scheduler.add_job(run_daily_report, "cron", hour=15, minute=30)
    scheduler.add_job(backup_db, "cron", hour=15, minute=35)
    scheduler.start()

    # 08:30 이후 실행 시 즉시 스크리닝 (이미 지나간 스케줄 보상)
    from datetime import datetime, time as dt_time
    now = datetime.now().time()
    if dt_time(8, 30) < now < dt_time(15, 10) and active_strategy is None:
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

    # WS 연결
    try:
        await ws_client.connect()
    except Exception as e:
        logger.error(f"WS 연결 실패: {e}")
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
        logger.info("시스템 종료 완료")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("시스템 종료")
