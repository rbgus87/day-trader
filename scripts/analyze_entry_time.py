"""scripts/analyze_entry_time.py — 백테스트 진입 시간대 분포 + 거래량 통과 시각.

거래량 조건(전일 × 2.0)이 장 초반 진입을 가로막고 진입을 후반으로
편중시키는지 진단한다.

분석:
  1. 30분 단위 진입 분포 (09:05~12:00, 6구간)
  2. 시간대별 평균 PnL% / 승률 / PF
  3. 진입 trade가 거래량 조건을 만족한 가장 빠른 시각
     (ticker × date 사후 candles 조회 → cum_vol ≥ prev_vol × ratio 첫 시점)
  4. 거래량 통과 시각 vs 실제 진입 시각 갭

사용:
    python scripts/analyze_entry_time.py
    python scripts/analyze_entry_time.py --start 2025-04-01 --end 2026-04-10
"""

import argparse
import asyncio
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import yaml

from scripts.analyze_baseline import collect_all_trades

DB_PATH = "daytrader.db"

TIME_BINS = [
    ("09:05~09:30", "09:05", "09:30"),
    ("09:30~10:00", "09:30", "10:00"),
    ("10:00~10:30", "10:00", "10:30"),
    ("10:30~11:00", "10:30", "11:00"),
    ("11:00~11:30", "11:00", "11:30"),
    ("11:30~12:00", "11:30", "12:00"),
    ("12:00+",      "12:00", "23:59"),  # 매수 차단 후
]


def hhmm(ts) -> str:
    return pd.to_datetime(ts).strftime("%H:%M")


def to_bin(t: pd.Timestamp) -> str | None:
    s = t.strftime("%H:%M")
    for label, lo, hi in TIME_BINS:
        if lo <= s < hi:
            return label
    return None


# ---------------------------------------------------------------------------
# 1) 진입 시간대 분포 + PnL
# ---------------------------------------------------------------------------

def analyze_entry_time(trades: list[dict]) -> list[dict]:
    bin_data: dict[str, list[dict]] = {label: [] for label, _, _ in TIME_BINS}
    for t in trades:
        b = to_bin(pd.to_datetime(t["entry_ts"]))
        if b:
            bin_data[b].append(t)

    rows = []
    total = len(trades)
    for label, _, _ in TIME_BINS:
        ts_list = bin_data[label]
        n = len(ts_list)
        if n == 0:
            rows.append({
                "label": label, "count": 0, "ratio": 0.0,
                "avg_pnl": 0.0, "avg_pnl_pct": 0.0,
                "win_rate": 0.0, "pf": 0.0, "total_pnl": 0,
            })
            continue
        pnls = [x["pnl"] for x in ts_list]
        pcts = [x.get("pnl_pct", 0.0) for x in ts_list]
        wins = sum(1 for p in pnls if p > 0)
        gp = sum(p for p in pnls if p > 0)
        gl = abs(sum(p for p in pnls if p < 0))
        pf = gp / gl if gl > 0 else float("inf")
        rows.append({
            "label": label,
            "count": n,
            "ratio": n / total * 100,
            "avg_pnl": sum(pnls) / n,
            "avg_pnl_pct": sum(pcts) / n * 100,
            "win_rate": wins / n * 100,
            "pf": pf,
            "total_pnl": sum(pnls),
        })
    return rows


# ---------------------------------------------------------------------------
# 2) 거래량 조건 통과 시각 (사후 candles 조회)
# ---------------------------------------------------------------------------

def fetch_day_candles(
    conn: sqlite3.Connection, ticker: str, date_str: str
) -> pd.DataFrame:
    cur = conn.execute(
        "SELECT ts, volume FROM intraday_candles "
        "WHERE ticker=? AND tf='1m' AND ts >= ? AND ts <= ? "
        "ORDER BY ts",
        (ticker, f"{date_str} 00:00:00", f"{date_str} 23:59:59"),
    )
    rows = cur.fetchall()
    return pd.DataFrame(rows, columns=["ts", "volume"])


def get_prev_day_volume(
    conn: sqlite3.Connection, ticker: str, date: pd.Timestamp,
    day_cache: dict,
) -> int | None:
    """주말/공휴일을 건너뛰고 직전 거래일의 1분봉 volume 합계."""
    cur_date = date - pd.Timedelta(days=1)
    for _ in range(7):  # 최대 1주일 거슬러
        ds = cur_date.strftime("%Y-%m-%d")
        key = (ticker, ds)
        if key not in day_cache:
            df = fetch_day_candles(conn, ticker, ds)
            day_cache[key] = int(df["volume"].sum()) if not df.empty else 0
        if day_cache[key] > 0:
            return day_cache[key]
        cur_date -= pd.Timedelta(days=1)
    return None


def analyze_volume_pass(
    trades: list[dict], db_path: str, ratio: float = 2.0,
) -> dict:
    """각 진입 trade에 대해, 당일 cum_volume이 prev_day_volume × ratio를
    처음 넘은 시각을 계산.

    Returns:
        {
            "samples": [{ticker, entry_ts, vol_pass_ts, gap_min}, ...],
            "pass_bin_counts": {label: count},
            "no_pass": int,         # 해당일 거래량 미달인 trade 수 (정상이면 0)
            "avg_gap_min": float,   # 통과 시각과 진입 시각 사이 평균 분
        }
    """
    conn = sqlite3.connect(db_path)
    day_cache: dict = {}  # (ticker, date) -> total_volume
    candle_cache: dict = {}  # (ticker, date) -> DataFrame

    samples = []
    no_pass = 0
    pass_bin_counts: dict[str, int] = {label: 0 for label, _, _ in TIME_BINS}

    for t in trades:
        ts = pd.to_datetime(t["entry_ts"])
        ticker = t.get("ticker", "")
        date_str = ts.strftime("%Y-%m-%d")
        key = (ticker, date_str)
        if key not in candle_cache:
            candle_cache[key] = fetch_day_candles(conn, ticker, date_str)
        df = candle_cache[key]
        if df.empty:
            no_pass += 1
            continue

        prev_vol = get_prev_day_volume(conn, ticker, ts, day_cache)
        if not prev_vol:
            no_pass += 1
            continue
        threshold = prev_vol * ratio

        df = df.copy()
        df["ts"] = pd.to_datetime(df["ts"])
        df["cum"] = df["volume"].cumsum()
        # 09:00 이전(시간외/정규장 외) 캔들 제외
        intraday = df[df["ts"].dt.time >= pd.Timestamp("09:00").time()]
        passed = intraday[intraday["cum"] >= threshold]
        if passed.empty:
            no_pass += 1
            continue
        vol_pass_ts = passed.iloc[0]["ts"]
        b = to_bin(vol_pass_ts)
        if b:
            pass_bin_counts[b] += 1
        gap = (ts - vol_pass_ts).total_seconds() / 60
        samples.append({
            "ticker": ticker,
            "entry_ts": ts,
            "vol_pass_ts": vol_pass_ts,
            "gap_min": gap,
        })

    conn.close()

    avg_gap = (
        sum(s["gap_min"] for s in samples) / len(samples)
        if samples else 0.0
    )

    return {
        "samples": samples,
        "pass_bin_counts": pass_bin_counts,
        "no_pass": no_pass,
        "avg_gap_min": avg_gap,
        "ratio": ratio,
        "total_eligible": len(trades) - no_pass,
    }


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------

def print_entry_dist(rows: list[dict], total: int) -> None:
    print()
    print("=" * 88)
    print(f" 1) 진입 시간대 분포 (총 {total}건)")
    print("=" * 88)
    print(f"{'시간대':<14} {'건수':>5} {'비율':>7} {'평균PnL':>12} "
          f"{'평균%':>9} {'승률':>7} {'PF':>7} {'총PnL':>12}")
    print("-" * 88)
    for r in rows:
        if r["count"] == 0:
            continue
        pf_str = f"{r['pf']:.2f}" if r["pf"] != float("inf") else "∞"
        print(
            f"{r['label']:<14} "
            f"{r['count']:>5} "
            f"{r['ratio']:>6.1f}% "
            f"{r['avg_pnl']:>+12,.0f} "
            f"{r['avg_pnl_pct']:>+8.2f}% "
            f"{r['win_rate']:>6.1f}% "
            f"{pf_str:>7} "
            f"{r['total_pnl']:>+12,.0f}"
        )
    print("-" * 88)


def print_vol_pass(stats: dict, total_trades: int) -> None:
    print()
    print("=" * 88)
    print(f" 2) 거래량 통과 시각 분포 -- cum_vol >= prev_vol x {stats['ratio']:.1f}")
    print(f"    (분석 대상 {stats['total_eligible']}건 / 데이터 부족 스킵 {stats['no_pass']}건)")
    print("=" * 88)
    print(f"{'시간대':<14} {'건수':>5} {'비율':>7}")
    print("-" * 30)
    eligible = stats["total_eligible"]
    for label, _, _ in TIME_BINS:
        cnt = stats["pass_bin_counts"][label]
        if cnt == 0:
            continue
        ratio = cnt / eligible * 100 if eligible else 0
        print(f"{label:<14} {cnt:>5} {ratio:>6.1f}%")
    print("-" * 30)
    print(f" 통과 시각 → 실제 진입 시각 평균 갭: {stats['avg_gap_min']:+.1f}분")
    print(f"   (양수 = 거래량 통과 후 BREAKOUT 등 추가 조건 대기 시간)")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-04-01")
    parser.add_argument("--end", default="2026-04-10")
    parser.add_argument("--ratio", type=float, default=2.0,
                        help="거래량 비율 (기본 2.0 = config.yaml momentum_volume_ratio)")
    args = parser.parse_args()

    print("=" * 88)
    print(f" 진입 시간대 분석 ({args.start} ~ {args.end})")
    print("=" * 88)

    trades = await collect_all_trades(args.start, args.end)
    if not trades:
        print("거래 없음.")
        return

    # 1) 진입 시간대 분포
    rows = analyze_entry_time(trades)
    print_entry_dist(rows, len(trades))

    # 2) 거래량 통과 시각
    print()
    print(f"[VOL] 거래량 통과 시각 산정 중 ({len(trades)}건)...")
    vol_stats = analyze_volume_pass(trades, DB_PATH, ratio=args.ratio)
    print_vol_pass(vol_stats, len(trades))

    print()
    print("=" * 88)
    print(" 완료")
    print("=" * 88)


if __name__ == "__main__":
    asyncio.run(main())
