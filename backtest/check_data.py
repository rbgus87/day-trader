"""backtest/check_data.py — DB 상태 점검 + 백테스트 준비도 확인.

사용법:
    python -m backtest.check_data
    python -m backtest.check_data --ticker 005930
"""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv
load_dotenv()

from pathlib import Path
from config.settings import AppConfig
from data.db_manager import DbManager


async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="DB 상태 점검")
    parser.add_argument("--ticker", type=str, default="", help="특정 종목 상세 조회")
    args = parser.parse_args()

    config = AppConfig.from_yaml()
    db_path = Path(config.db_path)

    print("=" * 70)
    print("  day-trader DB 상태 점검")
    print("=" * 70)

    # 1. DB 파일 존재 여부
    if not db_path.exists():
        print(f"\n  ❌ DB 파일 없음: {db_path}")
        print("     → python -c \"...\" 으로 DB 초기화 필요")
        return

    file_size = db_path.stat().st_size
    print(f"\n  DB 파일: {db_path} ({file_size / 1024 / 1024:.1f} MB)")

    db = DbManager(str(db_path))
    await db.init()

    try:
        # 2. 테이블 존재 확인
        tables = await db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        table_names = [t["name"] for t in tables]
        print(f"  테이블: {', '.join(table_names)}")

        # 3. intraday_candles 상태
        print("\n" + "-" * 70)
        print("  📊 intraday_candles (백테스트 핵심 데이터)")
        print("-" * 70)

        if "intraday_candles" not in table_names:
            print("  ❌ intraday_candles 테이블 없음")
            await db.close()
            return

        # 총 행 수
        row = await db.fetch_one("SELECT COUNT(*) as cnt FROM intraday_candles")
        total_candles = row["cnt"] if row else 0
        print(f"  총 캔들 수: {total_candles:,}")

        if total_candles == 0:
            print("\n  ❌ 분봉 데이터 없음 — 백테스트 불가")
            print("  → 데이터 수집 필요:")
            print("    python -m backtest.batch_collector --days 30")
            print("    (유니버스 48종목 × 30일, 예상 소요: 1~2시간)")
            print()
            print("  또는 주요 종목만 먼저:")
            print("    python -m backtest.batch_collector --tickers 042700,196170,329180,005930 --days 30")
            await db.close()
            return

        # 종목별 분포
        ticker_stats = await db.fetch_all("""
            SELECT ticker,
                   COUNT(*) as cnt,
                   MIN(ts) as first_ts,
                   MAX(ts) as last_ts,
                   COUNT(DISTINCT substr(ts, 1, 10)) as days
            FROM intraday_candles
            WHERE tf = '1m'
            GROUP BY ticker
            ORDER BY cnt DESC
        """)

        print(f"  종목 수: {len(ticker_stats)}")
        print()
        print(f"  {'종목':>8} | {'캔들수':>8} {'거래일':>6} {'최초 데이터':>12} {'최종 데이터':>12}")
        print("  " + "-" * 60)

        bt_ready = []  # 백테스트 가능 종목
        for s in ticker_stats[:20]:  # 상위 20개만 표시
            first = s["first_ts"][:10] if s["first_ts"] else "?"
            last = s["last_ts"][:10] if s["last_ts"] else "?"
            days = s["days"]
            print(f"  {s['ticker']:>8} | {s['cnt']:>8,} {days:>6} {first:>12} {last:>12}")
            if days >= 10:
                bt_ready.append(s["ticker"])

        if len(ticker_stats) > 20:
            print(f"  ... 외 {len(ticker_stats) - 20}종목")

        # 날짜 범위
        date_range = await db.fetch_one("""
            SELECT MIN(substr(ts, 1, 10)) as first_date,
                   MAX(substr(ts, 1, 10)) as last_date,
                   COUNT(DISTINCT substr(ts, 1, 10)) as total_days
            FROM intraday_candles WHERE tf = '1m'
        """)
        if date_range:
            print()
            print(f"  전체 기간: {date_range['first_date']} ~ {date_range['last_date']} ({date_range['total_days']}거래일)")

        # 4. 백테스트 준비도 판정
        print("\n" + "-" * 70)
        print("  🎯 백테스트 준비도")
        print("-" * 70)

        if len(bt_ready) >= 3 and date_range and date_range["total_days"] >= 10:
            print(f"  ✅ 백테스트 가능: {len(bt_ready)}종목 (10일+ 데이터)")
            print()
            print("  실행 방법:")
            for t in bt_ready[:5]:
                first = next(s for s in ticker_stats if s["ticker"] == t)
                fd = first["first_ts"][:10] if first["first_ts"] else "2026-02-01"
                ld = first["last_ts"][:10] if first["last_ts"] else "2026-03-29"
                print(f"    python -m backtest.run_all_strategies --ticker {t} --start {fd} --end {ld}")
        elif total_candles > 0 and len(bt_ready) < 3:
            print(f"  ⚠️ 데이터 부족: 10일+ 데이터 보유 종목 {len(bt_ready)}개")
            print("  → 추가 수집 필요:")
            print("    python -m backtest.batch_collector --days 30")
        else:
            print("  ❌ 백테스트 불가 — 데이터 수집 먼저 실행")

        # 5. 특정 종목 상세 (--ticker 옵션)
        if args.ticker:
            print("\n" + "-" * 70)
            print(f"  🔍 {args.ticker} 상세")
            print("-" * 70)

            detail = await db.fetch_all("""
                SELECT substr(ts, 1, 10) as date,
                       COUNT(*) as candles,
                       MIN(substr(ts, 12, 5)) as first_time,
                       MAX(substr(ts, 12, 5)) as last_time,
                       MIN(low) as day_low,
                       MAX(high) as day_high,
                       SUM(volume) as total_vol
                FROM intraday_candles
                WHERE ticker = ? AND tf = '1m'
                GROUP BY substr(ts, 1, 10)
                ORDER BY date
            """, (args.ticker,))

            if not detail:
                print(f"  데이터 없음: {args.ticker}")
            else:
                print(f"  {'날짜':>12} {'캔들':>5} {'시작':>6} {'종료':>6} {'저가':>10} {'고가':>10} {'거래량':>12}")
                print("  " + "-" * 70)
                for d in detail[-20:]:  # 최근 20일
                    print(
                        f"  {d['date']:>12} {d['candles']:>5} "
                        f"{d['first_time'] or '?':>6} {d['last_time'] or '?':>6} "
                        f"{d['day_low'] or 0:>10,.0f} {d['day_high'] or 0:>10,.0f} "
                        f"{d['total_vol'] or 0:>12,}"
                    )

        # 6. 다른 테이블 상태
        print("\n" + "-" * 70)
        print("  📋 기타 테이블 상태")
        print("-" * 70)

        for tbl in ["trades", "positions", "daily_pnl", "screener_results", "system_log"]:
            if tbl in table_names:
                r = await db.fetch_one(f"SELECT COUNT(*) as cnt FROM {tbl}")
                cnt = r["cnt"] if r else 0
                print(f"  {tbl:<20}: {cnt:>6,}행")

    finally:
        await db.close()

    print("\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
