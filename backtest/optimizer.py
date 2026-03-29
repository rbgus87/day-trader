"""backtest/optimizer.py — ORB 전략 파라미터 그리드 서치 옵티마이저.

사용법:
    python -m backtest.optimizer --ticker 005930
"""

from __future__ import annotations

import asyncio
import itertools
import sys
from dataclasses import dataclass

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from loguru import logger

from backtest.backtester import Backtester
from config.settings import AppConfig, TradingConfig
from data.db_manager import DbManager
from strategy.orb_strategy import OrbStrategy


@dataclass
class ParamSet:
    """탐색할 파라미터 조합."""
    tp1_pct: float
    stop_loss_pct: float
    orb_volume_ratio: float
    min_range_pct: float

    def label(self) -> str:
        return (
            f"TP={self.tp1_pct*100:.1f}% "
            f"SL={self.stop_loss_pct*100:.1f}% "
            f"VR={self.orb_volume_ratio:.1f} "
            f"MR={self.min_range_pct*100:.2f}%"
        )


@dataclass
class OptResult:
    """최적화 결과."""
    params: ParamSet
    total_trades: int
    wins: int
    win_rate: float
    profit_factor: float
    total_pnl: float
    max_drawdown: float
    sharpe_ratio: float


def generate_param_grid() -> list[ParamSet]:
    """파라미터 그리드를 생성한다."""
    tp1_values = [0.015, 0.020, 0.025, 0.030, 0.035]
    sl_values = [-0.010, -0.012, -0.015, -0.018, -0.020]
    vr_values = [1.0, 1.5, 2.0]
    mr_values = [0.0, 0.003, 0.005, 0.008, 0.010]

    grid = []
    for tp1, sl, vr, mr in itertools.product(tp1_values, sl_values, vr_values, mr_values):
        grid.append(ParamSet(
            tp1_pct=tp1,
            stop_loss_pct=sl,
            orb_volume_ratio=vr,
            min_range_pct=mr,
        ))
    return grid


async def run_optimization(
    ticker: str,
    start_date: str,
    end_date: str,
    db: DbManager,
) -> list[OptResult]:
    """전체 파라미터 그리드에 대해 백테스트를 실행한다."""
    grid = generate_param_grid()
    results: list[OptResult] = []
    total = len(grid)

    logger.info(f"Parameter optimization: {total} combinations for {ticker}")

    for idx, ps in enumerate(grid, 1):
        # 파라미터 적용
        config = TradingConfig(
            tp1_pct=ps.tp1_pct,
            orb_stop_loss_pct=ps.stop_loss_pct,
            orb_volume_ratio=ps.orb_volume_ratio,
        )
        strategy = OrbStrategy(config, min_range_pct=ps.min_range_pct)
        bt = Backtester(db=db, config=config)

        kpi = await bt.run_multi_day(ticker, start_date, end_date, strategy)

        result = OptResult(
            params=ps,
            total_trades=kpi["total_trades"],
            wins=kpi["wins"],
            win_rate=kpi["win_rate"],
            profit_factor=kpi["profit_factor"],
            total_pnl=kpi["total_pnl"],
            max_drawdown=kpi["max_drawdown"],
            sharpe_ratio=kpi["sharpe_ratio"],
        )
        results.append(result)

        if idx % 50 == 0 or idx == total:
            logger.info(f"Progress: {idx}/{total}")

    return results


def rank_results(results: list[OptResult], min_trades: int = 5) -> list[OptResult]:
    """결과를 Profit Factor 기준 내림차순 정렬, 최소 거래수 필터."""
    filtered = [r for r in results if r.total_trades >= min_trades]
    return sorted(filtered, key=lambda r: r.profit_factor, reverse=True)


def print_results(ranked: list[OptResult], top_n: int = 20) -> None:
    """상위 N개 결과 출력."""
    print()
    print(f"{'Rank':>4} | {'TP%':>5} {'SL%':>6} {'VR':>4} {'MR%':>6} | "
          f"{'Trades':>6} {'Wins':>4} {'WinR':>6} {'PF':>6} {'PnL':>10} {'MDD':>10} {'Sharpe':>7}")
    print("-" * 95)

    for i, r in enumerate(ranked[:top_n], 1):
        pf_str = f"{r.profit_factor:6.2f}" if r.profit_factor < 100 else "   INF"
        print(
            f"{i:4d} | "
            f"{r.params.tp1_pct*100:5.1f} {r.params.stop_loss_pct*100:6.1f} "
            f"{r.params.orb_volume_ratio:4.1f} {r.params.min_range_pct*100:6.2f} | "
            f"{r.total_trades:6d} {r.wins:4d} {r.win_rate:5.1%} "
            f"{pf_str} {r.total_pnl:+10,.0f} {r.max_drawdown:10,.0f} {r.sharpe_ratio:7.2f}"
        )


async def main() -> None:
    import argparse
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description="ORB parameter optimizer")
    parser.add_argument("--ticker", default="005930")
    parser.add_argument("--start", default="2026-02-05")
    parser.add_argument("--end", default="2026-03-23")
    args = parser.parse_args()

    # 로깅 최소화 (백테스트 로그 제거)
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    config = AppConfig.from_yaml()
    db = DbManager(config.db_path)
    await db.init()

    try:
        results = await run_optimization(args.ticker, args.start, args.end, db)
        ranked = rank_results(results, min_trades=5)

        print("=" * 95)
        print(f"  ORB Parameter Optimization - {args.ticker}")
        print(f"  Period: {args.start} ~ {args.end}")
        print(f"  Grid: {len(results)} combinations, {len(ranked)} with 5+ trades")
        print("=" * 95)

        print_results(ranked, top_n=20)

        if ranked:
            best = ranked[0]
            print()
            print(f"  BEST: {best.params.label()}")
            print(f"  -> WinRate={best.win_rate:.1%} PF={best.profit_factor:.2f} "
                  f"PnL={best.total_pnl:+,.0f} Trades={best.total_trades}")
        print("=" * 95)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
