"""scripts/grid_market_ma_period.py — 시장 필터 MA 기간 비교 그리드.

baseline 모멘텀 설정 (vr2.0 + 현행 파라미터 전부) 에서
market_filter_ma 기간만 [5(현행), 10, 20, off] 로 변경.

사용:
    python scripts/grid_market_ma_period.py
"""

from __future__ import annotations

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

logger.remove()

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig
from data.db_manager import DbManager

OLD_START = "2025-04-01"
OLD_END   = "2026-04-10"
NEW_START = "2026-04-11"
NEW_END   = "2026-05-12"

# (label, market_filter_enabled, ma_length)
SCENARIOS = [
    ("MA5 (현행)", True,  5),
    ("MA10",       True, 10),
    ("MA20",       True, 20),
    ("off",        False, 5),   # ma_length 무관 (filter disabled)
]


def _simulate_one(args):
    ticker, ticker_market, candles_pickle, trading_config, backtest_config, market_map = args
    from loguru import logger as _lg
    _lg.remove()
    import asyncio as _a
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
    result = _a.run(bt.run_multi_day_cached(ticker, candles, strategy))
    for t in result.get("trades", []):
        t["ticker"] = ticker
        t["ticker_market"] = ticker_market
    return result


async def run_scenario(
    candles_cache: dict,
    ticker_to_market: dict,
    base_config,
    backtest_config: BacktestConfig,
    db_path: str,
    mf_enabled: bool,
    ma_length: int,
) -> list[dict]:
    overridden = replace(
        base_config,
        market_filter_enabled=mf_enabled,
        market_ma_length=ma_length,
    )
    market_map = build_market_strong_by_date(db_path, ma_length=ma_length) if mf_enabled else {}

    workers = max(2, (os.cpu_count() or 2) - 1)
    tasks = [
        (tk, ticker_to_market.get(tk, "unknown"), candles_cache[tk], overridden, backtest_config, market_map)
        for tk in candles_cache
    ]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        kpis = list(ex.map(_simulate_one, tasks))

    trades: list[dict] = []
    for kpi in kpis:
        if kpi:
            trades.extend(kpi.get("trades", []))
    return trades


def compute_stats(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return dict(n=0, pf=0.0, pnl=0, win_rate=0.0)
    pnls = [t["pnl"] for t in trades]
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p < 0))
    pf = gp / gl if gl > 0 else float("inf")
    wins = sum(1 for p in pnls if p > 0)
    return dict(n=n, pf=round(pf, 3), pnl=int(sum(pnls)), win_rate=round(wins / n * 100, 1))


async def main() -> None:
    app = AppConfig.from_yaml()
    base_config = app.trading
    db_path = app.db_path

    bt_raw = yaml.safe_load(open("config.yaml", encoding="utf-8")).get("backtest", {})
    backtest_config = BacktestConfig(
        commission=bt_raw.get("commission", 0.00015),
        tax=bt_raw.get("tax", 0.0018),
        slippage=bt_raw.get("slippage", 0.0003),
    )

    uni = yaml.safe_load(open("config/universe_backtest.yaml", encoding="utf-8"))
    stocks = uni.get("stocks", [])
    ticker_to_market = {s["ticker"]: s.get("market", "unknown") for s in stocks}

    db = DbManager(db_path)
    await db.init()
    loader = Backtester(db=db, config=base_config, backtest_config=backtest_config)

    print("분봉 로드 중 (OLD)...", flush=True)
    old_cache: dict[str, bytes] = {}
    for s in stocks:
        tk = s["ticker"]
        c = await loader.load_candles(tk, OLD_START, f"{OLD_END} 23:59:59")
        if not c.empty:
            old_cache[tk] = pickle.dumps(c)
    print(f"  → {len(old_cache)}종목")

    print("분봉 로드 중 (NEW)...", flush=True)
    new_cache: dict[str, bytes] = {}
    for s in stocks:
        tk = s["ticker"]
        c = await loader.load_candles(tk, NEW_START, f"{NEW_END} 23:59:59")
        if not c.empty:
            new_cache[tk] = pickle.dumps(c)
    print(f"  → {len(new_cache)}종목")

    await db.close()

    # ── 시나리오별 실행 ────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("시장 필터 MA 기간 그리드 (모멘텀 전략, vr2.0 baseline)")
    print(f"OLD: {OLD_START} ~ {OLD_END}  /  NEW: {NEW_START} ~ {NEW_END}")
    print("=" * 78)

    # off 기준 (차단 건수 계산용)
    off_old_n: int | None = None
    off_new_n: int | None = None

    results: list[dict] = []
    for label, mf_enabled, ma_len in SCENARIOS:
        print(f"\n[RUN] {label} ...", flush=True)
        old_trades = await run_scenario(
            old_cache, ticker_to_market, base_config, backtest_config, db_path, mf_enabled, ma_len
        )
        new_trades = await run_scenario(
            new_cache, ticker_to_market, base_config, backtest_config, db_path, mf_enabled, ma_len
        )
        os = compute_stats(old_trades)
        ns = compute_stats(new_trades)

        if label == "off":
            off_old_n = os["n"]
            off_new_n = ns["n"]

        results.append(dict(
            label=label,
            old=os, new=ns,
            old_trades=old_trades,
            new_trades=new_trades,
        ))

    # ── 보고서 출력 ────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print(f"{'시나리오':<12} | {'OLD N':>6} {'차단':>5} {'PF':>7} {'PnL':>11} {'승률':>7} | {'NEW N':>6} {'차단':>5} {'PF':>7} {'PnL':>11} {'승률':>7}")
    print("-" * 78)

    for r in results:
        label = r["label"]
        os_ = r["old"]
        ns_ = r["new"]
        old_blocked = (off_old_n - os_["n"]) if (off_old_n is not None and label != "off") else 0
        new_blocked = (off_new_n - ns_["n"]) if (off_new_n is not None and label != "off") else 0
        print(
            f"  {label:<10} | {os_['n']:>6} {old_blocked:>5} {os_['pf']:>7.3f} {os_['pnl']:>+11,} {os_['win_rate']:>6.1f}% | "
            f"{ns_['n']:>6} {new_blocked:>5} {ns_['pf']:>7.3f} {ns_['pnl']:>+11,} {ns_['win_rate']:>6.1f}%"
        )

    print("\n[청산 분포 — 각 시나리오 OLD]")
    for r in results:
        label = r["label"]
        ts = r["old_trades"]
        if not ts:
            continue
        dist = Counter(t.get("exit_reason", "?") for t in ts)
        parts = "  ".join(f"{reason}:{cnt}" for reason, cnt in dist.most_common(6))
        print(f"  {label:<10}: {parts}")

    print()


if __name__ == "__main__":
    asyncio.run(main())
