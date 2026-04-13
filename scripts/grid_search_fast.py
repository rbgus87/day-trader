"""고속 그리드 서치 — 캔들 캐싱 + ProcessPoolExecutor 병렬화.

config.yaml 수정 없이 TradingConfig를 직접 replace하여 백테스트.
데이터 로드 1회 → pickle 직렬화 → 프로세스 풀에서 종목별 병렬 실행.

사용법:
    python scripts/grid_search_fast.py
    python scripts/grid_search_fast.py --param trailing_stop_pct --values 0.005,0.007,0.010,0.015,0.020
    python scripts/grid_search_fast.py --param momentum_stop_loss_pct --values -0.020,-0.025,-0.030,-0.035
    python scripts/grid_search_fast.py --param tp1_pct --values 0.10,0.12,0.15,0.18,0.20
"""
import argparse
import asyncio
import os
import pickle
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from loguru import logger

from backtest.backtester import Backtester, BacktestConfig
from config.settings import AppConfig
from data.db_manager import DbManager

logger.remove()
logger.add(sys.stderr, level="WARNING")

DEFAULT_START = "2025-04-01"
DEFAULT_END = "2026-04-10"
DEFAULT_PARAM = "trailing_stop_pct"
DEFAULT_VALUES = [0.005, 0.010, 0.015, 0.020, 0.025]


def _simulate_one_stock(args):
    """단일 종목 백테스트 (별도 프로세스에서 실행)."""
    ticker, candles_pickle, trading_config, backtest_config, param_name, value = args

    import asyncio as _aio
    import pickle as _pk
    from dataclasses import replace as _replace

    from backtest.backtester import Backtester as _BT
    from strategy.momentum_strategy import MomentumStrategy as _MS

    candles = _pk.loads(candles_pickle)
    new_config = _replace(trading_config, **{param_name: value})
    strategy = _MS(new_config)
    bt = _BT(db=None, config=new_config, backtest_config=backtest_config)
    kpi = _aio.run(bt.run_multi_day_cached(ticker, candles, strategy))
    return kpi


async def main():
    parser = argparse.ArgumentParser(description="Fast grid search with ProcessPoolExecutor")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--param", default=DEFAULT_PARAM, help="TradingConfig field name")
    parser.add_argument("--values", default=None, help="Comma-separated values (e.g. 0.005,0.010,0.015)")
    args = parser.parse_args()

    if args.values:
        grid = [float(v) for v in args.values.split(",")]
    else:
        grid = DEFAULT_VALUES

    app_config = AppConfig.from_yaml()
    trading_config = app_config.trading

    bt_cfg_raw = yaml.safe_load(open("config.yaml", encoding="utf-8")).get("backtest", {})
    backtest_config = BacktestConfig(
        commission=bt_cfg_raw.get("commission", 0.00015),
        tax=bt_cfg_raw.get("tax", 0.0018),
        slippage=bt_cfg_raw.get("slippage", 0.0003),
    )

    uni = yaml.safe_load(open("config/universe.yaml", encoding="utf-8"))
    stocks = uni.get("stocks", [])

    db = DbManager("daytrader.db")
    await db.init()
    bt = Backtester(db=db, config=trading_config, backtest_config=backtest_config)

    # 데이터 1회 로드 + pickle 직렬화
    t0 = time.time()
    print(f"Loading candles ({len(stocks)} stocks)...")
    candles_cache = {}
    for stock in stocks:
        ticker = stock["ticker"]
        candles = await bt.load_candles(ticker, args.start, f"{args.end} 23:59:59")
        if not candles.empty:
            candles_cache[ticker] = pickle.dumps(candles)
    await db.close()
    print(f"  Loaded: {len(candles_cache)} stocks ({time.time() - t0:.1f}s)")

    workers = max(2, os.cpu_count() - 1)
    current_val = getattr(trading_config, args.param, "N/A")
    print(f"  Workers: {workers}")
    print()
    print("=" * 70)
    print(f"Fast Grid Search: {args.param}")
    print(f"Period: {args.start} ~ {args.end}")
    print(f"Current: {current_val}")
    print(f"Grid: {grid}")
    print("=" * 70)

    results = []
    for i, value in enumerate(grid, 1):
        t1 = time.time()
        print(f"[{i}/{len(grid)}] {args.param}={value} ...", end=" ", flush=True)

        tasks = [
            (ticker, candles_cache[ticker], trading_config, backtest_config, args.param, value)
            for ticker in candles_cache
        ]

        with ProcessPoolExecutor(max_workers=workers) as executor:
            kpis = list(executor.map(_simulate_one_stock, tasks))

        total_trades = sum(k["total_trades"] for k in kpis if k)
        total_pnl = sum(k["total_pnl"] for k in kpis if k)
        gp = sum(t["pnl"] for k in kpis if k for t in k.get("trades", []) if t["pnl"] > 0)
        gl = sum(abs(t["pnl"]) for k in kpis if k for t in k.get("trades", []) if t["pnl"] < 0)
        pf = gp / gl if gl > 0 else float("inf")
        pf_above_1 = sum(1 for k in kpis if k and k["profit_factor"] > 1.0 and k["total_trades"] > 0)

        r = {"value": value, "trades": total_trades, "pnl": total_pnl, "pf": pf, "pf_above_1": pf_above_1}
        results.append(r)
        elapsed = time.time() - t1
        print(f"trades={r['trades']}, PF={r['pf']:.2f}, PnL={r['pnl']:+,.0f}, PF>1={r['pf_above_1']} ({elapsed:.1f}s)")

    # 결과 표
    print()
    print("=" * 70)
    print(f"{'Value':<12} {'Trades':>6} {'PF':>6} {'Total PnL':>12} {'PF>1.0':>8}")
    print("-" * 50)
    for r in results:
        pf_str = f"{r['pf']:.2f}" if r["pf"] < 100 else "INF"
        print(f"{r['value']:<12} {r['trades']:>6} {pf_str:>6} {r['pnl']:>+12,.0f} {r['pf_above_1']:>8}")

    best = max(results, key=lambda x: x["pnl"])
    print()
    print(f"Best: {args.param}={best['value']} (PF {best['pf']:.2f}, PnL {best['pnl']:+,.0f})")


if __name__ == "__main__":
    asyncio.run(main())
