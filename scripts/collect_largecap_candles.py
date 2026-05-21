"""scripts/collect_largecap_candles.py — KOSPI 대형주 50종목 분봉 배치 수집.

config/universe_largecap.yaml 기반으로 과거 300일치(≈2025-04-01~현재) 분봉을
키움 REST API(ka10080)로 수집해 기존 intraday_candles DB에 저장한다.

사용법:
    python -u scripts/collect_largecap_candles.py           # 전체 50종목 300일
    python -u scripts/collect_largecap_candles.py --days 60 # 최근 60일만
    python -u scripts/collect_largecap_candles.py --tickers 005930,000660

진행률: N/50종목 출력
실패 시: 3회 재시도 후 실패 목록 출력
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import yaml
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.data_collector import DataCollector
from config.settings import AppConfig
from core.auth import TokenManager
from core.kiwoom_rest import KiwoomRestClient
from core.rate_limiter import AsyncRateLimiter
from data.db_manager import DbManager

UNIVERSE_PATH = Path(__file__).parent.parent / "config" / "universe_largecap.yaml"
DEFAULT_DAYS = 300   # ≈2025-04-01 ~ 2026-05-21 (280 영업일 + 여유)
MAX_RETRY = 3


def _load_universe() -> list[dict]:
    data = yaml.safe_load(UNIVERSE_PATH.read_text(encoding="utf-8")) or {}
    return data.get("stocks", [])


async def _collect_with_retry(
    collector: DataCollector,
    ticker: str,
    days: int,
) -> int:
    for attempt in range(1, MAX_RETRY + 1):
        try:
            saved = await collector.collect_minute_candles(ticker, days=days)
            return saved
        except Exception as exc:
            logger.warning(f"{ticker} 수집 실패 (시도 {attempt}/{MAX_RETRY}): {exc}")
            if attempt < MAX_RETRY:
                await asyncio.sleep(2 ** attempt)
    return -1


async def _main(args: argparse.Namespace) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="{time:HH:mm:ss} | {level:<7} | {message}",
    )

    stocks = _load_universe()
    if args.tickers:
        requested = {t.strip() for t in args.tickers.split(",")}
        stocks = [s for s in stocks if str(s["ticker"]).zfill(6) in requested
                  or str(s["ticker"]) in requested]
        if not stocks:
            print("[ERROR] 매칭 종목 없음. universe_largecap.yaml 의 ticker 코드를 확인하세요.", flush=True)
            return

    total = len(stocks)
    print(f"[수집 시작] {total}종목 × {args.days}일치 분봉", flush=True)
    print(f"  유니버스: {UNIVERSE_PATH}", flush=True)

    config = AppConfig.from_yaml()
    db = DbManager(config.db_path)
    await db.init()

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
    collector = DataCollector(rest_client, db)

    results: dict[str, int] = {}
    failed: list[str] = []

    try:
        for idx, stock in enumerate(stocks, 1):
            ticker = str(stock["ticker"]).zfill(6)
            name   = stock.get("name", ticker)
            print(f"  [{idx:>2}/{total}] {ticker} {name} ...", flush=True, end=" ")

            saved = await _collect_with_retry(collector, ticker, args.days)
            results[ticker] = saved

            if saved < 0:
                failed.append(ticker)
                print(f"FAILED", flush=True)
            else:
                print(f"{saved:,}개 저장", flush=True)

    finally:
        await db.close()

    total_saved = sum(v for v in results.values() if v >= 0)
    print(f"\n{'='*50}", flush=True)
    print(f"[완료] {total}종목  총 {total_saved:,}개 캔들 저장", flush=True)
    if failed:
        print(f"[실패] {len(failed)}종목: {', '.join(failed)}", flush=True)
    print(f"{'='*50}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="KOSPI 대형주 분봉 배치 수집")
    parser.add_argument("--days",    type=int, default=DEFAULT_DAYS,
                        help=f"수집 영업일 수 (기본: {DEFAULT_DAYS})")
    parser.add_argument("--tickers", type=str, default="",
                        help="종목 코드 콤마 구분 (기본: 전체 50종목)")
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
