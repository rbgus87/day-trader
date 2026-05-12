"""scripts/grid_momentum_fade.py — momentum_fade 파라미터 그리드 서치.

3개 파라미터를 격자 탐색하여 PF >= 4.1 + forced_close <= 40% + PnL >= 260K
조건을 만족하는 최적 조합을 찾는다. 단일 프로세스(Windows multiprocessing 회피).

사용:
    python scripts/grid_momentum_fade.py
    python scripts/grid_momentum_fade.py --start 2025-04-01 --end 2026-04-10
"""

import argparse
import asyncio
import dataclasses
import sys
import time
from collections import Counter
from itertools import product
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

# loguru DEBUG/INFO 출력 억제 — 백테스터의 자체 로그가 파일 I/O로 지연 유발
from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")

from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig
from data.db_manager import DbManager
from strategy.momentum_strategy import MomentumStrategy


# 그리드 파라미터 — Stage 1: 2D (threshold × min_profit), min_hold=15 고정
# Stage 2(필요 시): 최적 (threshold, min_profit) × min_hold = [15, 20, 30]
# 사유: 1조합 ~8분, 36조합 ~5h 실행 비현실적. 2단계로 분리.
THRESHOLDS = [-0.005, -0.008, -0.010, -0.015]
MIN_PROFITS = [0.01, 0.02, 0.03]
MIN_HOLDS = [15]  # Stage 1 고정값


async def load_candles_and_market(start: str, end: str):
    """캔들과 market_map을 한 번만 로드 — 모든 조합에서 재사용."""
    app_config = AppConfig.from_yaml()
    base_config = app_config.trading
    bt_cfg_raw = yaml.safe_load(
        open("config.yaml", encoding="utf-8")
    ).get("backtest", {})
    backtest_config = BacktestConfig(
        commission=bt_cfg_raw.get("commission", 0.00015),
        tax=bt_cfg_raw.get("tax", 0.0020),
        slippage=bt_cfg_raw.get("slippage", 0.0003),
    )

    uni = yaml.safe_load(open("config/universe_backtest.yaml", encoding="utf-8"))
    stocks = uni.get("stocks", [])
    ticker_to_market = {s["ticker"]: s.get("market", "unknown") for s in stocks}

    db = DbManager(app_config.db_path)
    await db.init()
    bt_loader = Backtester(db=db, config=base_config, backtest_config=backtest_config)

    print(f"[LOAD] candles ({len(stocks)} stocks)...")
    candles_cache: dict = {}
    for i, s in enumerate(stocks, 1):
        tk = s["ticker"]
        candles = await bt_loader.load_candles(tk, start, f"{end} 23:59:59")
        if not candles.empty:
            candles_cache[tk] = candles
        if i % 10 == 0:
            print(f"  loaded {i}/{len(stocks)}")
    print(f"[LOAD] done {len(candles_cache)}/{len(stocks)}")
    await db.close()

    market_map = build_market_strong_by_date(
        app_config.db_path, ma_length=base_config.market_ma_length
    )

    return base_config, backtest_config, candles_cache, ticker_to_market, market_map


async def run_one_combo(
    base_config, backtest_config, candles_cache, ticker_to_market, market_map,
    threshold: float, min_profit: float, min_hold: int,
) -> dict:
    """한 조합 실행 -> 집계."""
    cfg = dataclasses.replace(
        base_config,
        momentum_fade_threshold=threshold,
        momentum_fade_min_profit=min_profit,
        momentum_fade_min_hold_min=min_hold,
    )

    all_trades = []
    for tk, candles in candles_cache.items():
        market = ticker_to_market.get(tk, "unknown")
        bt = Backtester(
            db=None, config=cfg, backtest_config=backtest_config,
            ticker_market=market, market_strong_by_date=market_map,
        )
        strategy = MomentumStrategy(cfg)
        result = await bt.run_multi_day_cached(tk, candles, strategy)
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)

    total = len(all_trades)
    if total == 0:
        return {
            "threshold": threshold, "min_profit": min_profit, "min_hold": min_hold,
            "pf": 0.0, "total_pnl": 0, "trades": 0,
            "exits": {}, "forced_close_pct": 0.0, "fade_count": 0,
        }
    gp = sum(t["pnl"] for t in all_trades if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in all_trades if t["pnl"] < 0))
    pf = gp / gl if gl > 0 else float("inf")
    pnl = sum(t["pnl"] for t in all_trades)
    exits = Counter(t.get("exit_reason", "?") for t in all_trades)
    fc = exits.get("forced_close", 0)
    fade = exits.get("momentum_fade", 0)

    return {
        "threshold": threshold,
        "min_profit": min_profit,
        "min_hold": min_hold,
        "pf": pf,
        "total_pnl": pnl,
        "trades": total,
        "exits": dict(exits),
        "forced_close_pct": fc / total * 100,
        "fade_count": fade,
    }


def select_best(results: list[dict]) -> dict | None:
    """선정 기준 (우선순위):
      1. PF >= 4.1
      2. forced_close <= 40%
      3. total PnL >= 260_000
      4. 위 모두 만족 중 momentum_fade 건수가 가장 적은 것
    """
    qualified = [
        r for r in results
        if r["pf"] >= 4.1
        and r["forced_close_pct"] <= 40.0
        and r["total_pnl"] >= 260_000
    ]
    if not qualified:
        return None
    return min(qualified, key=lambda r: r["fade_count"])


def write_report(results: list[dict], best: dict | None, out_path: Path):
    """reports/momentum_fade_grid.md 작성."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    lines.append("# Momentum Fade Grid Search 결과\n")
    lines.append(f"> 생성: {time.strftime('%Y-%m-%d %H:%M')}\n")
    lines.append("## 선정 기준 (우선순위)\n")
    lines.append("1. PF >= 4.1")
    lines.append("2. forced_close <= 40%")
    lines.append("3. 총 PnL >= 260,000")
    lines.append("4. 위 모두 만족 중 momentum_fade 건수 최소\n")
    lines.append("## 전체 36개 조합\n")
    lines.append(
        "| threshold | min_profit | min_hold | PF | trades | total PnL | "
        "forced_close% | momentum_fade | 통과 |"
    )
    lines.append("|-----------|-----------|----------|-----|--------|----------|--------------|--------------|------|")
    # PF 내림차순 정렬
    sorted_r = sorted(results, key=lambda r: r["pf"], reverse=True)
    for r in sorted_r:
        passed = (
            r["pf"] >= 4.1
            and r["forced_close_pct"] <= 40.0
            and r["total_pnl"] >= 260_000
        )
        marker = "PASS" if passed else ""
        lines.append(
            f"| {r['threshold']:+.3f} | {r['min_profit']:.2f} | {r['min_hold']} | "
            f"{r['pf']:.2f} | {r['trades']} | {r['total_pnl']:+,.0f} | "
            f"{r['forced_close_pct']:.1f}% | {r['fade_count']} | {marker} |"
        )
    lines.append("")
    if best is not None:
        lines.append("## 선정 최적 조합\n")
        lines.append(
            f"- threshold: **{best['threshold']:+.3f}**\n"
            f"- min_profit: **{best['min_profit']:.2f}**\n"
            f"- min_hold: **{best['min_hold']}**\n"
            f"- PF: **{best['pf']:.2f}**\n"
            f"- trades: {best['trades']}\n"
            f"- total PnL: {best['total_pnl']:+,.0f}\n"
            f"- forced_close: {best['forced_close_pct']:.1f}%\n"
            f"- momentum_fade: {best['fade_count']}건"
        )
        lines.append("")
        lines.append("### 청산 분포\n")
        for reason, cnt in sorted(best["exits"].items(), key=lambda x: -x[1]):
            pct = cnt / best["trades"] * 100
            lines.append(f"- {reason}: {cnt} ({pct:.1f}%)")
    else:
        lines.append("## 선정 결과: 모든 조합이 기준 미달\n")
        lines.append("기준을 완화하거나 time_decay 등 다른 파라미터 조정 필요.\n")
        lines.append("### PF 상위 5개 (참고용)\n")
        for r in sorted_r[:5]:
            lines.append(
                f"- threshold={r['threshold']:+.3f}, min_profit={r['min_profit']:.2f}, "
                f"min_hold={r['min_hold']} -> PF {r['pf']:.2f}, "
                f"forced_close {r['forced_close_pct']:.1f}%, "
                f"PnL {r['total_pnl']:+,.0f}, fade {r['fade_count']}"
            )
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[REPORT] {out_path} written")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-04-01")
    parser.add_argument("--end", default="2026-04-10")
    args = parser.parse_args()

    print("=" * 70)
    print(" Momentum Fade Grid Search (4 x 3 x 3 = 36 combinations)")
    print(f" Period: {args.start} ~ {args.end}")
    print("=" * 70)

    base_config, bt_cfg, candles_cache, ticker_market, market_map = (
        await load_candles_and_market(args.start, args.end)
    )

    combos = list(product(THRESHOLDS, MIN_PROFITS, MIN_HOLDS))
    print(f"\n[GRID] {len(combos)} combinations x {len(candles_cache)} tickers")
    print(f"[GRID] estimated runtime ~10-30 min (sequential, no multiprocessing)\n")

    results = []
    t_start = time.time()
    for i, (thr, mp, mh) in enumerate(combos, 1):
        r = await run_one_combo(
            base_config, bt_cfg, candles_cache, ticker_market, market_map,
            thr, mp, mh,
        )
        results.append(r)
        elapsed = time.time() - t_start
        eta = elapsed / i * (len(combos) - i)
        print(
            f"[{i:>2}/{len(combos)}] thr={thr:+.3f} mp={mp:.2f} mh={mh} -> "
            f"PF={r['pf']:.2f} PnL={r['total_pnl']:+,.0f} "
            f"fc%={r['forced_close_pct']:.1f} fade={r['fade_count']} "
            f"(elapsed {elapsed:.0f}s, ETA {eta:.0f}s)"
        )

    best = select_best(results)
    write_report(results, best, Path("reports/momentum_fade_grid.md"))

    if best:
        print("\n" + "=" * 70)
        print(" BEST")
        print("=" * 70)
        print(f"  threshold:  {best['threshold']:+.3f}")
        print(f"  min_profit: {best['min_profit']:.2f}")
        print(f"  min_hold:   {best['min_hold']}")
        print(f"  PF:         {best['pf']:.2f}")
        print(f"  PnL:        {best['total_pnl']:+,.0f}")
        print(f"  forced_close: {best['forced_close_pct']:.1f}%")
        print(f"  fade count: {best['fade_count']}")
    else:
        print("\n[WARN] 기준 만족 조합 없음 -- 보고서 PF 상위 참고")


if __name__ == "__main__":
    asyncio.run(main())
