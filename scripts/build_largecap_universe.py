"""scripts/build_largecap_universe.py — KOSPI 대형주 유니버스 검증 및 DB 데이터 현황 조회.

config/universe_largecap.yaml 을 읽어 종목 목록을 출력하고,
intraday_candles DB에 각 종목의 수집 현황(캔들 수, 최초/최신 날짜)을 확인한다.

사용법:
    python scripts/build_largecap_universe.py           # 목록 + DB 현황
    python scripts/build_largecap_universe.py --list    # 목록만 출력 (DB 조회 없음)
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

UNIVERSE_PATH = Path(__file__).parent.parent / "config" / "universe_largecap.yaml"


def _load_universe() -> list[dict]:
    data = yaml.safe_load(UNIVERSE_PATH.read_text(encoding="utf-8")) or {}
    return data.get("stocks", [])


async def _check_db(stocks: list[dict]) -> None:
    from config.settings import AppConfig
    from data.db_manager import DbManager

    config = AppConfig.from_yaml()
    db = DbManager(config.db_path)
    await db.init()

    print(f"\n{'ticker':>8} {'종목명':<14} {'캔들수':>8} {'최초날짜':>12} {'최신날짜':>12}", flush=True)
    print("-" * 60, flush=True)

    total_candles = 0
    missing: list[str] = []

    try:
        for stock in stocks:
            ticker = str(stock["ticker"]).zfill(6)
            name   = stock.get("name", ticker)

            row = await db.fetch_one(
                "SELECT COUNT(*) as cnt, MIN(ts) as first_ts, MAX(ts) as last_ts "
                "FROM intraday_candles WHERE ticker = ? AND tf = '1m'",
                (ticker,),
            )
            cnt      = row["cnt"] if row else 0
            first_ts = (row["first_ts"] or "")[:10] if row else ""
            last_ts  = (row["last_ts"] or "")[:10] if row else ""

            total_candles += cnt
            if cnt == 0:
                missing.append(ticker)

            status = "" if cnt > 0 else "  ← 미수집"
            print(
                f"{ticker:>8} {name:<14} {cnt:>8,} {first_ts:>12} {last_ts:>12}{status}",
                flush=True,
            )

    finally:
        await db.close()

    print("-" * 60, flush=True)
    print(f"{'합계':>24} {total_candles:>8,}", flush=True)

    if missing:
        print(f"\n[미수집] {len(missing)}종목: {', '.join(missing)}", flush=True)
        print("  → python scripts/collect_largecap_candles.py 실행 후 재확인", flush=True)
    else:
        print("\n[OK] 전 종목 데이터 수집 완료", flush=True)


async def _main(args: argparse.Namespace) -> None:
    stocks = _load_universe()
    total = len(stocks)

    print(f"[universe_largecap.yaml] {total}종목", flush=True)
    print(f"  파일: {UNIVERSE_PATH}", flush=True)

    kospi  = [s for s in stocks if s.get("market") == "kospi"]
    kosdaq = [s for s in stocks if s.get("market") == "kosdaq"]
    print(f"  KOSPI: {len(kospi)}종목 / KOSDAQ: {len(kosdaq)}종목", flush=True)

    print(f"\n{'No':>3} {'ticker':>8} {'종목명':<16} {'시장':>6}", flush=True)
    print("-" * 40, flush=True)
    for i, s in enumerate(stocks, 1):
        print(
            f"{i:>3} {str(s['ticker']).zfill(6):>8} "
            f"{s.get('name', '?'):<16} {s.get('market', '?'):>6}",
            flush=True,
        )

    if args.list:
        return

    print("\n[DB 데이터 현황 조회 중...]", flush=True)
    await _check_db(stocks)


def main() -> None:
    parser = argparse.ArgumentParser(description="KOSPI 대형주 유니버스 검증")
    parser.add_argument("--list", action="store_true", help="목록만 출력 (DB 조회 없음)")
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
