"""scripts/grid_volume_ratio_v2.py — baseline 로직(BE3 + limit_up_exit) 그리드.

기존 grid_volume_ratio.py는 ADR-017/018 미반영. 이 스크립트는
backtester.run_multi_day_cached를 그대로 사용해 baseline PF 4.56과
직접 비교 가능한 형태로 volume_ratio를 그리드 서치한다.

사용:
    python scripts/grid_volume_ratio_v2.py
    python scripts/grid_volume_ratio_v2.py --start 2025-04-01 --end 2026-04-10
"""

import argparse
import asyncio
import os
import pickle
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import pandas as pd
import yaml
from loguru import logger

logger.remove()  # 메인 프로세스 로그 억제 (워커 stdout 폭주 방지)

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig
from data.db_manager import DbManager

SCENARIOS = [
    ("V20", "ratio 2.0 (현행)", 2.0),
    ("V15", "ratio 1.5",        1.5),
    ("V10", "ratio 1.0 (참고)", 1.0),
]


def _simulate_one(args):
    ticker, ticker_market, candles_pickle, trading_config, backtest_config, market_map = args
    from loguru import logger as _lg
    _lg.remove()  # 워커 stdout 로그 차단 (BrokenProcessPool 방지)
    import asyncio as _asyncio
    from backtest.backtester import Backtester as _Bt
    from strategy.momentum_strategy import MomentumStrategy

    candles = pickle.loads(candles_pickle)
    strategy = MomentumStrategy(trading_config)
    bt = _Bt(
        db=None,
        config=trading_config,
        backtest_config=backtest_config,
        ticker_market=ticker_market,
        market_strong_by_date=market_map,
    )
    result = _asyncio.run(bt.run_multi_day_cached(ticker, candles, strategy))
    for t in result.get("trades", []):
        t["ticker"] = ticker
        t["ticker_market"] = ticker_market
    return result


async def collect_trades(start, end, volume_ratio):
    app_config = AppConfig.from_yaml()
    base_config = app_config.trading

    bt_raw = yaml.safe_load(open("config.yaml", encoding="utf-8")).get("backtest", {})
    backtest_config = BacktestConfig(
        commission=bt_raw.get("commission", 0.00015),
        tax=bt_raw.get("tax", 0.0018),
        slippage=bt_raw.get("slippage", 0.0003),
    )

    overridden = replace(base_config, momentum_volume_ratio=volume_ratio)

    uni = yaml.safe_load(open("config/universe_backtest.yaml", encoding="utf-8"))
    stocks = uni.get("stocks", [])
    ticker_to_market = {s["ticker"]: s.get("market", "unknown") for s in stocks}

    db = DbManager(app_config.db_path)
    await db.init()
    loader = Backtester(db=db, config=overridden, backtest_config=backtest_config)
    candles_cache = {}
    for s in stocks:
        tk = s["ticker"]
        c = await loader.load_candles(tk, start, f"{end} 23:59:59")
        if not c.empty:
            candles_cache[tk] = pickle.dumps(c)
    await db.close()

    market_map = build_market_strong_by_date(app_config.db_path, ma_length=overridden.market_ma_length)
    workers = max(2, (os.cpu_count() or 2) - 1)
    tasks = [
        (tk, ticker_to_market.get(tk, "unknown"), candles_cache[tk], overridden, backtest_config, market_map)
        for tk in candles_cache
    ]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        kpis = list(ex.map(_simulate_one, tasks))

    trades = []
    for kpi in kpis:
        if kpi:
            trades.extend(kpi.get("trades", []))
    return trades


def entry_time_bucket(ts):
    """진입 시각을 30분 버킷으로."""
    t = pd.to_datetime(ts)
    h, m = t.hour, t.minute
    if h == 9 and m < 30:
        return "09:05-09:30"
    if h == 9:
        return "09:30-10:00"
    if h == 10 and m < 30:
        return "10:00-10:30"
    if h == 10:
        return "10:30-11:00"
    if h == 11 and m < 30:
        return "11:00-11:30"
    if h == 11:
        return "11:30-12:00"
    return "기타"


def summarize(trades, label):
    n = len(trades)
    if n == 0:
        print(f"\n=== {label} === NO TRADES")
        return
    pnls = [t["pnl"] for t in trades]
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p < 0))
    pf = gp / gl if gl > 0 else float("inf")
    total = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    win_rate = wins / n * 100

    exit_dist = Counter(t.get("exit_reason", "?") for t in trades)

    bucket = defaultdict(int)
    for t in trades:
        bucket[entry_time_bucket(t["entry_ts"])] += 1

    print(f"\n=== {label} ===")
    print(f"  거래: {n}건 / PF: {pf:.2f} / 총 PnL: {total:+,.0f} / 거래당: {total/n:+,.0f} / 승률: {win_rate:.1f}%")
    print(f"  청산:")
    for r, c in exit_dist.most_common():
        pct = c / n * 100
        r_trades = [t for t in trades if t.get("exit_reason") == r]
        r_pnl = sum(t["pnl"] for t in r_trades)
        print(f"    {r:<18} {c:>4}건 ({pct:>5.1f}%)  PnL {r_pnl:+,.0f}")
    print(f"  진입 시간대:")
    order = ["09:05-09:30", "09:30-10:00", "10:00-10:30", "10:30-11:00", "11:00-11:30", "11:30-12:00", "기타"]
    for b in order:
        c = bucket.get(b, 0)
        if c == 0:
            continue
        pct = c / n * 100
        b_trades = [t for t in trades if entry_time_bucket(t["entry_ts"]) == b]
        b_pnl = sum(t["pnl"] for t in b_trades)
        b_pf = (sum(p for p in (t["pnl"] for t in b_trades) if p > 0) /
                abs(sum(p for p in (t["pnl"] for t in b_trades) if p < 0) or 1)) if b_trades else 0
        print(f"    {b:<14} {c:>4}건 ({pct:>5.1f}%)  PnL {b_pnl:+,.0f}  PF {b_pf:.2f}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-04-01")
    parser.add_argument("--end", default="2026-04-10")
    args = parser.parse_args()

    print("=" * 70)
    print(f" Volume Ratio Grid (BE3 + limit_up_exit)  {args.start} ~ {args.end}")
    print("=" * 70)

    for name, desc, ratio in SCENARIOS:
        print(f"\n[RUN] {name} {desc}")
        trades = await collect_trades(args.start, args.end, ratio)
        summarize(trades, f"{name} {desc}")


if __name__ == "__main__":
    asyncio.run(main())
