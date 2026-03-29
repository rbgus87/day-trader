"""backtest/run_all_strategies.py — 4개 전략 일괄 백테스트.

사용법:
    python -m backtest.run_all_strategies --ticker 005930
"""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv
load_dotenv()

from loguru import logger

from backtest.backtester import Backtester
from config.settings import AppConfig
from data.db_manager import DbManager
from strategy.orb_strategy import OrbStrategy
from strategy.vwap_strategy import VwapStrategy
from strategy.momentum_strategy import MomentumStrategy
from strategy.pullback_strategy import PullbackStrategy


async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="005930")
    parser.add_argument("--start", default="2026-02-05")
    parser.add_argument("--end", default="2026-03-23")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    config = AppConfig.from_yaml()
    db = DbManager(config.db_path)
    await db.init()

    # config.yaml backtest 섹션 로드
    import yaml
    from pathlib import Path
    _cfg = yaml.safe_load(open(Path("config.yaml"), encoding="utf-8")) or {}
    bt_cfg = _cfg.get("backtest", {})

    bt = Backtester(
        db=db, config=config.trading,
        commission=bt_cfg.get("commission"),
        tax=bt_cfg.get("tax"),
        slippage=bt_cfg.get("slippage"),
    )

    strategies = {
        "ORB": OrbStrategy(config.trading, min_range_pct=config.trading.orb_min_range_pct),
        "VWAP": VwapStrategy(config.trading),
        "Momentum": MomentumStrategy(config.trading),
        "Pullback": PullbackStrategy(config.trading),
    }

    print("=" * 80)
    print(f"  4-Strategy Backtest - {args.ticker}")
    print(f"  Period: {args.start} ~ {args.end}")
    print()
    print(f"  Cost Model:")
    print(f"    Commission: {bt._entry_fee*100:.3f}% (buy+sell)")
    print(f"    Tax:        {bt._tax*100:.2f}% (sell only)")
    print(f"    Slippage:   {bt._slippage*100:.3f}% (each side)")
    print(f"    Total cost: ~{(bt._entry_fee + bt._exit_fee + bt._tax + bt._slippage*2)*100:.3f}% per round-trip")
    print()
    print(f"  Strategy Params:")
    print(f"    ORB: TP={config.trading.tp1_pct*100:.1f}% SL={config.trading.orb_stop_loss_pct*100:.1f}% MinRange={config.trading.orb_min_range_pct*100:.2f}%")
    print(f"    VWAP: TP={config.trading.tp1_pct*100:.1f}% SL={config.trading.vwap_stop_loss_pct*100:.1f}% RSI=[{config.trading.vwap_rsi_low:.0f},{config.trading.vwap_rsi_high:.0f}]")
    print(f"    Momentum: TP={config.trading.tp1_pct*100:.1f}% SL=-1.5% VolRatio={config.trading.momentum_volume_ratio:.1f}")
    print(f"    Pullback: TP={config.trading.tp1_pct*100:.1f}% SL={config.trading.pullback_stop_loss_pct*100:.1f}% MinGain={config.trading.pullback_min_gain_pct*100:.1f}%")
    print("=" * 80)

    results = {}
    for name, strategy in strategies.items():
        kpi = await bt.run_multi_day(args.ticker, args.start, args.end, strategy)
        results[name] = kpi

    # Summary table
    print()
    print(f"{'Strategy':<12} | {'Trades':>6} {'Wins':>5} {'WinR':>6} {'PF':>6} "
          f"{'PnL':>12} {'MDD':>10} {'Sharpe':>7}")
    print("-" * 80)

    for name, kpi in results.items():
        pf = kpi["profit_factor"]
        pf_str = f"{pf:6.2f}" if pf < 100 else "   INF"
        print(
            f"{name:<12} | {kpi['total_trades']:>6} {kpi['wins']:>5} "
            f"{kpi['win_rate']:>5.1%} {pf_str} "
            f"{kpi['total_pnl']:>+12,.0f} {kpi['max_drawdown']:>10,.0f} "
            f"{kpi['sharpe_ratio']:>7.2f}"
        )

    # Trade details per strategy
    for name, kpi in results.items():
        trades = kpi.get("trades", [])
        if not trades:
            continue
        print()
        print(f"  --- {name} Trades ---")
        for i, t in enumerate(trades, 1):
            ets = t['entry_ts'].strftime('%m-%d %H:%M') if hasattr(t['entry_ts'], 'strftime') else str(t['entry_ts'])
            xts = t['exit_ts'].strftime('%m-%d %H:%M') if hasattr(t['exit_ts'], 'strftime') else str(t['exit_ts'])
            w = 'W' if t['pnl'] > 0 else 'L' if t['pnl'] < 0 else '-'
            print(f"    #{i:2d} [{w}] {ets} -> {xts} | PnL:{t['pnl']:>+9,.0f} ({t['pnl_pct']:+.2%}) | {t['exit_reason']}")

    print("=" * 80)
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
