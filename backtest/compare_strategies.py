"""backtest/compare_strategies.py — 5전략 비교 배치 백테스트.

사용법:
    python -m backtest.compare_strategies
    python -m backtest.compare_strategies --start 2026-01-01 --end 2026-03-23
"""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv
load_dotenv()

import yaml
from pathlib import Path
from loguru import logger

from backtest.backtester import Backtester
from config.settings import TradingConfig, BacktestConfig
from data.db_manager import DbManager
from strategy.momentum_strategy import MomentumStrategy
from strategy.pullback_strategy import PullbackStrategy
from strategy.gap_strategy import GapStrategy
from strategy.open_break_strategy import OpenBreakStrategy
from strategy.big_candle_strategy import BigCandleStrategy
from strategy.flow_strategy import FlowStrategy


STRATEGY_NAME_MAP = {
    "momentum": "Momentum",
    "pullback": "Pullback",
    "flow": "Flow",
    "gap": "Gap",
    "open_break": "OpenBreak",
    "big_candle": "BigCandle",
}


async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end", default="2026-03-23")
    parser.add_argument(
        "--strategy", default="all",
        help="단일 전략만 테스트 (momentum/pullback/flow/gap/open_break/big_candle/all)",
    )
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    # config 로드
    _cfg = yaml.safe_load(open(Path("config.yaml"), encoding="utf-8")) or {}
    bt_cfg = _cfg.get("backtest", {})
    backtest_config = BacktestConfig(
        commission=bt_cfg.get("commission", 0.00015),
        tax=bt_cfg.get("tax", 0.0018),
        slippage=bt_cfg.get("slippage", 0.0003),
    )
    from config.settings import AppConfig
    trading_config = AppConfig.from_yaml().trading

    # universe 로드
    uni_path = Path("config/universe.yaml")
    uni = yaml.safe_load(open(uni_path, encoding="utf-8")) or {}
    stocks = uni.get("stocks", [])

    db = DbManager("daytrader.db")
    await db.init()

    bt = Backtester(
        db=db, config=trading_config,
        backtest_config=backtest_config,
    )

    # 전략 필터링
    all_strategy_names = ["Momentum", "Pullback", "Flow", "Gap", "OpenBreak", "BigCandle"]
    if args.strategy == "all":
        STRATEGY_KEYS = all_strategy_names
    else:
        target = STRATEGY_NAME_MAP.get(args.strategy.lower())
        if not target:
            print(f"Unknown strategy: {args.strategy}")
            print(f"Available: {', '.join(STRATEGY_NAME_MAP.keys())}")
            return
        STRATEGY_KEYS = [target]
    SHORT_KEYS = [s[:3].upper() for s in STRATEGY_KEYS]

    n_strat = len(STRATEGY_KEYS)
    label = f"{n_strat}-Strategy Comparison" if n_strat > 1 else f"{STRATEGY_KEYS[0]} Single"

    print("=" * 120)
    print(f"  {label} Backtest")
    print(f"  Period: {args.start} ~ {args.end}")
    print(f"  Universe: {len(stocks)} stocks")
    print(f"  Cost: commission={backtest_config.commission*100:.3f}% tax={backtest_config.tax*100:.2f}% slippage={backtest_config.slippage*100:.3f}%")
    print("=" * 120)

    # 헤더
    header = f"{'종목':<20}"
    for sk in SHORT_KEYS:
        header += f" | {sk}_거래 {sk}_PF {sk}_PnL"
    print(header)
    print("-" * 120)

    # 전략별 합산
    totals = {k: {"trades": 0, "pnl": 0.0, "gross_profit": 0.0, "gross_loss": 0.0, "pf_above_1": 0}
              for k in STRATEGY_KEYS}

    tested_count = 0

    for stock in stocks:
        ticker = stock["ticker"]
        name = stock["name"]

        # 데이터 존재 확인
        candles = await bt.load_candles(ticker, args.start, f"{args.end} 23:59:59")
        if candles.empty:
            continue

        tested_count += 1
        row = f"{name}({ticker})"
        row = f"{row:<20}"

        all_strategies = {
            "Momentum": MomentumStrategy(trading_config),
            "Pullback": PullbackStrategy(trading_config),
            "Flow": FlowStrategy(trading_config),
            "Gap": GapStrategy(trading_config),
            "OpenBreak": OpenBreakStrategy(trading_config),
            "BigCandle": BigCandleStrategy(trading_config),
        }
        strategies = {k: all_strategies[k] for k in STRATEGY_KEYS}

        for sname, strategy in strategies.items():
            kpi = await bt.run_multi_day(ticker, args.start, args.end, strategy)
            t_count = kpi["total_trades"]
            pf = kpi["profit_factor"]
            pnl = kpi["total_pnl"]

            sk = SHORT_KEYS[STRATEGY_KEYS.index(sname)]
            pf_str = f"{pf:5.2f}" if pf < 100 else "  INF"
            row += f" | {t_count:>4} {pf_str} {pnl:>+8,.0f}"

            totals[sname]["trades"] += t_count
            totals[sname]["pnl"] += pnl
            # gross profit/loss 재계산
            for trade in kpi.get("trades", []):
                if trade["pnl"] > 0:
                    totals[sname]["gross_profit"] += trade["pnl"]
                else:
                    totals[sname]["gross_loss"] += abs(trade["pnl"])
            if pf > 1.0 and t_count > 0:
                totals[sname]["pf_above_1"] += 1

        print(row)

    # 합계
    print("-" * 120)
    row = f"{'합계':<20}"
    for sname in STRATEGY_KEYS:
        t = totals[sname]
        sk = SHORT_KEYS[STRATEGY_KEYS.index(sname)]
        pf = t["gross_profit"] / t["gross_loss"] if t["gross_loss"] > 0 else float("inf")
        pf_str = f"{pf:5.2f}" if pf < 100 else "  INF"
        row += f" | {t['trades']:>4} {pf_str} {t['pnl']:>+8,.0f}"
    print(row)

    # 순위
    print()
    print("=" * 120)
    print(f"  테스트 종목: {tested_count}/{len(stocks)}")
    print()
    print(f"  {'전략':<12} | {'거래':>5} | {'PF':>6} | {'총PnL':>12} | {'PF>1.0 종목':>10}")
    print("-" * 60)

    ranked = sorted(STRATEGY_KEYS, key=lambda k: totals[k]["pnl"], reverse=True)
    for sname in ranked:
        t = totals[sname]
        pf = t["gross_profit"] / t["gross_loss"] if t["gross_loss"] > 0 else float("inf")
        pf_str = f"{pf:6.2f}" if pf < 100 else "   INF"
        print(f"  {sname:<12} | {t['trades']:>5} | {pf_str} | {t['pnl']:>+12,.0f} | {t['pf_above_1']:>10}")

    print("=" * 120)
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
