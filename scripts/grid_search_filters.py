"""scripts/grid_search_filters.py — Phase 1 필터 조합 그리드 백테스트.

5개 조합 비교:
  A. 필터 없음 (기준)
  B. ADX만
  C. ADX + RVol
  D. ADX + RVol + VWAP
  E. ADX + RVol + VWAP + 시장

사용:
    python scripts/grid_search_filters.py
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

logger.remove()  # 워커 로그 억제

START = "2025-04-01"
END = "2026-04-10"

COMBINATIONS = [
    (
        "A. 필터 없음",
        {
            "adx_enabled": False,
            "rvol_enabled": False,
            "vwap_enabled": False,
            "market_filter_enabled": False,
        },
    ),
    (
        "B. ADX만",
        {
            "adx_enabled": True,
            "rvol_enabled": False,
            "vwap_enabled": False,
            "market_filter_enabled": False,
        },
    ),
    (
        "C. ADX+RVol",
        {
            "adx_enabled": True,
            "rvol_enabled": True,
            "vwap_enabled": False,
            "market_filter_enabled": False,
        },
    ),
    (
        "D. ADX+RVol+VWAP",
        {
            "adx_enabled": True,
            "rvol_enabled": True,
            "vwap_enabled": True,
            "market_filter_enabled": False,
        },
    ),
    (
        "E. 전부 (+시장)",
        {
            "adx_enabled": True,
            "rvol_enabled": True,
            "vwap_enabled": True,
            "market_filter_enabled": True,
        },
    ),
    (
        "F. RVol만",
        {
            "adx_enabled": False,
            "rvol_enabled": True,
            "vwap_enabled": False,
            "market_filter_enabled": False,
        },
    ),
    (
        "G. RVol+시장",
        {
            "adx_enabled": False,
            "rvol_enabled": True,
            "vwap_enabled": False,
            "market_filter_enabled": True,
        },
    ),
    (
        "H. 시장만",
        {
            "adx_enabled": False,
            "rvol_enabled": False,
            "vwap_enabled": False,
            "market_filter_enabled": True,
        },
    ),
]


def simulate_one(args: tuple) -> dict:
    """ProcessPool 워커: 단일 종목 multi-day 백테스트."""
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

    print(f"[LOAD] 캔들 로딩 중 ({len(stocks)}종목)...")
    candles_cache: dict[str, bytes] = {}
    for stock in stocks:
        ticker = stock["ticker"]
        candles = await bt.load_candles(ticker, START, f"{END} 23:59:59")
        if not candles.empty:
            candles_cache[ticker] = pickle.dumps(candles)
    print(f"  로드 완료: {len(candles_cache)}종목")

    await db.close()

    # 지수 강세 맵 (E 조합만 사용하지만 한 번 계산해서 재사용)
    print("[LOAD] 지수 강세 맵 빌드 중...")
    market_map = build_market_strong_by_date(
        app_config.db_path, ma_length=base_config.market_ma_length
    )
    print(f"  날짜: {len(market_map)}일")

    workers = max(2, (os.cpu_count() or 2) - 1)
    print(f"[RUN] 병렬 워커: {workers}\n")

    results: list[dict] = []
    for label, overrides in COMBINATIONS:
        print(f"=== {label} ===")
        trading_config = replace(base_config, **overrides)

        use_market = overrides["market_filter_enabled"]
        tasks = [
            (
                ticker,
                ticker_to_market.get(ticker, "unknown"),
                candles_cache[ticker],
                trading_config,
                backtest_config,
                market_map if use_market else {},
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
            1
            for k in kpis
            if k and k["total_trades"] > 0 and k["profit_factor"] > 1.0
        )

        r = {
            "label": label,
            "trades": total_trades,
            "pnl": total_pnl,
            "pf": pf,
            "pf_above_1": pf_above_1,
        }
        results.append(r)
        print(
            f"  거래={r['trades']}, PF={r['pf']:.2f}, "
            f"PnL={r['pnl']:+,.0f}, PF>1 종목={r['pf_above_1']}\n"
        )

    # 최종 표
    print("=" * 80)
    print(f"{'조합':<25} {'거래':>7} {'PF':>6} {'총 PnL':>16} {'PF>1':>6}")
    print("-" * 80)
    for r in results:
        pf_str = f"{r['pf']:.2f}" if r["pf"] != float("inf") else "inf"
        print(
            f"{r['label']:<25} {r['trades']:>7} {pf_str:>6} "
            f"{r['pnl']:>+16,.0f} {r['pf_above_1']:>6}"
        )
    print("=" * 80)

    valid = [r for r in results if r["pf"] != float("inf") and r["trades"] > 0]
    if valid:
        best = max(valid, key=lambda x: x["pf"])
        print(f"\n[BEST] {best['label']} - PF {best['pf']:.2f}, 거래 {best['trades']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
