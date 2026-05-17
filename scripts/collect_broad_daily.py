"""scripts/collect_broad_daily.py — 광범위 유니버스 일봉 수집 (장중 실행).

300종목의 일봉 데이터를 ka10081로 수집해 ticker_daily_ohlcv 테이블에 저장한다.
ATR(14) 및 거래대금 계산에 최소 60영업일 데이터가 필요하므로 최대한 많이 수집.

주의: 키움 일봉 API(ka10081)는 장중에만 실시간 당일 데이터 반환.
      장외 실행 시 전일까지만 수집된다. (백테스트에는 전일까지면 충분)

사용법:
    python -u scripts/collect_broad_daily.py           # 전체 수집
    python -u scripts/collect_broad_daily.py --resume  # 미수집분만
    python -u scripts/collect_broad_daily.py --tickers 005930,000660  # 특정 종목
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import yaml
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.universe_simulator import ensure_daily_table
from config.settings import AppConfig
from core.auth import TokenManager
from core.kiwoom_rest import KiwoomRestClient
from core.rate_limiter import AsyncRateLimiter

_BROAD_UNIVERSE_PATH = Path(__file__).parent.parent / "config" / "universe_broad.yaml"
_BATCH_SIZE = 50
_TARGET_START_DATE = "20240101"     # ATR 계산용 여유 포함 (약 400영업일)


# ---------------------------------------------------------------------------
# 일봉 파싱
# ---------------------------------------------------------------------------

def _parse_daily_ohlcv(data: dict) -> list[tuple]:
    """ka10081 응답에서 (dt, open, high, low, close, volume, turnover) 추출."""
    output = (
        data.get("stk_dt_pole_chart_qry")
        or data.get("output2")
        or []
    )
    rows = []
    for item in output:
        try:
            dt = item.get("dt", "")
            if not dt or len(dt) != 8:
                continue
            open_  = abs(float(item.get("open_pric", 0) or 0))
            high   = abs(float(item.get("high_pric", 0) or 0))
            low    = abs(float(item.get("low_pric", 0) or 0))
            close  = abs(float(item.get("cur_prc", 0) or 0))
            volume = int(item.get("trde_qty", 0) or 0)
            # trde_prica: 거래대금 (백만원 단위) → 원 단위로 변환
            turnover = int(item.get("trde_prica", 0) or 0) * 1_000_000
            if close <= 0:
                continue
            rows.append((dt, open_, high, low, close, volume, turnover))
        except (ValueError, TypeError):
            continue
    return rows


# ---------------------------------------------------------------------------
# DB 상태 확인
# ---------------------------------------------------------------------------

def _check_db_status(db_path: str, tickers: list[str]) -> dict[str, dict]:
    conn = sqlite3.connect(db_path)
    result: dict[str, dict] = {}
    try:
        for ticker in tickers:
            cur = conn.execute(
                "SELECT COUNT(*) as cnt, MIN(dt) as min_dt, MAX(dt) as max_dt "
                "FROM ticker_daily_ohlcv WHERE ticker=?",
                (ticker,),
            )
            row = cur.fetchone()
            cnt, min_dt, max_dt = (row or (0, None, None))
            result[ticker] = {"cnt": int(cnt or 0), "min_dt": min_dt, "max_dt": max_dt}
    finally:
        conn.close()
    return result


# ---------------------------------------------------------------------------
# 일봉 수집
# ---------------------------------------------------------------------------

_INSERT_SQL = (
    "INSERT OR REPLACE INTO ticker_daily_ohlcv "
    "(ticker, dt, open, high, low, close, volume, turnover) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)


async def _collect_one(
    ticker: str,
    rest_client: KiwoomRestClient,
    db_path: str,
    sem: asyncio.Semaphore,
    start_date: str,     # YYYYMMDD
) -> int:
    """단일 종목 일봉 수집."""
    from datetime import date as _date
    async with sem:
        total_saved = 0
        base_dt = _date.today().strftime("%Y%m%d")  # ka10081은 base_dt 필수
        conn = sqlite3.connect(db_path)
        try:
            for _page in range(50):   # 최대 50 페이지 (1페이지 ≈ 900일)
                try:
                    data = await rest_client.get_daily_ohlcv(ticker, base_dt=base_dt)
                except Exception as e:
                    logger.debug(f"[{ticker}] 일봉 API 실패: {e}")
                    break

                rows = _parse_daily_ohlcv(data)
                if not rows:
                    break

                # 시작일 필터
                filtered = [(dt, o, h, l, c, v, t) for dt, o, h, l, c, v, t in rows if dt >= start_date]
                if filtered:
                    batch = [(ticker, dt, o, h, l, c, v, t) for dt, o, h, l, c, v, t in filtered]
                    conn.executemany(_INSERT_SQL, batch)
                    conn.commit()
                    total_saved += len(batch)

                # 가장 오래된 데이터가 start_date 이전이면 종료
                oldest_dt = rows[-1][0]
                if oldest_dt <= start_date:
                    break

                # 페이지네이션
                if oldest_dt == base_dt:
                    break
                base_dt = oldest_dt

                if len(rows) < 900:
                    break

        finally:
            conn.close()

        return total_saved


async def collect_all(
    tickers: list[str],
    rest_client: KiwoomRestClient,
    db_path: str,
    resume: bool,
) -> dict[str, int]:
    """전체 종목 일봉 수집 (Semaphore(5) 병렬)."""
    sem = asyncio.Semaphore(5)

    db_status = _check_db_status(db_path, tickers)
    tasks = []
    skipped = []

    for ticker in tickers:
        status = db_status.get(ticker, {"cnt": 0, "min_dt": None})
        if resume and status["cnt"] > 0 and status.get("min_dt") and status["min_dt"] <= "20240110":
            skipped.append(ticker)
            continue
        tasks.append((ticker, _collect_one(ticker, rest_client, db_path, sem, _TARGET_START_DATE)))

    if skipped:
        logger.info(f"스킵 (기존 데이터 충분): {len(skipped)}종목")

    results: dict[str, int] = {t: 0 for t in skipped}
    coros = [(ticker, coro) for ticker, coro in tasks]

    for i in range(0, len(coros), _BATCH_SIZE):
        chunk = coros[i:i + _BATCH_SIZE]
        chunk_tasks = [(t, asyncio.create_task(c)) for t, c in chunk]

        for ticker, task in chunk_tasks:
            try:
                saved = await task
                results[ticker] = saved
                logger.info(f"[{ticker}] {saved}개 저장")
            except Exception as e:
                logger.warning(f"[{ticker}] 실패: {e}")
                results[ticker] = 0

        done = sum(1 for v in results.values())
        logger.info(f"진행: {done}/{len(tickers)} — 저장 {sum(results.values()):,}건")

    return results


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

async def _main(args: argparse.Namespace) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level:<7} | {message}")

    config = AppConfig.from_yaml()
    db_path = config.db_path

    # ticker_daily_ohlcv 테이블 생성
    ensure_daily_table(db_path)
    logger.info("ticker_daily_ohlcv 테이블 준비 완료")

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

    try:
        # 종목 결정
        if args.tickers:
            tickers = [t.strip() for t in args.tickers.split(",")]
        else:
            if not _BROAD_UNIVERSE_PATH.exists():
                logger.error(f"유니버스 파일 없음: {_BROAD_UNIVERSE_PATH}")
                logger.error("먼저 python -u scripts/collect_broad_universe.py --select-only 실행")
                return
            with open(_BROAD_UNIVERSE_PATH, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            tickers = [s["ticker"] for s in data.get("stocks", [])]

        logger.info(f"일봉 수집 대상: {len(tickers)}종목 (시작일 {_TARGET_START_DATE})")
        t0 = time.time()

        results = await collect_all(tickers, rest_client, db_path, args.resume)

        total_saved = sum(v for v in results.values())
        elapsed = (time.time() - t0) / 60
        logger.info(f"\n{'='*60}")
        logger.info(f"일봉 수집 완료: {len(results)}종목 / {total_saved:,}건 / {elapsed:.1f}분")
        logger.info(f"{'='*60}\n")

    finally:
        if rest_client._session:
            await rest_client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="광범위 유니버스 일봉 수집")
    parser.add_argument("--resume", action="store_true", help="미수집분만 이어서")
    parser.add_argument("--tickers", type=str, default="", help="특정 종목 (콤마 구분)")
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
