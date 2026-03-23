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
from risk.risk_manager import RiskManager


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
    config = AppConfig()

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

    # API 서버 확인
    logger.info(f"API URL: {config.kiwoom.rest_base_url}")

    # 인프라 초기화
    db = DbManager(config.db_path)
    await db.init()

    notifier = TelegramNotifier(config.telegram)
    await notifier.send_system_start()

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
    order_manager = OrderManager(
        rest_client=rest_client, risk_manager=risk_manager,
        notifier=notifier, db=db, trading_config=config.trading,
        order_queue=order_queue,
    )

    # 활성 전략 (스크리닝 후 설정)
    active_strategy = None

    # 스케줄러
    scheduler = AsyncIOScheduler()

    # --- 파이프라인 태스크 ---

    async def tick_consumer():
        """틱 → 캔들 빌더."""
        while True:
            tick = await tick_queue.get()
            await candle_builder.on_tick(tick)

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
                capital = risk_manager._daily_capital or 10_000_000
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

    async def force_close():
        """15:10 강제 청산."""
        logger.warning("15:10 강제 청산 시작")
        for ticker, pos in list(risk_manager._positions.items()):
            if pos["remaining_qty"] > 0:
                await order_manager.execute_sell_force_close(
                    ticker=ticker, qty=pos["remaining_qty"],
                )
        await candle_builder.flush()
        candle_builder.reset()
        risk_manager.reset_daily()

    scheduler.add_job(force_close, "cron", hour=15, minute=10)
    scheduler.start()

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

    logger.info("파이프라인 시작 — 매매 대기 중")

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("태스크 취소됨")
    finally:
        for t in tasks:
            t.cancel()
        scheduler.shutdown()
        await ws_client.disconnect()
        await db.close()
        await notifier.send_system_stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("시스템 종료")
