"""고속 그리드 서치 — 캔들 메모리 캐싱 + 파라미터 직접 교체.

config.yaml 수정 없이 TradingConfig를 직접 replace하여 백테스트.
데이터 로드 1회 → 그리드 값별 시뮬레이션만 반복.

사용법:
    python scripts/grid_search_fast.py
    python scripts/grid_search_fast.py --param trailing_stop_pct --values 0.005,0.007,0.010,0.015,0.020
    python scripts/grid_search_fast.py --param momentum_stop_loss_pct --values -0.020,-0.025,-0.030,-0.035
    python scripts/grid_search_fast.py --param tp1_pct --values 0.10,0.12,0.15,0.18,0.20
"""
import argparse
import asyncio
import sys
from dataclasses import replace
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from loguru import logger

from backtest.backtester import Backtester, BacktestConfig
from config.settings import AppConfig
from data.db_manager import DbManager
from strategy.momentum_strategy import MomentumStrategy

logger.remove()
logger.add(sys.stderr, level="WARNING")

DEFAULT_START = "2025-05-07"
DEFAULT_END = "2026-04-10"
DEFAULT_PARAM = "trailing_stop_pct"
DEFAULT_VALUES = [0.005, 0.007, 0.010, 0.015, 0.020]


async def run_for_value(bt, candles_cache, stocks, trading_config, param_name, value):
    """단일 파라미터 값으로 전종목 백테스트."""
    new_config = replace(trading_config, **{param_name: value})

    total_trades = 0
    total_pnl = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    pf_above_1 = 0

    for stock in stocks:
        ticker = stock["ticker"]
        if ticker not in candles_cache:
            continue

        strategy = MomentumStrategy(new_config)
        kpi = await bt.run_multi_day_cached(ticker, candles_cache[ticker], strategy)

        total_trades += kpi["total_trades"]
        total_pnl += kpi["total_pnl"]
        for t in kpi.get("trades", []):
            if t["pnl"] > 0:
                gross_profit += t["pnl"]
            else:
                gross_loss += abs(t["pnl"])
        if kpi["profit_factor"] > 1.0 and kpi["total_trades"] > 0:
            pf_above_1 += 1

    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    return {
        "trades": total_trades,
        "pnl": total_pnl,
        "pf": pf,
        "pf_above_1": pf_above_1,
    }


async def main():
    parser = argparse.ArgumentParser(description="Fast grid search with cached candles")
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

    # 데이터 1회 로드
    print(f"Loading candles ({len(stocks)} stocks)...")
    candles_cache = {}
    for stock in stocks:
        ticker = stock["ticker"]
        candles = await bt.load_candles(ticker, args.start, f"{args.end} 23:59:59")
        if not candles.empty:
            candles_cache[ticker] = candles
    print(f"  Loaded: {len(candles_cache)} stocks")

    # 현재값 표시
    current_val = getattr(trading_config, args.param, "N/A")
    print()
    print("=" * 70)
    print(f"Fast Grid Search: {args.param}")
    print(f"Period: {args.start} ~ {args.end}")
    print(f"Current: {current_val}")
    print(f"Grid: {grid}")
    print("=" * 70)

    results = []
    for i, value in enumerate(grid, 1):
        print(f"[{i}/{len(grid)}] {args.param}={value} ...", end=" ", flush=True)
        r = await run_for_value(bt, candles_cache, stocks, trading_config, args.param, value)
        r["value"] = value
        results.append(r)
        print(f"trades={r['trades']}, PF={r['pf']:.2f}, PnL={r['pnl']:+,.0f}, PF>1={r['pf_above_1']}")

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

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
