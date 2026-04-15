"""scripts/identify_data_gaps.py - 60종목 x 1년 coverage 누락 식별.

Phase 4 데이터 수집 사전 조사. API 호출 없이 DB만 조회.

출력:
- reports/data_gaps_report.csv
- reports/data_gaps_summary.txt
- 콘솔 요약

사용:
    python scripts/identify_data_gaps.py
    python scripts/identify_data_gaps.py --start 2025-04-01 --end 2026-04-15
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml

from utils.market_calendar import is_trading_day

DB_PATH = Path("daytrader.db")
UNIVERSE_PATH = Path("config/universe.yaml")
REPORTS_DIR = Path("reports")
CANDLES_PER_DAY = 380  # 1분봉 추정 (09:00~15:30 = 390분, 실제는 ~380)


def load_universe() -> list[dict]:
    if not UNIVERSE_PATH.exists():
        print(f"[ERROR] universe 파일 없음: {UNIVERSE_PATH}")
        sys.exit(1)
    uni = yaml.safe_load(open(UNIVERSE_PATH, encoding="utf-8")) or {}
    return uni.get("stocks", [])


def trading_days(start: date, end: date) -> list[date]:
    days = []
    d = start
    while d <= end:
        if is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)
    return days


def ticker_coverage(conn, ticker: str, all_trading_days: set[date]) -> dict:
    rows = conn.execute(
        "SELECT DISTINCT date(ts) FROM intraday_candles "
        "WHERE ticker=? AND date(ts) BETWEEN ? AND ?",
        (ticker, min(all_trading_days).isoformat(),
         max(all_trading_days).isoformat()),
    ).fetchall()
    covered = {date.fromisoformat(r[0]) for r in rows if r[0]}
    missing = sorted(all_trading_days - covered)
    return {
        "covered_days": len(covered),
        "missing_days": len(missing),
        "missing_dates": [d.isoformat() for d in missing],
        "is_full": len(missing) == 0,
        "is_empty": len(covered) == 0,
    }


def check_phantom_tickers(conn, universe_tickers: set[str]) -> list[str]:
    """intraday_candles에 있지만 universe에 없는 종목."""
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM intraday_candles"
    ).fetchall()
    db_tickers = {r[0] for r in rows}
    return sorted(db_tickers - universe_tickers)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-04-01")
    parser.add_argument("--end", default="2026-04-15")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    REPORTS_DIR.mkdir(exist_ok=True)

    universe = load_universe()
    universe_tickers = {s["ticker"] for s in universe}
    days = trading_days(start, end)
    days_set = set(days)

    conn = sqlite3.connect(DB_PATH)
    try:
        # 종목별 coverage
        results = []
        for s in universe:
            ticker = s["ticker"]
            cov = ticker_coverage(conn, ticker, days_set)
            cov.update({
                "ticker": ticker,
                "name": s.get("name", ""),
                "market": s.get("market", ""),
            })
            results.append(cov)

        phantom = check_phantom_tickers(conn, universe_tickers)
    finally:
        conn.close()

    # CSV
    csv_path = REPORTS_DIR / "data_gaps_report.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "name", "market", "covered_days",
                    "missing_days", "missing_dates"])
        for r in sorted(results, key=lambda x: x["missing_days"], reverse=True):
            w.writerow([
                r["ticker"], r["name"], r["market"],
                r["covered_days"], r["missing_days"],
                ";".join(r["missing_dates"]) if r["missing_days"] <= 20
                else f"... ({r['missing_days']}개)",
            ])

    # 통계
    total_days = len(days)
    full_cov = [r for r in results if r["is_full"]]
    partial = [r for r in results if not r["is_full"] and not r["is_empty"]]
    empty = [r for r in results if r["is_empty"]]

    total_missing = sum(r["missing_days"] for r in results)
    avg_missing = total_missing / len(results) if results else 0
    est_bars = total_missing * CANDLES_PER_DAY

    # 4/11 ~ 종료 누락 (최근 gap)
    recent_cutoff = date(2026, 4, 11)
    recent_days = [d for d in days if d >= recent_cutoff]
    recent_missing_tickers = 0
    for r in results:
        missing_dates = {date.fromisoformat(d) for d in r["missing_dates"]}
        if any(d in missing_dates for d in recent_days):
            recent_missing_tickers += 1

    # TXT 요약
    summary_path = REPORTS_DIR / "data_gaps_summary.txt"
    lines = [
        "=" * 70,
        f"데이터 누락 조사 ({datetime.now():%Y-%m-%d %H:%M:%S})",
        "=" * 70,
        f"기간        : {args.start} ~ {args.end}",
        f"거래일수    : {total_days}일",
        f"universe    : {len(universe)}종목",
        f"CANDLES/DAY : {CANDLES_PER_DAY} (추정)",
        "",
        "[coverage 분포]",
        f"  풀 coverage (전 {total_days}일) : {len(full_cov)}종목",
        f"  부분 coverage               : {len(partial)}종목",
        f"  완전 empty (0일)             : {len(empty)}종목",
        "",
        "[누락 통계]",
        f"  평균 누락 일수             : {avg_missing:.1f}일",
        f"  총 누락 (종목x일)          : {total_missing}종목-일",
        f"  추정 수집 대상 분봉        : {est_bars:,}건",
        "",
        f"[최근 gap (>= {recent_cutoff})]",
        f"  {recent_cutoff}~{args.end} 구간 미수집 종목: {recent_missing_tickers}개",
        f"  (이 구간 거래일 수: {len(recent_days)})",
        "",
    ]
    if empty:
        lines.append("[완전 empty 종목 (수집 불가 후보)]")
        for r in empty:
            lines.append(f"  - {r['ticker']} ({r['name']}, {r['market']})")
        lines.append("")
    if phantom:
        lines.append(f"[universe 외 종목 데이터 (옛 universe 잔재) - {len(phantom)}개]")
        for t in phantom[:20]:
            lines.append(f"  - {t}")
        if len(phantom) > 20:
            lines.append(f"  ... 총 {len(phantom)}개")
        lines.append("")
    lines.append("[누락 일수 상위 20종목]")
    for r in sorted(results, key=lambda x: x["missing_days"], reverse=True)[:20]:
        lines.append(
            f"  {r['ticker']} {r['name']:<15} "
            f"[{r['market']:<6}] 누락 {r['missing_days']:>3}일 / "
            f"coverage {r['covered_days']:>3}/{total_days}"
        )
    lines.append("")
    lines.append(f"CSV: {csv_path}")

    out = "\n".join(lines)
    summary_path.write_text(out, encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
