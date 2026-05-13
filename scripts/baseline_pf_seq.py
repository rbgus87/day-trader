"""scripts/baseline_pf_seq.py — baseline PF measurement (sequential, no multiprocessing).

Sequential version of baseline_pf_limit_up.py for environments where
ProcessPoolExecutor fails (Windows multiprocessing BrokenProcessPool).
"""

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig
from data.db_manager import DbManager
from strategy.momentum_strategy import MomentumStrategy


async def collect_all_trades_seq(start: str, end: str) -> list[dict]:
    app_config = AppConfig.from_yaml()
    base_config = app_config.trading

    bt_cfg_raw = yaml.safe_load(
        open("config.yaml", encoding="utf-8")
    ).get("backtest", {})
    backtest_config = BacktestConfig(
        commission=bt_cfg_raw.get("commission", 0.00015),
        tax=bt_cfg_raw.get("tax", 0.0020),
        slippage=bt_cfg_raw.get("slippage", 0.0003),
    )

    uni = yaml.safe_load(open("config/universe_backtest.yaml", encoding="utf-8"))
    stocks = uni.get("stocks", [])
    ticker_to_market = {s["ticker"]: s.get("market", "unknown") for s in stocks}

    db = DbManager(app_config.db_path)
    await db.init()
    bt_loader = Backtester(db=db, config=base_config, backtest_config=backtest_config)

    print(f"[LOAD] candles ({len(stocks)} stocks, {start}~{end})")
    candles_cache: dict = {}
    for i, s in enumerate(stocks, 1):
        tk = s["ticker"]
        candles = await bt_loader.load_candles(tk, start, f"{end} 23:59:59")
        if not candles.empty:
            candles_cache[tk] = candles
        if i % 10 == 0:
            print(f"  loaded {i}/{len(stocks)}")
    print(f"[LOAD] done {len(candles_cache)}/{len(stocks)}")
    await db.close()

    market_map = build_market_strong_by_date(
        app_config.db_path, ma_length=base_config.market_ma_length
    )

    all_trades = []
    print(f"[RUN] sequential backtest for {len(candles_cache)} tickers")
    for i, (tk, candles) in enumerate(candles_cache.items(), 1):
        market = ticker_to_market.get(tk, "unknown")
        bt = Backtester(
            db=None,
            config=base_config,
            backtest_config=backtest_config,
            ticker_market=market,
            market_strong_by_date=market_map,
        )
        strategy = MomentumStrategy(base_config)
        result = await bt.run_multi_day_cached(tk, candles, strategy)
        for t in result.get("trades", []):
            t["ticker"] = tk
            t["ticker_market"] = market
            all_trades.append(t)
        if i % 5 == 0:
            print(f"  done {i}/{len(candles_cache)} (cumulative trades={len(all_trades)})")

    print(f"[DONE] total {len(all_trades)} trades")
    return all_trades


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-04-01")
    parser.add_argument("--end", default="2026-04-10")
    args = parser.parse_args()

    print("=" * 64)
    print(f" baseline PF (sequential)  ({args.start} ~ {args.end})")
    print("=" * 64)

    trades = await collect_all_trades_seq(args.start, args.end)
    if not trades:
        print("ERROR: no trades.")
        return

    total = len(trades)
    pnl = sum(t["pnl"] for t in trades)
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    pf = gp / gl if gl > 0 else float("inf")
    exit_dist = Counter(t.get("exit_reason", "?") for t in trades)

    print()
    print(f"  total trades: {total}")
    print(f"  PF          : {pf:.3f}")
    print(f"  total PnL   : {pnl:+,.0f}")
    print(f"  PnL/trade   : {pnl/total:+,.1f}")
    print()
    print("  exit reasons:")
    for reason, cnt in exit_dist.most_common():
        ratio = cnt / total * 100
        print(f"    {reason:<20} {cnt:>4} ({ratio:>5.1f}%)")

    lu = exit_dist.get("limit_up_exit", 0)
    lu_trades = [t for t in trades if t.get("exit_reason") == "limit_up_exit"]
    if lu:
        lu_pnl = sum(t["pnl"] for t in lu_trades)
        lu_avg_pct = sum(t.get("pnl_pct", 0) for t in lu_trades) / lu
        print()
        print(f"  limit_up_exit details:")
        print(f"    n={lu} / total PnL {lu_pnl:+,.0f} / avg PnL% {lu_avg_pct*100:+.2f}%")


if __name__ == "__main__":
    asyncio.run(main())
