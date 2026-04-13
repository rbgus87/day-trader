"""scripts/grid_search_buytime.py — Phase 3 Day 11.5 최종 buy_time_end 그리드.

현재 11:30(PnL +204k) vs 비활성(+238k) 사이에서 균형점 탐색.
consecutive_rest / daily_loss -1.5%는 강세장 효과 0이라 유지.
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

CASES = [
    ("0. 비활성", {"buy_time_limit_enabled": False}),
    ("1. 11:00",  {"buy_time_limit_enabled": True, "buy_time_end": "11:00"}),
    ("2. 11:30",  {"buy_time_limit_enabled": True, "buy_time_end": "11:30"}),
    ("3. 12:00",  {"buy_time_limit_enabled": True, "buy_time_end": "12:00"}),
    ("4. 13:00",  {"buy_time_limit_enabled": True, "buy_time_end": "13:00"}),
    ("5. 14:00",  {"buy_time_limit_enabled": True, "buy_time_end": "14:00"}),
]


def simulate_one(args: tuple) -> dict:
    (ticker, ticker_market, candles_pickle,
     trading_config, backtest_config, market_map) = args
    import asyncio as _asyncio
    from backtest.backtester import Backtester as _Backtester
    from strategy.momentum_strategy import MomentumStrategy
    candles = pickle.loads(candles_pickle)
    strategy = MomentumStrategy(trading_config)
    bt = _Backtester(
        db=None, config=trading_config, backtest_config=backtest_config,
        ticker_market=ticker_market, market_strong_by_date=market_map,
    )
    return _asyncio.run(bt.run_multi_day_cached(ticker, candles, strategy))


async def main() -> int:
    cfg = AppConfig.from_yaml()
    base = cfg.trading
    bt_cfg_raw = yaml.safe_load(open("config.yaml", encoding="utf-8")).get("backtest", {})
    bt_cfg = BacktestConfig(
        commission=bt_cfg_raw.get("commission", 0.00015),
        tax=bt_cfg_raw.get("tax", 0.0018),
        slippage=bt_cfg_raw.get("slippage", 0.0003),
    )
    uni = yaml.safe_load(open("config/universe.yaml", encoding="utf-8"))
    stocks = uni.get("stocks", [])
    ticker_to_market = {s["ticker"]: s.get("market", "unknown") for s in stocks}

    db = DbManager(cfg.db_path)
    await db.init()
    loader = Backtester(db=db, config=base, backtest_config=bt_cfg)

    print(f"[LOAD] 캔들 ({len(stocks)}종목)...")
    candles_cache: dict[str, bytes] = {}
    for s in stocks:
        tk = s["ticker"]
        candles = await loader.load_candles(tk, START, f"{END} 23:59:59")
        if not candles.empty:
            candles_cache[tk] = pickle.dumps(candles)
    print(f"  로드 {len(candles_cache)}")
    await db.close()

    market_map = build_market_strong_by_date(cfg.db_path, ma_length=base.market_ma_length)
    workers = max(2, (os.cpu_count() or 2) - 1)
    print(f"[RUN] 워커 {workers}\n")

    results: list[dict] = []
    for label, overrides in CASES:
        trading_config = replace(base, **overrides)
        tasks = [
            (tk, ticker_to_market.get(tk, "unknown"),
             candles_cache[tk], trading_config, bt_cfg, market_map)
            for tk in candles_cache
        ]
        with ProcessPoolExecutor(max_workers=workers) as executor:
            kpis = list(executor.map(simulate_one, tasks))

        total_trades = sum(k["total_trades"] for k in kpis if k)
        total_pnl = sum(k["total_pnl"] for k in kpis if k)
        gp = gl = 0.0
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
        per_trade = total_pnl / total_trades if total_trades else 0.0

        results.append({
            "label": label, "trades": total_trades, "pf": pf,
            "pnl": total_pnl, "pf_above_1": pf_above_1,
            "per_trade": per_trade,
        })
        pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
        print(f"  {label:<15} 거래={total_trades:>4} PF={pf_str:>5} "
              f"PnL={total_pnl:>+12,.0f} PF>1={pf_above_1} /trade={per_trade:>+8,.0f}")

    # 효율 계산 (기준: 비활성)
    baseline = results[0]
    print("\n" + "=" * 90)
    print(f"{'조합':<15} {'거래':>6} {'PF':>6} {'PnL':>14} {'/trade':>8} "
          f"{'PnL_Δ%':>8} {'PF_Δ%':>7} {'효율':>7}")
    print("-" * 90)
    for r in results:
        pf_str = f"{r['pf']:.2f}" if r["pf"] != float("inf") else "inf"
        pnl_delta = (r["pnl"] - baseline["pnl"]) / baseline["pnl"] * 100 if baseline["pnl"] else 0
        pf_delta = ((r["pf"] - baseline["pf"]) / baseline["pf"] * 100) if baseline["pf"] else 0
        # 효율 = PnL 감소폭 / PF 상승폭 (작을수록 trade-off 유리)
        if pf_delta > 0:
            efficiency = abs(pnl_delta) / pf_delta
            eff_str = f"{efficiency:.3f}"
        else:
            eff_str = "—"
        print(f"{r['label']:<15} {r['trades']:>6} {pf_str:>6} "
              f"{r['pnl']:>+14,.0f} {r['per_trade']:>+8,.0f} "
              f"{pnl_delta:>+7.1f}% {pf_delta:>+6.1f}% {eff_str:>7}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
