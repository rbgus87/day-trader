"""scripts/analyze_exit_pnl.py — 청산 사유별 PnL 분포 + BE3 효과 분석.

backtest_single.py와 동일한 기간/필터로 baseline(BE3 ON) 한 번, BE3 OFF 한 번
실행해 청산 사유별 평균 PnL과 BE3 효과(70건 흡수분의 PnL 차이)를 정량화한다.

사용:
    python scripts/analyze_exit_pnl.py --start 2025-04-01 --end 2026-04-10
"""

import argparse
import asyncio
import os
import pickle
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import yaml
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))

logger.remove()  # 디버그 폭주 차단

from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig
from data.db_manager import DbManager


def simulate_one(args: tuple) -> dict:
    (
        ticker, ticker_market, candles_pickle,
        trading_config, backtest_config, market_map,
    ) = args

    import asyncio as _asyncio
    from loguru import logger as _logger
    _logger.remove()  # worker 프로세스에서도 로그 차단
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


def collect_trades(kpis: list[dict]) -> list[dict]:
    out: list[dict] = []
    for k in kpis:
        if not k:
            continue
        for t in k.get("trades", []):
            out.append(t)
    return out


def summarize_by_reason(trades: list[dict]) -> dict[str, dict]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        buckets[t.get("exit_reason", "unknown")].append(float(t.get("pnl", 0.0)))
    out: dict[str, dict] = {}
    total = sum(len(v) for v in buckets.values()) or 1
    for reason, pnls in buckets.items():
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        out[reason] = {
            "count": len(pnls),
            "share": len(pnls) / total,
            "sum_pnl": sum(pnls),
            "mean_pnl": sum(pnls) / len(pnls) if pnls else 0.0,
            "win_rate": len(wins) / len(pnls) if pnls else 0.0,
            "gross_profit": sum(wins),
            "gross_loss": abs(sum(losses)),
        }
    return out


async def run_scenario(
    label: str,
    base_config,
    backtest_config,
    candles_cache: dict[str, bytes],
    ticker_to_market: dict[str, str],
    market_map: dict,
    workers: int,
) -> list[dict]:
    print(f"\n[RUN] {label}")
    tasks = [
        (
            tk, ticker_to_market.get(tk, "unknown"),
            candles_cache[tk], base_config, backtest_config, market_map,
        )
        for tk in candles_cache
    ]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        kpis = list(executor.map(simulate_one, tasks))
    return collect_trades(kpis)


def print_table(label: str, summary: dict[str, dict], total_pnl: float) -> None:
    print(f"\n=== {label} ===")
    print(f"{'reason':<18} {'cnt':>5} {'share':>8} {'sum_pnl':>14} {'mean_pnl':>12} {'win%':>7}")
    print("-" * 70)
    order = ["forced_close", "breakeven_stop", "stop_loss",
             "limit_up_exit", "trailing_stop", "tp1_hit"]
    seen = set()
    for r in order:
        if r in summary:
            s = summary[r]
            print(
                f"{r:<18} {s['count']:>5} {s['share']*100:>7.1f}% "
                f"{s['sum_pnl']:>+14,.0f} {s['mean_pnl']:>+12,.0f} "
                f"{s['win_rate']*100:>6.1f}%"
            )
            seen.add(r)
    for r, s in summary.items():
        if r in seen:
            continue
        print(
            f"{r:<18} {s['count']:>5} {s['share']*100:>7.1f}% "
            f"{s['sum_pnl']:>+14,.0f} {s['mean_pnl']:>+12,.0f} "
            f"{s['win_rate']*100:>6.1f}%"
        )
    print("-" * 70)
    print(f"총 PnL: {total_pnl:+,.0f}")


def match_be3_to_d0(
    be3_trades: list[dict], d0_trades: list[dict],
) -> list[tuple[dict, dict | None]]:
    """BE3 trades 중 breakeven_stop만 추출해 D0의 동일 entry_ts 거래에 매칭."""
    d0_index: dict[tuple[str, str], dict] = {}
    for t in d0_trades:
        key = (str(t.get("entry_ts")), str(t.get("entry_price")))
        d0_index[key] = t
    pairs = []
    for t in be3_trades:
        if t.get("exit_reason") != "breakeven_stop":
            continue
        key = (str(t.get("entry_ts")), str(t.get("entry_price")))
        pairs.append((t, d0_index.get(key)))
    return pairs


async def main(start: str, end: str) -> int:
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
    bt_loader = Backtester(db=db, config=base_config, backtest_config=backtest_config)

    print("=" * 70)
    print(" 청산 사유별 PnL 분포 + BE3 효과 (baseline vs BE3 OFF)")
    print("=" * 70)
    print(f"  기간   : {start} ~ {end}")
    print(f"  종목수 : {len(stocks)}")

    candles_cache: dict[str, bytes] = {}
    for s in stocks:
        tk = s["ticker"]
        candles = await bt_loader.load_candles(tk, start, f"{end} 23:59:59")
        if not candles.empty:
            candles_cache[tk] = pickle.dumps(candles)
    print(f"  캔들   : {len(candles_cache)}/{len(stocks)} 로드")
    await db.close()

    market_map = build_market_strong_by_date(
        app_config.db_path, ma_length=base_config.market_ma_length
    )

    workers = max(2, (os.cpu_count() or 2) - 1)
    print(f"  워커   : {workers}")

    # 1) baseline (BE3 ON)
    be3_trades = await run_scenario(
        "baseline (BE3 ON)", base_config, backtest_config,
        candles_cache, ticker_to_market, market_map, workers,
    )
    be3_summary = summarize_by_reason(be3_trades)
    be3_total = sum(t["pnl"] for t in be3_trades)
    print_table("baseline (BE3 ON)", be3_summary, be3_total)

    # 2) BE3 OFF
    d0_config = replace(base_config, breakeven_enabled=False)
    d0_trades = await run_scenario(
        "BE3 OFF", d0_config, backtest_config,
        candles_cache, ticker_to_market, market_map, workers,
    )
    d0_summary = summarize_by_reason(d0_trades)
    d0_total = sum(t["pnl"] for t in d0_trades)
    print_table("BE3 OFF", d0_summary, d0_total)

    # 3) BE3 70건 → BE3 OFF 동일 거래 매칭
    print("\n=== BE3 흡수분 추적 (BE3 ON breakeven_stop ↔ BE3 OFF 동일 거래) ===")
    pairs = match_be3_to_d0(be3_trades, d0_trades)
    matched = [(b, d) for b, d in pairs if d is not None]
    unmatched = [b for b, d in pairs if d is None]
    print(f"  BE3 발동(breakeven_stop): {len(pairs)}건")
    print(f"  D0 매칭 성공:             {len(matched)}건")
    print(f"  D0 매칭 실패:             {len(unmatched)}건 (BE3 발동으로만 발생한 신규 거래일 수 있음)")
    if matched:
        sum_be3 = sum(b["pnl"] for b, _ in matched)
        sum_d0 = sum(d["pnl"] for _, d in matched)
        delta = sum_d0 - sum_be3
        print(f"  매칭 BE3 PnL 합:  {sum_be3:+,.0f}  (mean {sum_be3/len(matched):+,.0f})")
        print(f"  매칭 D0  PnL 합:  {sum_d0:+,.0f}  (mean {sum_d0/len(matched):+,.0f})")
        print(f"  delta(D0 - BE3):  {delta:+,.0f}   (BE3 없었을 때의 PnL 변화)")
        # exit_reason 분포 (D0 측)
        d0_reasons: dict[str, int] = defaultdict(int)
        for _, d in matched:
            d0_reasons[d.get("exit_reason", "unknown")] += 1
        print("  D0 측 청산 사유 분포:")
        for r, c in sorted(d0_reasons.items(), key=lambda x: -x[1]):
            print(f"    {r:<18} {c:>4}")

    # 4) 전체 비교
    print("\n=== 전체 비교 ===")
    print(f"  BE3 ON  거래수={len(be3_trades)}  총 PnL={be3_total:+,.0f}")
    print(f"  BE3 OFF 거래수={len(d0_trades)}  총 PnL={d0_total:+,.0f}")
    print(f"  Δ(OFF − ON): {d0_total - be3_total:+,.0f}")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-04-01")
    parser.add_argument("--end", default="2026-04-10")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.start, args.end)))
