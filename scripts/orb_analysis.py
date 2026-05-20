"""scripts/orb_analysis.py — ORB 56건 월별/일별 분포 + rvol 감도 그리드.

사용법:
  python scripts/orb_analysis.py
"""

from __future__ import annotations

import asyncio
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd

from backtest.backtester_fast import ORBFastBacktester
from config.settings import TradingConfig
from data.db_manager import DbManager
from strategy.orb_strategy import ORBStrategy

OLD_START = "2025-04-01"
OLD_END = "2026-04-10"
NEW_START = "2026-04-11"
NEW_END = "2026-05-12"

UNIVERSE_FILE = Path("config/universe_backtest.yaml")


def _orb_config(rvol: float = 1.5) -> TradingConfig:
    return TradingConfig(
        market_filter_enabled=False,
        intraday_market_filter_enabled=False,
        blacklist_enabled=False,
        consecutive_loss_rest_enabled=False,
        adx_enabled=False,
        orb_enabled=True,
        orb_range_minutes=5,
        orb_sl_ratio=1.5,
        orb_tp_ratio=3.0,
        orb_entry_deadline="09:30",
        orb_breakout_buffer=0.0,
        orb_use_volume_filter=True,
        orb_rvol_min=rvol,
        orb_min_range_pct=0.005,
        orb_max_range_pct=0.10,
        max_trades_per_day=1,
        cooldown_minutes=999,
    )


async def load_candles(db: DbManager, tickers: list[str], start: str, end: str) -> dict:
    """ticker → pd.DataFrame (분봉)."""
    result: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        rows = await db.fetch_all(
            "SELECT ts, open, high, low, close, volume FROM intraday_candles "
            "WHERE ticker=? AND ts>=? AND ts<=? ORDER BY ts",
            (ticker, f"{start} 09:00:00", f"{end} 15:30:00"),
        )
        if not rows:
            continue
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"])
        result[ticker] = df
    return result


async def run_combo(
    candle_map: dict[str, pd.DataFrame],
    rvol: float,
    period_label: str,
) -> list[dict]:
    from config.settings import BacktestConfig
    cfg = _orb_config(rvol)
    bt_cfg = BacktestConfig()
    all_trades: list[dict] = []
    for ticker, df in candle_map.items():
        bt = ORBFastBacktester(
            db=None, config=cfg, backtest_config=bt_cfg,
            ticker_market="unknown", market_strong_by_date=None,
        )
        strategy = ORBStrategy(cfg)
        result = await bt.run_multi_day_cached(ticker, df, strategy)
        for t in result.get("trades", []):
            t["ticker"] = ticker
        all_trades.extend(result.get("trades", []))
    return all_trades


def compute_stats(trades: list[dict]) -> dict:
    if not trades:
        return dict(n=0, pf=0.0, pnl=0, win_rate=0.0)
    gross_win = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gross_loss = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    wins = sum(1 for t in trades if t["pnl"] > 0)
    return dict(n=len(trades), pf=round(pf, 3), pnl=int(sum(t["pnl"] for t in trades)), win_rate=round(wins / len(trades) * 100, 1))


def print_distribution(trades: list[dict]) -> None:
    if not trades:
        print("  거래 없음")
        return

    monthly: dict[str, list] = defaultdict(list)
    daily: dict[str, list] = defaultdict(list)
    for t in trades:
        d = t["entry_ts"].strftime("%Y-%m-%d")  # entry_ts는 datetime
        monthly[d[:7]].append(t)
        daily[d].append(t)

    print("\n[월별 거래 분포]")
    for ym in sorted(monthly):
        ms = monthly[ym]
        wins = sum(1 for t in ms if t["pnl"] > 0)
        total_pnl = int(sum(t["pnl"] for t in ms))
        print(f"  {ym}: {len(ms):3d}건  승 {wins:2d}  PnL {total_pnl:+,}")

    multi = [(d, ts) for d, ts in daily.items() if len(ts) >= 2]
    print(f"\n[일별 동시 진입: 2건 이상인 날 {len(multi)}일]")
    for d, ts in sorted(multi):
        print(f"  {d}: {len(ts)}건")
    max_day = max(daily, key=lambda d: len(daily[d]))
    print(f"  최대 {len(daily[max_day])}건 ({max_day})")


async def main() -> None:
    import yaml

    uni = yaml.safe_load(open(UNIVERSE_FILE, encoding="utf-8")) or {}
    tickers = [s["ticker"] for s in uni.get("stocks", [])]
    print(f"유니버스: {len(tickers)}종목")

    db = DbManager("daytrader.db")
    await db.init()

    print("분봉 로드 중 (OLD)...", flush=True)
    old_candles = await load_candles(db, tickers, OLD_START, OLD_END)
    print(f"  → {len(old_candles)}종목 로드")

    print("분봉 로드 중 (NEW)...", flush=True)
    new_candles = await load_candles(db, tickers, NEW_START, NEW_END)
    print(f"  → {len(new_candles)}종목 로드")

    await db.close()

    # ─── 1. 기준 조합(rvol=1.5) 분포 분석 ───────────────────────────────
    print("\n" + "=" * 60)
    print("ORB 기준 조합 (sl=1.5 / tp=3.0 / dl=09:30 / buf=0.0 / rvol=1.5)")
    print("=" * 60)

    old_trades_15 = await run_combo(old_candles, 1.5, "OLD")
    stats = compute_stats(old_trades_15)
    print(f"\nOLD 구간 ({OLD_START}~{OLD_END}): {stats['n']}건 / PF {stats['pf']} / PnL {stats['pnl']:+,} / 승률 {stats['win_rate']}%")
    print_distribution(old_trades_15)

    new_trades_15 = await run_combo(new_candles, 1.5, "NEW")
    stats_new = compute_stats(new_trades_15)
    print(f"\nNEW 구간 ({NEW_START}~{NEW_END}): {stats_new['n']}건 / PF {stats_new['pf']} / PnL {stats_new['pnl']:+,} / 승률 {stats_new['win_rate']}%")

    # ─── 2. rvol 감도 그리드 ─────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("rvol 감도 그리드 (sl=1.5 / tp=3.0 / dl=09:30 / buf=0.0)")
    print("=" * 60)
    print(f"{'rvol_min':>9} | {'OLD N':>6} {'OLD PF':>7} {'OLD PnL':>10} | {'NEW N':>6} {'NEW PF':>7} {'NEW PnL':>10}")
    print("-" * 68)

    for rvol in [1.0, 1.2, 1.5]:
        if rvol == 1.5:
            ot = old_trades_15
            nt = new_trades_15
        else:
            ot = await run_combo(old_candles, rvol, "OLD")
            nt = await run_combo(new_candles, rvol, "NEW")
        os_ = compute_stats(ot)
        ns_ = compute_stats(nt)
        print(
            f"  {rvol:>5.1f}   | {os_['n']:>6} {os_['pf']:>7.3f} {os_['pnl']:>+10,} | "
            f"{ns_['n']:>6} {ns_['pf']:>7.3f} {ns_['pnl']:>+10,}"
        )


if __name__ == "__main__":
    asyncio.run(main())
