"""backtest/extract_profitable.py — Momentum PF>1.0 종목 추출 → universe_filtered.yaml 생성.

사용법:
    python -m backtest.extract_profitable
    python -m backtest.extract_profitable --start 2025-10-17 --end 2026-04-07 --min-pf 1.0 --min-trades 5
"""

import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv
load_dotenv()

import yaml
from loguru import logger

from backtest.backtester import Backtester
from config.settings import AppConfig, BacktestConfig
from data.db_manager import DbManager
from strategy.momentum_strategy import MomentumStrategy


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-10-17")
    parser.add_argument("--end", default="2026-04-07")
    parser.add_argument("--min-pf", type=float, default=1.0)
    parser.add_argument("--min-trades", type=int, default=5)
    parser.add_argument("--output", default="config/universe_filtered.yaml")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    app_config = AppConfig.from_yaml()
    trading_config = app_config.trading

    bt_cfg = yaml.safe_load(open(Path("config.yaml"), encoding="utf-8")).get("backtest", {})
    backtest_config = BacktestConfig(
        commission=bt_cfg.get("commission", 0.00015),
        tax=bt_cfg.get("tax", 0.0018),
        slippage=bt_cfg.get("slippage", 0.0003),
    )

    uni = yaml.safe_load(open("config/universe.yaml", encoding="utf-8")) or {}
    stocks = uni.get("stocks", [])

    db = DbManager("daytrader.db")
    await db.init()

    bt = Backtester(
        db=db, config=trading_config,
        backtest_config=backtest_config,
    )

    print(f"종목별 Momentum 백테스트: {args.start} ~ {args.end}")
    print(f"필터: PF >= {args.min_pf}, 거래수 >= {args.min_trades}")
    print("=" * 80)

    filtered = []
    for stock in stocks:
        ticker = stock["ticker"]
        name = stock["name"]

        candles = await bt.load_candles(ticker, args.start, f"{args.end} 23:59:59")
        if candles.empty:
            continue

        strategy = MomentumStrategy(trading_config)
        kpi = await bt.run_multi_day(ticker, args.start, args.end, strategy)

        trades = kpi["total_trades"]
        pf = kpi["profit_factor"]
        pnl = kpi["total_pnl"]

        status = ""
        if trades >= args.min_trades and pf >= args.min_pf:
            filtered.append({
                "ticker": ticker,
                "name": name,
                "pf": pf,
                "pnl": pnl,
                "trades": trades,
            })
            status = " >>> selected"

        pf_str = f"{pf:5.2f}" if pf < 100 else "  INF"
        print(f"  {name:<20} {ticker:<8} trades {trades:>4}  PF {pf_str}  PnL {pnl:>+10,.0f}{status}")

    print("=" * 80)
    print(f"selected: {len(filtered)}/{len(stocks)}")

    # PF 내림차순 정렬
    filtered.sort(key=lambda x: x["pf"], reverse=True)

    print()
    print("Top 10:")
    for i, s in enumerate(filtered[:10], 1):
        print(f"  {i:>2}. {s['name']:<20} PF {s['pf']:.2f}  PnL {s['pnl']:+,.0f}  trades {s['trades']}")

    # universe YAML 저장
    output_stocks = [
        {"ticker": s["ticker"], "name": s["name"]}
        for s in filtered
    ]

    output_data = {
        "meta": {
            "source": "backtest_filtered",
            "period": f"{args.start} ~ {args.end}",
            "criteria": f"PF>={args.min_pf}, trades>={args.min_trades}",
            "count": len(output_stocks),
        },
        "stocks": output_stocks,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(output_data, f, allow_unicode=True, sort_keys=False)

    print()
    print(f"saved: {output_path}")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
