"""scripts/verify_data_coverage.py - Phase 4 데이터 수집 후 정합성 검증.

60종목 x 거래일 매트릭스 출력. 풀 coverage 확인 + 누락 셀 보고.

사용:
    python scripts/verify_data_coverage.py
    python scripts/verify_data_coverage.py --start 2025-04-01 --end 2026-04-15
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml

from utils.market_calendar import is_trading_day

DB_PATH = Path("daytrader.db")
UNIVERSE_PATH = Path("config/universe.yaml")
EXPECTED_BARS_MIN = 300  # 일당 분봉 최소 (점심 휴장 반영)
EXPECTED_BARS_MAX = 400


def load_universe():
    uni = yaml.safe_load(open(UNIVERSE_PATH, encoding="utf-8")) or {}
    return uni.get("stocks", [])


def trading_days_in_range(start: date, end: date) -> list[date]:
    days = []
    d = start
    while d <= end:
        if is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)
    return days


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-04-01")
    parser.add_argument("--end", default="2026-04-15")
    parser.add_argument("--matrix", action="store_true",
                        help="월별 매트릭스 출력")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    days = trading_days_in_range(start, end)
    days_set = set(days)

    universe = load_universe()
    universe_tickers = [s["ticker"] for s in universe]
    name_map = {s["ticker"]: s.get("name", "") for s in universe}

    conn = sqlite3.connect(DB_PATH)
    try:
        # 종목 x 일자 데이터
        # ticker -> {date: bars_count}
        coverage: dict[str, dict[date, int]] = defaultdict(dict)
        rows = conn.execute(
            "SELECT ticker, date(ts) AS d, COUNT(*) AS bars "
            "FROM intraday_candles "
            "WHERE date(ts) BETWEEN ? AND ? "
            "GROUP BY ticker, d",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        for t, d_str, bars in rows:
            try:
                d = date.fromisoformat(d_str)
                coverage[t][d] = bars
            except Exception:
                pass
    finally:
        conn.close()

    # 분류
    full_cov = []
    partial = []
    short = []  # < 200일 (신규 상장 추정)
    empty = []

    abnormal_bar_days = []  # (ticker, date, bars) - 분봉 수 비정상

    for ticker in universe_tickers:
        cov = coverage.get(ticker, {})
        covered_days = set(cov.keys())
        present_in_range = covered_days & days_set
        missing = days_set - covered_days

        # 비정상 분봉 수 검사
        for d, bars in cov.items():
            if d in days_set and (bars < EXPECTED_BARS_MIN or bars > EXPECTED_BARS_MAX):
                abnormal_bar_days.append((ticker, d, bars))

        n = len(present_in_range)
        if n == len(days):
            full_cov.append(ticker)
        elif n == 0:
            empty.append(ticker)
        elif n < 200:
            short.append((ticker, n))
        else:
            partial.append((ticker, n, sorted(missing)))

    total_days = len(days)
    print("=" * 70)
    print(f" Phase 4 데이터 coverage 검증")
    print("=" * 70)
    print(f"기간       : {args.start} ~ {args.end}")
    print(f"거래일수   : {total_days}일")
    print(f"universe   : {len(universe_tickers)}종목")
    print()
    print("[Coverage 분포]")
    print(f"  Full coverage ({total_days}일 전부): {len(full_cov)}종목")
    print(f"  Partial coverage (200~{total_days-1}일): {len(partial)}종목")
    print(f"  Short coverage (< 200일, 신규 상장 추정): {len(short)}종목")
    print(f"  Empty (0일): {len(empty)}종목")
    print()

    if short:
        print("[Short coverage 종목 (정상 - 신규 상장)]")
        for t, n in short:
            print(f"  {t} {name_map.get(t, ''):<15} {n}일 / {total_days}")
        print()

    if partial:
        print("[Partial coverage 종목 (누락 있음)]")
        for t, n, missing in sorted(partial, key=lambda x: x[1]):
            miss_count = len(missing)
            sample = ", ".join(d.isoformat() for d in missing[:5])
            ellipsis = "..." if miss_count > 5 else ""
            print(f"  {t} {name_map.get(t, ''):<15} {n}일 누락 {miss_count}일 [{sample}{ellipsis}]")
        print()

    if empty:
        print(f"[Empty 종목] {len(empty)}개")
        for t in empty:
            print(f"  {t} {name_map.get(t, '')}")
        print()

    if abnormal_bar_days:
        print(f"[비정상 분봉 수 일자 (300~400 범위 외, 점심 휴장 반영)] {len(abnormal_bar_days)}건")
        for t, d, bars in abnormal_bar_days[:10]:
            print(f"  {t} {d}: {bars} bars")
        if len(abnormal_bar_days) > 10:
            print(f"  ...총 {len(abnormal_bar_days)}건")
        print()

    # 매트릭스 (월별)
    if args.matrix:
        print("[월별 매트릭스 - 종목 x 월별 거래일 coverage]")
        months = sorted({(d.year, d.month) for d in days})
        month_days = {(y, m): [d for d in days if d.year == y and d.month == m]
                      for y, m in months}
        header = "ticker          " + " ".join(f"{y%100:02d}/{m:02d}" for y, m in months)
        print(header)
        for ticker in universe_tickers:
            cov = coverage.get(ticker, {})
            line = f"{ticker} {name_map.get(ticker, ''):<10}"
            for y, m in months:
                expected = len(month_days[(y, m)])
                got = sum(1 for d in month_days[(y, m)] if d in cov)
                if got == expected:
                    line += "  OK "
                elif got == 0:
                    line += "  -- "
                else:
                    line += f" {got:>2}/{expected:<2}"
            print(line)

    print()
    # 최종 평가
    rate = (len(full_cov) + len(short)) / len(universe_tickers) * 100
    print(f"=== 최종 평가 ===")
    print(f"  사용 가능 종목: {len(full_cov) + len(short)}/{len(universe_tickers)} ({rate:.0f}%)")
    print(f"    - Full: {len(full_cov)}, Short(신규상장): {len(short)}, Partial: {len(partial)}, Empty: {len(empty)}")
    return 0 if not partial and not empty else 1


if __name__ == "__main__":
    sys.exit(main())
