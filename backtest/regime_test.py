"""backtest/regime_test.py — Momentum 수익 편향 검증 (대형주/기간 분할).

분석 1: 대형주 3종목 vs 나머지
분석 2: 상승장(2025-11~2026-01) vs 조정장(2026-02~2026-03)
분석 3: 종합 판정

실행: python -m backtest.regime_test
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


STRATEGY_NAMES = ["Momentum", "Pullback", "Flow", "Gap", "BigCandle", "OpenBreak"]
LARGE_CAP_TICKERS = {"005930", "000660", "068270"}


def _make_strategies(tc: TradingConfig) -> dict:
    return {
        "Momentum": MomentumStrategy(tc),
        "Pullback": PullbackStrategy(tc),
        "Flow": FlowStrategy(tc),
        "Gap": GapStrategy(tc),
        "BigCandle": BigCandleStrategy(tc),
        "OpenBreak": OpenBreakStrategy(tc),
    }


def _empty_totals() -> dict:
    return {
        k: {"trades": 0, "pnl": 0.0, "gross_profit": 0.0, "gross_loss": 0.0, "pf_above_1": 0, "tested": 0}
        for k in STRATEGY_NAMES
    }


def _calc_pf(t: dict) -> float:
    return t["gross_profit"] / t["gross_loss"] if t["gross_loss"] > 0 else float("inf")


def _pf_str(pf: float) -> str:
    return f"{pf:6.2f}" if pf < 100 else "   INF"


async def _run_group(
    bt: Backtester, tc: TradingConfig, stocks: list[dict],
    start: str, end: str, label: str,
) -> dict:
    """종목 그룹에 대해 6전략 백테스트 실행, totals dict 반환."""
    totals = _empty_totals()
    tested = 0

    for stock in stocks:
        ticker = stock["ticker"]
        candles = await bt.load_candles(ticker, start, f"{end} 23:59:59")
        if candles.empty:
            continue
        tested += 1

        strategies = _make_strategies(tc)
        for sname, strategy in strategies.items():
            kpi = await bt.run_multi_day(ticker, start, end, strategy)
            t_count = kpi["total_trades"]
            pf = kpi["profit_factor"]
            pnl = kpi["total_pnl"]

            totals[sname]["trades"] += t_count
            totals[sname]["pnl"] += pnl
            totals[sname]["tested"] = tested
            for trade in kpi.get("trades", []):
                if trade["pnl"] > 0:
                    totals[sname]["gross_profit"] += trade["pnl"]
                else:
                    totals[sname]["gross_loss"] += abs(trade["pnl"])
            if pf > 1.0 and t_count > 0:
                totals[sname]["pf_above_1"] += 1

    return totals


def _print_table(totals: dict, tested: int) -> None:
    print(f"  {'전략':<12} | {'거래':>5} | {'PF':>6} | {'총PnL':>12} | {'PF>1.0':>8}")
    print("  " + "-" * 56)
    for sname in STRATEGY_NAMES:
        t = totals[sname]
        pf = _calc_pf(t)
        n = t.get("tested", tested)
        pf1_str = f"{t['pf_above_1']}/{n}" if n > 0 else "—"
        print(f"  {sname:<12} | {t['trades']:>5} | {_pf_str(pf)} | {t['pnl']:>+12,.0f} | {pf1_str:>8}")


async def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    _cfg = yaml.safe_load(open(Path("config.yaml"), encoding="utf-8")) or {}
    bt_cfg = _cfg.get("backtest", {})
    backtest_config = BacktestConfig(
        commission=bt_cfg.get("commission", 0.00015),
        tax=bt_cfg.get("tax", 0.0018),
        slippage=bt_cfg.get("slippage", 0.0003),
    )
    tc = TradingConfig()

    uni = yaml.safe_load(open(Path("config/universe.yaml"), encoding="utf-8")) or {}
    all_stocks = uni.get("stocks", [])

    db = DbManager("daytrader.db")
    await db.init()
    bt = Backtester(db=db, config=tc, backtest_config=backtest_config)

    large_stocks = [s for s in all_stocks if s["ticker"] in LARGE_CAP_TICKERS]
    small_stocks = [s for s in all_stocks if s["ticker"] not in LARGE_CAP_TICKERS]

    FULL_START = "2025-11-01"
    FULL_END = "2026-03-23"

    # ─── 분석 1: 대형주 편향 ─────────────────────────────────────────────
    print()
    print("=" * 70)
    print("  분석 1: 대형주 편향 검증")
    print("=" * 70)

    print(f"\n  [대형주 {len(large_stocks)}종목만]")
    large_totals = await _run_group(bt, tc, large_stocks, FULL_START, FULL_END, "대형주")
    _print_table(large_totals, len(large_stocks))

    print(f"\n  [대형주 제외 ({len(small_stocks)}종목)]")
    small_totals = await _run_group(bt, tc, small_stocks, FULL_START, FULL_END, "나머지")
    _print_table(small_totals, len(small_stocks))

    # ─── 분석 2: 기간 분할 ───────────────────────────────────────────────
    print()
    print("=" * 70)
    print("  분석 2: 기간별 편향 검증")
    print("=" * 70)

    PERIOD_A_START, PERIOD_A_END = "2025-11-01", "2026-01-31"
    PERIOD_B_START, PERIOD_B_END = "2026-02-01", "2026-03-23"

    print(f"\n  [구간 A: {PERIOD_A_START} ~ {PERIOD_A_END} 상승장]")
    period_a = await _run_group(bt, tc, all_stocks, PERIOD_A_START, PERIOD_A_END, "A")
    tested_a = period_a[STRATEGY_NAMES[0]]["tested"]
    _print_table(period_a, tested_a)

    print(f"\n  [구간 B: {PERIOD_B_START} ~ {PERIOD_B_END} 조정장]")
    period_b = await _run_group(bt, tc, all_stocks, PERIOD_B_START, PERIOD_B_END, "B")
    tested_b = period_b[STRATEGY_NAMES[0]]["tested"]
    _print_table(period_b, tested_b)

    # ─── 전체 기간 (판정용) ─────────────────────────────────────────────
    full_totals = await _run_group(bt, tc, all_stocks, FULL_START, FULL_END, "전체")

    # ─── 분석 3: 종합 판정 ───────────────────────────────────────────────
    print()
    print("=" * 70)
    print("  분석 3: 종합 판정")
    print("=" * 70)
    print()
    print(f"  {'전략':<12} | {'전체PF':>6} | {'대형주제외':>8} | {'A구간PF':>7} | {'B구간PF':>7} | 판정")
    print("  " + "-" * 70)

    for sname in STRATEGY_NAMES:
        full_pf = _calc_pf(full_totals[sname])
        small_pf = _calc_pf(small_totals[sname])
        a_pf = _calc_pf(period_a[sname])
        b_pf = _calc_pf(period_b[sname])

        full_pnl = full_totals[sname]["pnl"]
        large_pnl = large_totals[sname]["pnl"]

        # 판정
        verdicts = []
        if abs(full_pnl) > 0 and abs(large_pnl / full_pnl) > 0.5:
            verdicts.append("대형주 의존")
        if small_pf > 1.0 and b_pf > 0.9:
            verdicts.append("진짜 엣지")
        elif small_pf < 0.9 and a_pf > 1.0:
            verdicts.append("상승장 편향")
        elif small_pf < 0.9 and a_pf < 0.9:
            verdicts.append("환경 무관 손실")

        verdict = " + ".join(verdicts) if verdicts else "판단 보류"

        print(
            f"  {sname:<12} | {_pf_str(full_pf)} | {_pf_str(small_pf)} | {_pf_str(a_pf)} | {_pf_str(b_pf)} | {verdict}"
        )

    print("=" * 70)
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
