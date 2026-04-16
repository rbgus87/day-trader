"""ATR vs PF 상관 분석 스크립트 (1회용)."""
import asyncio
import os
import pickle
import sqlite3
import statistics
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig
from data.db_manager import DbManager


def simulate_one(args):
    ticker, ticker_market, candles_pickle, trading_config, backtest_config, market_map = args
    import asyncio as _a
    from backtest.backtester import Backtester as _B
    from strategy.momentum_strategy import MomentumStrategy

    candles = pickle.loads(candles_pickle)
    strategy = MomentumStrategy(trading_config)
    bt = _B(
        db=None, config=trading_config, backtest_config=backtest_config,
        ticker_market=ticker_market, market_strong_by_date=market_map,
    )
    return _a.run(bt.run_multi_day_cached(ticker, candles, strategy))


async def main():
    app_config = AppConfig.from_yaml()
    base_config = app_config.trading
    bt_cfg_raw = yaml.safe_load(open("config.yaml", encoding="utf-8")).get("backtest", {})
    backtest_config = BacktestConfig(
        commission=bt_cfg_raw.get("commission", 0.00015),
        tax=bt_cfg_raw.get("tax", 0.0018),
        slippage=bt_cfg_raw.get("slippage", 0.0003),
    )

    # ATR map from DB
    conn = sqlite3.connect("daytrader.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT ticker, atr_pct FROM ticker_atr WHERE dt = (SELECT MAX(dt) FROM ticker_atr)"
    ).fetchall()
    atr_map = {r["ticker"]: r["atr_pct"] / 100 for r in rows}  # DB stores percent, convert to ratio
    conn.close()

    # 41종목 universe
    with open("config/universe_pre_20260416.yaml", encoding="utf-8") as f:
        old41_data = yaml.safe_load(f) or {}
    old41_stocks = old41_data.get("stocks", [])
    old41_tickers = {s["ticker"] for s in old41_stocks}
    old41_names = {s["ticker"]: s.get("name", s["ticker"]) for s in old41_stocks}
    old41_markets = {s["ticker"]: s.get("market", "unknown") for s in old41_stocks}

    # 60종목 universe (market 정보 보충)
    with open("config/universe_pre_atr6_20260416.yaml", encoding="utf-8") as f:
        all60_data = yaml.safe_load(f) or {}
    for s in all60_data.get("stocks", []):
        tk = s["ticker"]
        if tk not in old41_markets:
            old41_markets[tk] = s.get("market", "unknown")
        if tk not in old41_names:
            old41_names[tk] = s.get("name", tk)

    # Load candles for ALL tickers with ATR
    db = DbManager(app_config.db_path)
    await db.init()
    bt_loader = Backtester(db=db, config=base_config, backtest_config=backtest_config)

    candles_cache = {}
    for tk in atr_map:
        candles = await bt_loader.load_candles(tk, "2025-04-01", "2026-04-10 23:59:59")
        if not candles.empty:
            candles_cache[tk] = pickle.dumps(candles)
    await db.close()

    market_map = build_market_strong_by_date(app_config.db_path, ma_length=base_config.market_ma_length)

    tasks = []
    for tk in candles_cache:
        mkt = old41_markets.get(tk, "unknown")
        tasks.append((tk, mkt, candles_cache[tk], base_config, backtest_config, market_map))

    workers = max(2, (os.cpu_count() or 2) - 1)
    with ProcessPoolExecutor(max_workers=workers) as executor:
        kpis = list(executor.map(simulate_one, tasks))

    # Build results
    task_tickers = [t[0] for t in tasks]
    results = {}
    for tk, kpi in zip(task_tickers, kpis):
        trades_list = kpi.get("trades", []) if kpi else []
        gp = sum(t["pnl"] for t in trades_list if t.get("pnl", 0) > 0)
        gl = sum(abs(t["pnl"]) for t in trades_list if t.get("pnl", 0) < 0)
        pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0)
        results[tk] = {
            "pf": pf,
            "trades": kpi.get("total_trades", 0) if kpi else 0,
            "pnl": kpi.get("total_pnl", 0) if kpi else 0,
            "atr": atr_map.get(tk, 0),
            "in_old41": tk in old41_tickers,
            "name": old41_names.get(tk, tk),
        }

    # ======== ANALYSIS ========
    print("=" * 70)
    print(" 분석 1: 이전 41종목 ATR 분포")
    print("=" * 70)

    old41_rows = [
        (tk, r["atr"], r["pf"], r["trades"], r["pnl"])
        for tk, r in results.items()
        if r["in_old41"] and r["atr"] > 0
    ]
    old41_rows.sort(key=lambda x: x[1])

    atrs_only = [a[1] for a in old41_rows]
    print(f"  종목 수: {len(old41_rows)}")
    print(f"  ATR 최소: {min(atrs_only):.2%}")
    print(f"  ATR 최대: {max(atrs_only):.2%}")
    print(f"  ATR 중앙: {statistics.median(atrs_only):.2%}")
    print(f"  ATR 평균: {statistics.mean(atrs_only):.2%}")
    print()

    band_6_8 = [a for a in atrs_only if 0.06 <= a < 0.08]
    band_8_10 = [a for a in atrs_only if 0.08 <= a < 0.10]
    band_10_12 = [a for a in atrs_only if 0.10 <= a < 0.12]
    band_12p = [a for a in atrs_only if a >= 0.12]
    print(f"  ATR 6~8%:   {len(band_6_8)}종목")
    print(f"  ATR 8~10%:  {len(band_8_10)}종목")
    print(f"  ATR 10~12%: {len(band_10_12)}종목")
    print(f"  ATR 12%+:   {len(band_12p)}종목")

    # Histogram
    print()
    print("  히스토그램:")
    for lo in range(6, 16, 2):
        hi = lo + 2
        cnt = len([a for a in atrs_only if lo / 100 <= a < hi / 100])
        bar = "#" * cnt
        print(f"  {lo:>2}~{hi:>2}%: {bar} ({cnt})")
    cnt_16p = len([a for a in atrs_only if a >= 0.16])
    if cnt_16p:
        print(f"   16%+: {'#' * cnt_16p} ({cnt_16p})")

    print()
    print("=" * 70)
    print(" 분석 2: 41종목 - ATR vs PF (PF 내림차순)")
    print("=" * 70)
    print(f"{'ticker':<8} {'name':<14} {'ATR':>7} {'PF':>7} {'trades':>6} {'PnL':>10}")
    print("-" * 60)
    for tk, atr, pf, trades, pnl in sorted(old41_rows, key=lambda x: -x[2]):
        name = results[tk]["name"][:12]
        pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
        print(f"{tk:<8} {name:<14} {atr:>6.1%} {pf_str:>7} {trades:>6} {pnl:>+10,.0f}")

    print()
    print("=" * 70)
    print(" 분석 3: PF 상위/하위 10의 ATR 범위")
    print("=" * 70)
    by_pf = sorted(old41_rows, key=lambda x: -x[2])
    top10 = by_pf[:10]
    bot10 = by_pf[-10:]
    top_atrs = [a[1] for a in top10]
    bot_atrs = [a[1] for a in bot10]
    print(f"  PF 상위 10: ATR {min(top_atrs):.1%} ~ {max(top_atrs):.1%} (평균 {statistics.mean(top_atrs):.1%})")
    print(f"  PF 하위 10: ATR {min(bot_atrs):.1%} ~ {max(bot_atrs):.1%} (평균 {statistics.mean(bot_atrs):.1%})")

    pfs_only = [a[2] for a in old41_rows if a[2] != float("inf")]
    atrs_finite = [a[1] for a in old41_rows if a[2] != float("inf")]
    if len(atrs_finite) > 2:
        n = len(atrs_finite)
        ma = sum(atrs_finite) / n
        mp = sum(pfs_only) / n
        cov = sum((a - ma) * (p - mp) for a, p in zip(atrs_finite, pfs_only)) / n
        sa = (sum((a - ma) ** 2 for a in atrs_finite) / n) ** 0.5
        sp = (sum((p - mp) ** 2 for p in pfs_only) / n) ** 0.5
        corr = cov / (sa * sp) if sa * sp > 0 else 0
        print(f"  ATR-PF 상관계수: {corr:.3f}")

    print()
    print("=" * 70)
    print(" 분석 4: 추가 종목 (60종목 중 41종목에 없는 것)")
    print("=" * 70)
    extras = [
        (tk, r["atr"], r["pf"], r["trades"], r["pnl"])
        for tk, r in results.items()
        if not r["in_old41"] and r["atr"] > 0 and r["trades"] > 0
    ]
    extras.sort(key=lambda x: -x[2])
    if extras:
        e_atrs = [a[1] for a in extras]
        e_pfs = [a[2] for a in extras if a[2] != float("inf")]
        pf_below_1 = sum(1 for a in extras if a[2] < 1.0)
        print(f"  종목 수: {len(extras)}")
        print(f"  ATR: {min(e_atrs):.1%} ~ {max(e_atrs):.1%} (평균 {statistics.mean(e_atrs):.1%})")
        if e_pfs:
            print(f"  PF < 1: {pf_below_1}/{len(extras)}")
            print(f"  평균 PF: {statistics.mean(e_pfs):.2f}")
        print()
        print(f"  {'ticker':<8} {'ATR':>7} {'PF':>7} {'trades':>6} {'PnL':>10}")
        print("  " + "-" * 42)
        for tk, atr, pf, trades, pnl in extras:
            pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
            print(f"  {tk:<8} {atr:>6.1%} {pf_str:>7} {trades:>6} {pnl:>+10,.0f}")

    print()
    print("=" * 70)
    print(" 분석 5: ATR 밴드별 성과 (전체 60종목)")
    print("=" * 70)
    all_data = [
        (tk, r["atr"], r["pf"], r["trades"], r["pnl"])
        for tk, r in results.items()
        if r["atr"] > 0 and r["trades"] > 0
    ]

    for lo_pct, hi_pct, label in [
        (6, 8, " 6~ 8%"),
        (8, 10, " 8~10%"),
        (10, 15, "10~15%"),
        (15, 50, "15%+  "),
        (6, 10, " 6~10%"),
        (6, 12, " 6~12%"),
        (8, 12, " 8~12%"),
    ]:
        lo = lo_pct / 100
        hi = hi_pct / 100
        band = [d for d in all_data if lo <= d[1] < hi]
        if band:
            b_pfs = [d[2] for d in band if d[2] != float("inf")]
            avg_pf = statistics.mean(b_pfs) if b_pfs else 0
            pf_gt1 = sum(1 for d in band if d[2] > 1.0)
            b_pnl = sum(d[4] for d in band)
            b_trades = sum(d[3] for d in band)
            print(
                f"  ATR {label}: {len(band):>2}종목, "
                f"평균PF={avg_pf:.2f}, PF>1={pf_gt1}/{len(band)}, "
                f"거래={b_trades:>3}, PnL={b_pnl:>+10,.0f}"
            )


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
