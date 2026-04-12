"""scripts/backtest_defense_level_a.py — Phase 3 Day 11.5 영향 측정.

현재 config 상태 (ADX20 + market + ATR + Day 10 방어 + buy_time_limit 11:30
+ 연속 손실 휴식)로 12종목 universe 단일 백테스트 + exit_reason 분포.
"""

import asyncio
import pickle
import sys
from collections import Counter
from pathlib import Path

import yaml
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig
from data.db_manager import DbManager
from strategy.momentum_strategy import MomentumStrategy

logger.remove()
START = "2025-04-01"
END = "2026-04-10"


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
    market_map = build_market_strong_by_date(cfg.db_path, ma_length=base.market_ma_length)
    loader = Backtester(db=db, config=base, backtest_config=bt_cfg)

    all_kpis = []
    for s in stocks:
        tk = s["ticker"]
        candles = await loader.load_candles(tk, START, f"{END} 23:59:59")
        if candles.empty:
            continue
        strat = MomentumStrategy(base)
        bt = Backtester(
            db=None, config=base, backtest_config=bt_cfg,
            ticker_market=s.get("market", "unknown"),
            market_strong_by_date=market_map,
        )
        kpi = await bt.run_multi_day_cached(tk, candles, strat)
        all_kpis.append(kpi)
    await db.close()

    total_trades = sum(k["total_trades"] for k in all_kpis if k)
    total_pnl = sum(k["total_pnl"] for k in all_kpis if k)
    gp = gl = 0.0
    exit_counter: Counter = Counter()
    for k in all_kpis:
        if not k:
            continue
        for t in k.get("trades", []):
            p = t.get("pnl", 0.0)
            if p > 0:
                gp += p
            elif p < 0:
                gl += abs(p)
            exit_counter[t.get("exit_reason", "?")] += 1
    pf = (gp / gl) if gl > 0 else float("inf")
    pf_above_1 = sum(
        1 for k in all_kpis
        if k and k["total_trades"] > 0 and k["profit_factor"] > 1.0
    )

    print("[Phase 3 Day 11.5] 방어 레벨 A 적용 백테스트")
    print(f"  universe: {len(stocks)}종목")
    print(f"  buy_time_end: {base.buy_time_end}, daily_max_loss: {base.daily_max_loss_pct}")
    print(f"  consecutive_loss_threshold: {base.consecutive_loss_threshold}")
    print(f"  거래: {total_trades}")
    print(f"  PF:   {pf:.2f}")
    print(f"  PnL:  {total_pnl:+,.0f}")
    print(f"  PF>1 종목: {pf_above_1}")
    print()
    total = sum(exit_counter.values()) or 1
    for reason, n in exit_counter.most_command() if False else exit_counter.most_common():
        print(f"  {reason:<18} {n:>4} ({n/total*100:.1f}%)")
    print()
    print("[기준선 Phase 3] 거래 229 / PF 2.32 / PnL +213,513")
    diff = total_pnl - 213_513
    print(f"[PnL 차이] {diff:+,.0f} ({diff/213_513*100:+.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
