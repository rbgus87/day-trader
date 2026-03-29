"""backtest/optimize_all.py — 4전략 파라미터 그리드 서치 옵티마이저.

사용법:
    python -m backtest.optimize_all
    python -m backtest.optimize_all --strategy orb --ticker 196170
    python -m backtest.optimize_all --strategy all --tickers 196170,247540,005380
"""

from __future__ import annotations

import asyncio
import itertools
import sys
from dataclasses import dataclass, field

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from loguru import logger

from backtest.backtester import Backtester
from config.settings import TradingConfig
from data.db_manager import DbManager
from strategy.orb_strategy import OrbStrategy
from strategy.vwap_strategy import VwapStrategy
from strategy.momentum_strategy import MomentumStrategy
from strategy.pullback_strategy import PullbackStrategy


# =========================================================================
# 파라미터 그리드 정의
# =========================================================================

ORB_GRID = {
    "tp1_pct":       [0.015, 0.020, 0.025, 0.030],
    "stop_loss_pct": [-0.008, -0.010, -0.012, -0.015],
    "min_range_pct": [0.003, 0.005, 0.008],
}

VWAP_GRID = {
    "tp1_pct":       [0.015, 0.020, 0.025, 0.030],
    "stop_loss_pct": [-0.008, -0.010, -0.012, -0.015],
    "rsi_low":       [30.0, 35.0, 40.0],
    "rsi_high":      [60.0, 65.0, 70.0],
}

MOMENTUM_GRID = {
    "tp1_pct":       [0.015, 0.020, 0.025, 0.030],
    "stop_loss_pct": [-0.008, -0.010, -0.012, -0.015],
    "volume_ratio":  [1.2, 1.5, 2.0],
}

PULLBACK_GRID = {
    "tp1_pct":       [0.015, 0.020, 0.025, 0.030],
    "stop_loss_pct": [-0.010, -0.012, -0.015, -0.018],
    "min_gain_pct":  [0.02, 0.03, 0.04],
}


@dataclass
class OptResult:
    """최적화 결과."""
    strategy: str
    params: dict
    total_trades: int
    wins: int
    win_rate: float
    profit_factor: float
    total_pnl: float
    max_drawdown: float
    sharpe_ratio: float
    tickers_tested: list[str] = field(default_factory=list)

    def param_label(self) -> str:
        parts = []
        for k, v in self.params.items():
            if isinstance(v, float):
                if abs(v) < 1:
                    parts.append(f"{k}={v*100:.1f}%")
                else:
                    parts.append(f"{k}={v:.1f}")
            else:
                parts.append(f"{k}={v}")
        return " ".join(parts)


def _make_grid(grid_def: dict) -> list[dict]:
    """파라미터 dict의 모든 조합을 생성한다."""
    keys = list(grid_def.keys())
    values = list(grid_def.values())
    combos = []
    for combo in itertools.product(*values):
        combos.append(dict(zip(keys, combo)))
    return combos


def _make_strategy(name: str, params: dict, base_config: TradingConfig):
    """전략명과 파라미터로 (TradingConfig, Strategy) 쌍을 생성한다."""
    if name == "orb":
        cfg = TradingConfig(
            tp1_pct=params["tp1_pct"],
            orb_stop_loss_pct=params["stop_loss_pct"],
            orb_min_range_pct=params["min_range_pct"],
        )
        return cfg, OrbStrategy(cfg, min_range_pct=params["min_range_pct"])

    elif name == "vwap":
        cfg = TradingConfig(
            tp1_pct=params["tp1_pct"],
            vwap_stop_loss_pct=params["stop_loss_pct"],
            vwap_rsi_low=params["rsi_low"],
            vwap_rsi_high=params["rsi_high"],
        )
        return cfg, VwapStrategy(cfg)

    elif name == "momentum":
        cfg = TradingConfig(
            tp1_pct=params["tp1_pct"],
            stop_loss_pct=params["stop_loss_pct"],
            momentum_volume_ratio=params["volume_ratio"],
        )
        return cfg, MomentumStrategy(cfg)

    elif name == "pullback":
        cfg = TradingConfig(
            tp1_pct=params["tp1_pct"],
            pullback_stop_loss_pct=params["stop_loss_pct"],
            pullback_min_gain_pct=params["min_gain_pct"],
        )
        return cfg, PullbackStrategy(cfg)

    raise ValueError(f"Unknown strategy: {name}")


async def optimize_strategy(
    strategy_name: str,
    tickers: list[str],
    start_date: str,
    end_date: str,
    db: DbManager,
    min_trades: int = 5,
) -> list[OptResult]:
    """단일 전략의 파라미터 그리드 서치를 다중 종목에서 실행한다."""
    grids = {
        "orb": ORB_GRID,
        "vwap": VWAP_GRID,
        "momentum": MOMENTUM_GRID,
        "pullback": PULLBACK_GRID,
    }

    grid_def = grids[strategy_name]
    combos = _make_grid(grid_def)
    total = len(combos)
    base_config = TradingConfig()

    print(f"\n  [{strategy_name.upper()}] {total} combinations x {len(tickers)} tickers = {total * len(tickers)} backtests")

    results: list[OptResult] = []

    for idx, params in enumerate(combos, 1):
        # 모든 종목에서 집계
        agg_trades = 0
        agg_wins = 0
        agg_pnl = 0.0
        agg_gross_profit = 0.0
        agg_gross_loss = 0.0
        agg_mdd = 0.0
        all_trade_pnls: list[float] = []

        for ticker in tickers:
            cfg, strat = _make_strategy(strategy_name, params, base_config)
            bt = Backtester(db=db, config=cfg)
            kpi = await bt.run_multi_day(ticker, start_date, end_date, strat)

            agg_trades += kpi["total_trades"]
            agg_wins += kpi["wins"]
            agg_pnl += kpi["total_pnl"]
            agg_mdd = max(agg_mdd, kpi["max_drawdown"])

            for t in kpi.get("trades", []):
                p = t.get("pnl", 0)
                if p > 0:
                    agg_gross_profit += p
                else:
                    agg_gross_loss += abs(p)
                all_trade_pnls.append(p)

        win_rate = agg_wins / agg_trades if agg_trades > 0 else 0.0
        pf = agg_gross_profit / agg_gross_loss if agg_gross_loss > 0 else float("inf")

        # Sharpe
        import numpy as np
        if len(all_trade_pnls) >= 2:
            arr = np.array(all_trade_pnls)
            mean = arr.mean()
            std = arr.std(ddof=1)
            sharpe = (mean / std * (252 ** 0.5)) if std > 0 else 0.0
        else:
            sharpe = 0.0

        results.append(OptResult(
            strategy=strategy_name,
            params=params,
            total_trades=agg_trades,
            wins=agg_wins,
            win_rate=win_rate,
            profit_factor=round(pf, 2),
            total_pnl=round(agg_pnl),
            max_drawdown=round(agg_mdd),
            sharpe_ratio=round(sharpe, 2),
            tickers_tested=tickers,
        ))

        if idx % 20 == 0 or idx == total:
            print(f"    Progress: {idx}/{total}")

    # 필터 + 정렬
    filtered = [r for r in results if r.total_trades >= min_trades]
    ranked = sorted(filtered, key=lambda r: r.profit_factor, reverse=True)
    return ranked


def print_strategy_results(ranked: list[OptResult], top_n: int = 10) -> None:
    """전략별 상위 N개 결과 출력."""
    if not ranked:
        print("    No results with enough trades.")
        return

    strat = ranked[0].strategy.upper()
    keys = list(ranked[0].params.keys())

    # 헤더
    param_header = " ".join(f"{k:>8}" for k in keys)
    print(f"\n  {'Rank':>4} | {param_header} | {'Trades':>6} {'Wins':>4} {'WinR':>6} {'PF':>6} {'PnL':>12} {'MDD':>10} {'Sharpe':>7}")
    print("  " + "-" * (12 + 9 * len(keys) + 60))

    for i, r in enumerate(ranked[:top_n], 1):
        param_vals = " ".join(
            f"{r.params[k]*100:>7.1f}%" if isinstance(r.params[k], float) and abs(r.params[k]) < 1
            else f"{r.params[k]:>8.1f}"
            for k in keys
        )
        pf_str = f"{r.profit_factor:6.2f}" if r.profit_factor < 100 else "   INF"
        print(
            f"  {i:4d} | {param_vals} | "
            f"{r.total_trades:6d} {r.wins:4d} {r.win_rate:5.1%} "
            f"{pf_str} {r.total_pnl:+12,.0f} {r.max_drawdown:10,.0f} {r.sharpe_ratio:7.2f}"
        )


async def main() -> None:
    import argparse
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description="4-Strategy parameter optimizer")
    parser.add_argument("--strategy", default="all", help="orb|vwap|momentum|pullback|all")
    parser.add_argument("--tickers", default="196170,247540,005380,068270,042700,028300",
                        help="Comma-separated ticker list")
    parser.add_argument("--start", default="2025-12-30")
    parser.add_argument("--end", default="2026-03-24")
    parser.add_argument("--min-trades", type=int, default=10)
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="ERROR")

    db = DbManager("daytrader.db")
    await db.init()

    tickers = args.tickers.split(",")
    strategies = ["orb", "vwap", "momentum", "pullback"] if args.strategy == "all" else [args.strategy]

    print("=" * 100)
    print(f"  4-Strategy Parameter Optimizer")
    print(f"  Period: {args.start} ~ {args.end}")
    print(f"  Tickers: {', '.join(tickers)}")
    print(f"  Min trades: {args.min_trades}")
    print("=" * 100)

    best_params = {}

    for strat_name in strategies:
        try:
            ranked = await optimize_strategy(
                strat_name, tickers, args.start, args.end, db, args.min_trades,
            )
            print(f"\n  === {strat_name.upper()} TOP 10 ===")
            print_strategy_results(ranked, top_n=10)

            if ranked:
                best = ranked[0]
                best_params[strat_name] = best
                print(f"\n  BEST {strat_name.upper()}: {best.param_label()}")
                print(f"  -> Trades={best.total_trades} WinR={best.win_rate:.1%} "
                      f"PF={best.profit_factor:.2f} PnL={best.total_pnl:+,.0f} Sharpe={best.sharpe_ratio:.2f}")
        except Exception as e:
            print(f"\n  [{strat_name.upper()}] ERROR: {e}")

    # 최종 요약
    print("\n" + "=" * 100)
    print("  OPTIMIZATION SUMMARY — Best Parameters")
    print("=" * 100)
    for strat_name, best in best_params.items():
        print(f"  {strat_name.upper():>10}: {best.param_label()}")
        print(f"             Trades={best.total_trades} WinR={best.win_rate:.1%} "
              f"PF={best.profit_factor:.2f} PnL={best.total_pnl:+,.0f}")
    print("=" * 100)

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
