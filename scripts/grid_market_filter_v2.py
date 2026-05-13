"""scripts/grid_market_filter_v2.py — 시장 필터 ON/OFF 그리드.

baseline 로직(BE3 + limit_up_exit) 기반. KOSPI/KOSDAQ 분리 통계 포함.
사용:
    python scripts/grid_market_filter_v2.py --start 2025-04-01 --end 2026-04-10
"""

import argparse
import asyncio
import os
import pickle
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import yaml
from loguru import logger

logger.remove()

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig
from data.db_manager import DbManager

SCENARIOS = [
    ("MF_ON",  "시장 필터 ON (현행)",  True),
    ("MF_OFF", "시장 필터 OFF",        False),
]


def _simulate_one(args):
    ticker, ticker_market, candles_pickle, trading_config, backtest_config, market_map = args
    from loguru import logger as _lg
    _lg.remove()
    import asyncio as _asyncio
    from backtest.backtester import Backtester as _Bt
    from strategy.momentum_strategy import MomentumStrategy

    candles = pickle.loads(candles_pickle)
    strategy = MomentumStrategy(trading_config)
    bt = _Bt(
        db=None,
        config=trading_config,
        backtest_config=backtest_config,
        ticker_market=ticker_market,
        market_strong_by_date=market_map,
    )
    result = _asyncio.run(bt.run_multi_day_cached(ticker, candles, strategy))
    for t in result.get("trades", []):
        t["ticker"] = ticker
        t["ticker_market"] = ticker_market
    return result


async def collect_trades(start, end, mf_enabled):
    app_config = AppConfig.from_yaml()
    base_config = app_config.trading

    bt_raw = yaml.safe_load(open("config.yaml", encoding="utf-8")).get("backtest", {})
    backtest_config = BacktestConfig(
        commission=bt_raw.get("commission", 0.00015),
        tax=bt_raw.get("tax", 0.0018),
        slippage=bt_raw.get("slippage", 0.0003),
    )

    overridden = replace(base_config, market_filter_enabled=mf_enabled)

    uni = yaml.safe_load(open("config/universe_backtest.yaml", encoding="utf-8"))
    stocks = uni.get("stocks", [])
    ticker_to_market = {s["ticker"]: s.get("market", "unknown") for s in stocks}

    db = DbManager(app_config.db_path)
    await db.init()
    loader = Backtester(db=db, config=overridden, backtest_config=backtest_config)
    candles_cache = {}
    for s in stocks:
        tk = s["ticker"]
        c = await loader.load_candles(tk, start, f"{end} 23:59:59")
        if not c.empty:
            candles_cache[tk] = pickle.dumps(c)
    await db.close()

    market_map = build_market_strong_by_date(app_config.db_path, ma_length=overridden.market_ma_length)
    workers = max(2, (os.cpu_count() or 2) - 1)
    tasks = [
        (tk, ticker_to_market.get(tk, "unknown"), candles_cache[tk], overridden, backtest_config, market_map)
        for tk in candles_cache
    ]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        kpis = list(ex.map(_simulate_one, tasks))

    trades = []
    for kpi in kpis:
        if kpi:
            trades.extend(kpi.get("trades", []))
    return trades


def stats(trades):
    n = len(trades)
    if n == 0:
        return None
    pnls = [t["pnl"] for t in trades]
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p < 0))
    pf = gp / gl if gl > 0 else float("inf")
    total = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    return {"n": n, "pf": pf, "total": total, "per": total / n, "win_rate": wins / n * 100}


def summarize(trades, label):
    s = stats(trades)
    if not s:
        print(f"\n=== {label} === NO TRADES")
        return
    print(f"\n=== {label} ===")
    print(f"  거래: {s['n']}건 / PF: {s['pf']:.2f} / 총 PnL: {s['total']:+,.0f} / 거래당: {s['per']:+,.0f} / 승률: {s['win_rate']:.1f}%")

    exit_dist = Counter(t.get("exit_reason", "?") for t in trades)
    print(f"  청산:")
    for r, c in exit_dist.most_common():
        pct = c / s['n'] * 100
        r_pnl = sum(t["pnl"] for t in trades if t.get("exit_reason") == r)
        print(f"    {r:<18} {c:>4}건 ({pct:>5.1f}%)  PnL {r_pnl:+,.0f}")

    print(f"  시장별:")
    for mkt in ["kospi", "kosdaq", "unknown"]:
        mtrades = [t for t in trades if t.get("ticker_market") == mkt]
        ms = stats(mtrades)
        if ms:
            print(f"    {mkt:<8} {ms['n']:>4}건  PF {ms['pf']:.2f}  PnL {ms['total']:+,.0f}  거래당 {ms['per']:+,.0f}  승률 {ms['win_rate']:.1f}%")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-04-01")
    parser.add_argument("--end", default="2026-04-10")
    args = parser.parse_args()

    print("=" * 70)
    print(f" Market Filter Grid (BE3 + limit_up_exit)  {args.start} ~ {args.end}")
    print("=" * 70)

    for name, desc, mf in SCENARIOS:
        print(f"\n[RUN] {name} {desc}")
        trades = await collect_trades(args.start, args.end, mf)
        summarize(trades, f"{name} {desc}")


if __name__ == "__main__":
    asyncio.run(main())
