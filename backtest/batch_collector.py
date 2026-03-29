"""backtest/batch_collector.py — 다종목 분봉 데이터 배치 수집.

스크리너 결과 또는 유니버스 전체를 대상으로 과거 분봉 데이터를 수집한다.
CLI 스크립트로 독립 실행 가능.

사용법:
    # 유니버스 전체 수집 (30일)
    python -m backtest.batch_collector

    # 특정 종목만 수집
    python -m backtest.batch_collector --tickers 005930,000660 --days 60

    # 스크리너 결과 기반 수집
    python -m backtest.batch_collector --from-screener --date 2026-03-23
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

from backtest.data_collector import DataCollector
from config.settings import AppConfig
from core.auth import TokenManager
from core.kiwoom_rest import KiwoomRestClient
from core.rate_limiter import AsyncRateLimiter
from data.db_manager import DbManager

_DEFAULT_UNIVERSE_PATH = Path(__file__).parent.parent / "config" / "universe.yaml"


class BatchCollector:
    """다종목 분봉 데이터를 배치로 수집한다."""

    def __init__(self, collector: DataCollector, db: DbManager) -> None:
        self._collector = collector
        self._db = db

    async def collect_universe(self, days: int = 30) -> dict[str, int]:
        """유니버스 전체 종목의 분봉을 수집한다.

        Returns:
            {ticker: saved_count} 딕셔너리
        """
        tickers = self._load_universe_tickers()
        return await self._collect_multiple(tickers, days)

    async def collect_tickers(self, tickers: list[str], days: int = 30) -> dict[str, int]:
        """지정된 종목의 분봉을 수집한다."""
        return await self._collect_multiple(tickers, days)

    async def collect_from_screener(self, date: str, days: int = 30) -> dict[str, int]:
        """특정 날짜의 스크리너 결과 종목의 분봉을 수집한다.

        Args:
            date: 스크리너 실행 날짜 (YYYY-MM-DD)
            days: 수집할 영업일 수
        """
        rows = await self._db.fetch_all(
            "SELECT DISTINCT ticker FROM screener_results WHERE date = ? AND selected = 1",
            (date,),
        )
        if not rows:
            logger.warning(f"스크리너 결과 없음: {date}")
            return {}

        tickers = [row["ticker"] for row in rows]
        logger.info(f"스크리너 결과 ({date}): {len(tickers)}종목 — {tickers}")
        return await self._collect_multiple(tickers, days)

    async def _collect_multiple(self, tickers: list[str], days: int) -> dict[str, int]:
        """여러 종목의 분봉을 순차 수집한다."""
        results = {}
        total = len(tickers)

        logger.info(f"배치 수집 시작: {total}종목, {days}일치")

        for idx, ticker in enumerate(tickers, 1):
            try:
                saved = await self._collector.collect_minute_candles(ticker, days=days)
                results[ticker] = saved
                logger.info(f"[{idx}/{total}] {ticker} 수집 완료: {saved}개 저장")
            except Exception as exc:
                logger.error(f"[{idx}/{total}] {ticker} 수집 실패: {exc}")
                results[ticker] = 0

        total_saved = sum(results.values())
        logger.info(f"배치 수집 완료: {total}종목, 총 {total_saved}개 캔들 저장")
        return results

    @staticmethod
    def _load_universe_tickers() -> list[str]:
        """universe.yaml에서 종목코드만 추출한다."""
        if not _DEFAULT_UNIVERSE_PATH.exists():
            logger.error(f"유니버스 파일 없음: {_DEFAULT_UNIVERSE_PATH}")
            return []

        with open(_DEFAULT_UNIVERSE_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        stocks = data.get("stocks", [])
        return [s["ticker"] for s in stocks if "ticker" in s]


async def _main(args: argparse.Namespace) -> None:
    """CLI 진입점."""
    config = AppConfig.from_yaml()

    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="{time:HH:mm:ss} | {level:<7} | {message}",
    )

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
    batch = BatchCollector(collector, db)

    try:
        if args.from_screener:
            results = await batch.collect_from_screener(args.date, days=args.days)
        elif args.tickers:
            ticker_list = [t.strip() for t in args.tickers.split(",")]
            results = await batch.collect_tickers(ticker_list, days=args.days)
        else:
            results = await batch.collect_universe(days=args.days)

        # 결과 요약
        print(f"\n{'='*50}")
        print(f"배치 수집 결과: {len(results)}종목")
        print(f"{'='*50}")
        for ticker, count in sorted(results.items(), key=lambda x: -x[1]):
            print(f"  {ticker}: {count:,}개")
        print(f"{'─'*50}")
        print(f"  합계: {sum(results.values()):,}개")
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="백테스트용 분봉 데이터 배치 수집")
    parser.add_argument("--tickers", type=str, help="수집 종목 (콤마 구분, 예: 005930,000660)")
    parser.add_argument("--days", type=int, default=30, help="수집 영업일 수 (기본: 30)")
    parser.add_argument("--from-screener", action="store_true", help="스크리너 결과 기반 수집")
    parser.add_argument("--date", type=str, default="", help="스크리너 날짜 (YYYY-MM-DD)")

    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
