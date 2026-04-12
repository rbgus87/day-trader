"""scripts/backtest_ablation.py — Phase 3 Day 11.5 방어 레벨 A ablation.

3가지 방어를 개별 on/off로 분리해 각 기여도 측정:
  - buy_time_limit
  - consecutive_loss_rest
  - daily_max_loss (-1.5% 강화 vs -2.0% 기존)

기준선 A: Phase 3 (모든 방어 off 상태로 되돌림 → 실제로는 레벨 A 3개만 off)
비교 4종:
  1. baseline (레벨 A 3개 전부 off)
  2. buy_time_limit only
  3. consecutive_rest only
  4. full (현재 config)
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
    ("0. baseline (A 전부 off)", {
        "buy_time_limit_enabled": False,
        "consecutive_loss_rest_enabled": False,
        "daily_max_loss_pct": -0.02,
    }),
    ("1. buy_time_limit only", {
        "buy_time_limit_enabled": True,
        "consecutive_loss_rest_enabled": False,
        "daily_max_loss_pct": -0.02,
    }),
    ("2. consecutive_rest only", {
        "buy_time_limit_enabled": False,
        "consecutive_loss_rest_enabled": True,
        "daily_max_loss_pct": -0.02,
    }),
    ("3. daily_loss -1.5% only", {
        "buy_time_limit_enabled": False,
        "consecutive_loss_rest_enabled": False,
        "daily_max_loss_pct": -0.015,
    }),
    ("4. FULL (모두 on)", {
        "buy_time_limit_enabled": True,
        "consecutive_loss_rest_enabled": True,
        "daily_max_loss_pct": -0.015,
    }),
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
        ec: Counter = Counter()
        for k in kpis:
            if not k:
                continue
            for t in k.get("trades", []):
                p = t.get("pnl", 0.0)
                if p > 0:
                    gp += p
                elif p < 0:
                    gl += abs(p)
                ec[t.get("exit_reason", "?")] += 1
        pf = (gp / gl) if gl > 0 else float("inf")
        pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
        print(f"  {label:<30} 거래={total_trades:>4} PF={pf_str:>5} "
              f"PnL={total_pnl:>+12,.0f}")
        dist = ",".join(f"{k[:4]}={v}" for k, v in ec.most_common())
        print(f"    {dist}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
