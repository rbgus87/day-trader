"""scripts/grid_search_atr.py — Phase 2 ATR 파라미터 순차 그리드 서치.

Phase 1 필터(ADX20 + 시장) 고정. ATR stop/tp/trail multiplier를
3단계 순차 탐색하여 PnL 최대 조합을 찾는다.

Stage 1: stop_multiplier 그리드 (tp=3.0, trail=2.5 고정)
Stage 2: Stage 1 최적 stop + tp_multiplier 그리드
Stage 3: Stage 1, 2 최적 + trail_multiplier 그리드

시장 필터는 자동 포함 (Phase 1 기준선 +208k와 공정 비교).
"""

import asyncio
import os
import pickle
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import yaml
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig
from data.db_manager import DbManager

logger.remove()

START = "2025-04-01"
END = "2026-04-10"


def simulate_one(args: tuple) -> dict:
    """워커: 단일 종목 multi-day 백테스트 (시장 필터 포함)."""
    (
        ticker,
        ticker_market,
        candles_pickle,
        trading_config,
        backtest_config,
        market_map,
    ) = args

    import asyncio as _asyncio

    from backtest.backtester import Backtester as _Backtester
    from strategy.momentum_strategy import MomentumStrategy

    candles = pickle.loads(candles_pickle)
    strategy = MomentumStrategy(trading_config)
    bt = _Backtester(
        db=None,
        config=trading_config,
        backtest_config=backtest_config,
        ticker_market=ticker_market,
        market_strong_by_date=market_map,
    )
    return _asyncio.run(bt.run_multi_day_cached(ticker, candles, strategy))


async def run_grid(
    label: str,
    combinations: list[tuple[str, dict]],
    candles_cache: dict[str, bytes],
    ticker_to_market: dict[str, str],
    market_map: dict,
    base_config,
    backtest_config,
    workers: int,
) -> list[dict]:
    """단일 그리드 실행 + 결과 반환."""
    results: list[dict] = []
    for sub_label, overrides in combinations:
        trading_config = replace(base_config, **overrides)

        tasks = [
            (
                ticker,
                ticker_to_market.get(ticker, "unknown"),
                candles_cache[ticker],
                trading_config,
                backtest_config,
                market_map,
            )
            for ticker in candles_cache
        ]

        with ProcessPoolExecutor(max_workers=workers) as executor:
            kpis = list(executor.map(simulate_one, tasks))

        total_trades = sum(k["total_trades"] for k in kpis if k)
        total_pnl = sum(k["total_pnl"] for k in kpis if k)

        gp = 0.0
        gl = 0.0
        for k in kpis:
            if not k:
                continue
            for t in k.get("trades", []):
                p = t.get("pnl", 0.0)
                if p > 0:
                    gp += p
                elif p < 0:
                    gl += abs(p)
        pf = (gp / gl) if gl > 0 else float("inf")

        pf_above_1 = sum(
            1 for k in kpis
            if k and k["total_trades"] > 0 and k["profit_factor"] > 1.0
        )

        r = {
            "label": sub_label,
            "trades": total_trades,
            "pnl": total_pnl,
            "pf": pf,
            "pf_above_1": pf_above_1,
            "overrides": overrides,
        }
        results.append(r)
        pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
        print(
            f"  {sub_label:<25} 거래={total_trades:>5}  "
            f"PF={pf_str:>6}  PnL={total_pnl:>+12,.0f}  PF>1={pf_above_1}"
        )
    return results


def print_table(title: str, results: list[dict]) -> dict:
    print(f"\n=== {title} ===")
    print(f"{'조합':<25} {'거래':>6} {'PF':>6} {'총 PnL':>14} {'PF>1':>6}")
    print("-" * 70)
    for r in results:
        pf_str = f"{r['pf']:.2f}" if r["pf"] != float("inf") else "inf"
        print(
            f"{r['label']:<25} {r['trades']:>6} {pf_str:>6} "
            f"{r['pnl']:>+14,.0f} {r['pf_above_1']:>6}"
        )
    # PnL 최대 기준 선택
    valid = [r for r in results if r["pf"] != float("inf") and r["trades"] > 0]
    if not valid:
        valid = results
    best = max(valid, key=lambda x: x["pnl"])
    print(f"\n-> 최적: {best['label']} (PnL {best['pnl']:+,.0f}, PF {best['pf']:.2f})")
    return best


async def main() -> int:
    app_config = AppConfig.from_yaml()
    base_config = app_config.trading

    bt_cfg_raw = yaml.safe_load(
        open("config.yaml", encoding="utf-8")
    ).get("backtest", {})
    backtest_config = BacktestConfig(
        commission=bt_cfg_raw.get("commission", 0.00015),
        tax=bt_cfg_raw.get("tax", 0.0018),
        slippage=bt_cfg_raw.get("slippage", 0.0003),
    )

    uni = yaml.safe_load(open("config/universe.yaml", encoding="utf-8"))
    stocks = uni.get("stocks", [])
    ticker_to_market = {s["ticker"]: s.get("market", "unknown") for s in stocks}

    db = DbManager(app_config.db_path)
    await db.init()
    bt = Backtester(db=db, config=base_config, backtest_config=backtest_config)

    print(f"[LOAD] 캔들 로딩 ({len(stocks)}종목)...")
    candles_cache: dict[str, bytes] = {}
    for stock in stocks:
        ticker = stock["ticker"]
        candles = await bt.load_candles(ticker, START, f"{END} 23:59:59")
        if not candles.empty:
            candles_cache[ticker] = pickle.dumps(candles)
    print(f"  로드 완료: {len(candles_cache)}종목")

    await db.close()

    print("[LOAD] 시장 강세 맵 빌드...")
    market_map = build_market_strong_by_date(
        app_config.db_path, ma_length=base_config.market_ma_length
    )
    print(f"  날짜: {len(market_map)}일")

    workers = max(2, (os.cpu_count() or 2) - 1)
    print(f"[RUN] 병렬 워커: {workers}\n")

    # === STAGE 1: stop_multiplier ===
    print("=" * 80)
    print("STAGE 1: atr_stop_multiplier (tp=3.0, trail=2.5 고정)")
    print("=" * 80)
    stage1_combos = [
        (
            f"stop_mult={m}",
            {"atr_stop_multiplier": m, "atr_tp_multiplier": 3.0, "atr_trail_multiplier": 2.5},
        )
        for m in [1.0, 1.2, 1.5, 1.8, 2.0]
    ]
    stage1 = await run_grid(
        "Stage1", stage1_combos, candles_cache, ticker_to_market, market_map,
        base_config, backtest_config, workers,
    )
    best1 = print_table("Stage 1: stop_multiplier", stage1)
    best_stop = best1["overrides"]["atr_stop_multiplier"]

    # === STAGE 2: tp_multiplier ===
    print("\n" + "=" * 80)
    print(f"STAGE 2: atr_tp_multiplier (stop={best_stop} 고정, trail=2.5)")
    print("=" * 80)
    stage2_combos = [
        (
            f"tp_mult={m}",
            {"atr_stop_multiplier": best_stop, "atr_tp_multiplier": m, "atr_trail_multiplier": 2.5},
        )
        for m in [2.0, 3.0, 4.0, 5.0]
    ]
    stage2 = await run_grid(
        "Stage2", stage2_combos, candles_cache, ticker_to_market, market_map,
        base_config, backtest_config, workers,
    )
    best2 = print_table("Stage 2: tp_multiplier", stage2)
    best_tp = best2["overrides"]["atr_tp_multiplier"]

    # === STAGE 3: trail_multiplier ===
    print("\n" + "=" * 80)
    print(f"STAGE 3: atr_trail_multiplier (stop={best_stop}, tp={best_tp} 고정)")
    print("=" * 80)
    stage3_combos = [
        (
            f"trail_mult={m}",
            {"atr_stop_multiplier": best_stop, "atr_tp_multiplier": best_tp, "atr_trail_multiplier": m},
        )
        for m in [1.5, 2.0, 2.5, 3.0, 3.5]
    ]
    stage3 = await run_grid(
        "Stage3", stage3_combos, candles_cache, ticker_to_market, market_map,
        base_config, backtest_config, workers,
    )
    best3 = print_table("Stage 3: trail_multiplier", stage3)
    best_trail = best3["overrides"]["atr_trail_multiplier"]

    # === 최종 요약 ===
    print("\n" + "=" * 80)
    print("Phase 2 최적 파라미터")
    print("=" * 80)
    print(f"  atr_stop_multiplier:  {best_stop}")
    print(f"  atr_tp_multiplier:    {best_tp}")
    print(f"  atr_trail_multiplier: {best_trail}")
    print()
    print(f"  최종 PnL:    {best3['pnl']:+,.0f}")
    print(f"  PF:          {best3['pf']:.2f}")
    print(f"  거래수:      {best3['trades']}")
    print(f"  PF>1 종목:   {best3['pf_above_1']}")
    print()
    print("  [기준선] Phase 1 L 조합: PnL +208,303, PF 1.46, 거래 501")
    improvement = best3["pnl"] - 208_303
    print(f"  [증감] PnL {improvement:+,.0f} ({improvement/208303*100:+.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
