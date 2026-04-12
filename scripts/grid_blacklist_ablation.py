"""scripts/grid_blacklist_ablation.py — 60종목 블랙리스트 on/off × buy_time.

Phase 3 Day 11.5 후속: 블랙리스트가 60종목에서 유익한지 판단.
buy_time 3개(비활성/11:30/12:00) × 블랙리스트 on/off = 6 조합.
"""

import asyncio
import os
import pickle
import sys
from collections import Counter
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
    ("비활성 + BL ON",  {"buy_time_limit_enabled": False, "blacklist_enabled": True}),
    ("비활성 + BL OFF", {"buy_time_limit_enabled": False, "blacklist_enabled": False}),
    ("11:30 + BL ON",   {"buy_time_limit_enabled": True, "buy_time_end": "11:30", "blacklist_enabled": True}),
    ("11:30 + BL OFF",  {"buy_time_limit_enabled": True, "buy_time_end": "11:30", "blacklist_enabled": False}),
    ("12:00 + BL ON",   {"buy_time_limit_enabled": True, "buy_time_end": "12:00", "blacklist_enabled": True}),
    ("12:00 + BL OFF",  {"buy_time_limit_enabled": True, "buy_time_end": "12:00", "blacklist_enabled": False}),
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
        pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
        print(f"  {label:<22} 거래={total_trades:>4} PF={pf_str:>5} "
              f"PnL={total_pnl:>+12,.0f} PF>1={pf_above_1:>2} /trade={per_trade:>+7,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
