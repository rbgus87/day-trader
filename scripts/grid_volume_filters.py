"""scripts/grid_volume_filters.py — volume_by_time_ratio × breakout_volume_surge_ratio 그리드 서치.

기존 baseline PF + 확장 구간(04-11~05-12) PF를 동시 측정해
신규 거래량 필터가 두 구간 모두 개선하는 조합을 탐색한다.

최적화:
- 캔들 데이터를 첫 회만 로드하여 dict 캐시 (DB 반복 조회 제거).
- ProcessPoolExecutor 병렬화 — 조합별 백테스트를 워커에 분산.
- spawn context 명시 (Windows BrokenProcessPool 회피).
- --verify: 필터 비활성 상태에서 baseline(PF 3.73)과 비교 검증.

사용:
    python scripts/grid_volume_filters.py --verify
    python scripts/grid_volume_filters.py
    python scripts/grid_volume_filters.py --start-old 2025-04-01 --end-old 2026-04-10 \\
        --start-new 2026-04-11 --end-new 2026-05-12
"""

import argparse
import asyncio
import dataclasses
import multiprocessing as mp
import os
import pickle
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")

from backtest.backtester import Backtester
from strategy.momentum_strategy import MomentumStrategy


# 그리드 파라미터 (3 × 3 = 9 조합)
VBT_RATIOS = [1.2, 1.5, 2.0]
SURGE_RATIOS = [1.5, 2.0, 3.0]

# baseline 검증 임계 (필터 비활성 시 PF 3.73 ± 0.02)
BASELINE_EXPECTED_PF = 3.727
BASELINE_EXPECTED_TRADES = 247
BASELINE_TOLERANCE_PF = 0.03
BASELINE_TOLERANCE_TRADES = 5

CUT_DATE = date(2026, 4, 11)  # 구간 분할 기준


# ────────────────────────────────────────────────────────────────────────
# Worker — ProcessPoolExecutor에서 실행 (모듈 top-level 정의 필수)
# ────────────────────────────────────────────────────────────────────────

def _worker_run_combo(args: tuple) -> dict:
    """워커: 한 조합 (vbt_ratio, surge_ratio)을 전체 기간 모든 종목에 대해 실행."""
    (vbt_ratio, surge_ratio), candles_bytes, market_map_bytes, ticker_to_market, base_config, bt_config = args

    from loguru import logger as _logger
    _logger.remove()
    _logger.add(sys.stderr, level="WARNING")

    candles_cache = pickle.loads(candles_bytes)
    market_map = pickle.loads(market_map_bytes)

    cfg = dataclasses.replace(
        base_config,
        volume_by_time_enabled=(vbt_ratio is not None),
        volume_by_time_ratio=vbt_ratio if vbt_ratio is not None else 1.5,
        breakout_volume_surge_enabled=(surge_ratio is not None),
        breakout_volume_surge_ratio=surge_ratio if surge_ratio is not None else 2.0,
    )

    import asyncio as _asyncio
    from backtest.backtester import Backtester as _BT
    from strategy.momentum_strategy import MomentumStrategy as _MS

    all_trades: list[dict] = []
    for tk, candles in candles_cache.items():
        market = ticker_to_market.get(tk, "unknown")
        bt = _BT(
            db=None, config=cfg, backtest_config=bt_config,
            ticker_market=market, market_strong_by_date=market_map,
        )
        strategy = _MS(cfg)
        result = _asyncio.run(bt.run_multi_day_cached(tk, candles, strategy))
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)

    return _compute_result(vbt_ratio, surge_ratio, all_trades)


def _compute_result(vbt_ratio, surge_ratio, all_trades: list) -> dict:
    from datetime import date as _date
    import pandas as _pd

    cut = _date(2026, 4, 11)
    old_trades = []
    new_trades = []
    for t in all_trades:
        try:
            ets = t.get("exit_ts")
            if ets is None:
                continue
            d = ets.date() if hasattr(ets, "date") else _pd.to_datetime(ets).date()
            if d < cut:
                old_trades.append(t)
            else:
                new_trades.append(t)
        except Exception:
            old_trades.append(t)

    def _pf(trades):
        gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
        return gp / gl if gl > 0 else float("inf")

    def _pnl(trades):
        return sum(t["pnl"] for t in trades)

    total = len(all_trades)
    return {
        "vbt_ratio": vbt_ratio,
        "surge_ratio": surge_ratio,
        "old_pf": _pf(old_trades),
        "old_trades": len(old_trades),
        "old_pnl": _pnl(old_trades),
        "new_pf": _pf(new_trades),
        "new_trades": len(new_trades),
        "new_pnl": _pnl(new_trades),
        "total_pf": _pf(all_trades),
        "total_trades": total,
        "total_pnl": _pnl(all_trades),
        "exits": dict(Counter(t.get("exit_reason", "?") for t in all_trades)),
    }


# ────────────────────────────────────────────────────────────────────────
# 데이터 로드
# ────────────────────────────────────────────────────────────────────────

async def load_candles_and_market(start: str, end: str):
    from config.settings import AppConfig
    from utils.grid_runner import load_candle_cache
    cache = await load_candle_cache(start, end)
    # 신규 필터 비활성 (그리드에서 override)
    base_config_disabled = dataclasses.replace(
        cache.base_config,
        volume_by_time_enabled=False,
        breakout_volume_surge_enabled=False,
    )
    app_config = AppConfig.from_yaml()
    return (
        base_config_disabled,
        cache.bt_config,
        cache.candles,
        cache.ticker_to_market,
        cache.market_map,
        app_config.db_path,
    )


# ────────────────────────────────────────────────────────────────────────
# 검증 모드
# ────────────────────────────────────────────────────────────────────────

async def run_verify(start: str, end: str):
    print("=" * 64)
    print(f" [VERIFY] 필터 비활성 baseline 검증 ({start} ~ {end})")
    print("=" * 64)

    base_config, bt_config, candles_cache, ticker_to_market, market_map, _ = \
        await load_candles_and_market(start, end)

    all_trades: list = []
    for tk, candles in candles_cache.items():
        market = ticker_to_market.get(tk, "unknown")
        bt = Backtester(
            db=None, config=base_config, backtest_config=bt_config,
            ticker_market=market, market_strong_by_date=market_map,
        )
        strategy = MomentumStrategy(base_config)
        result = await bt.run_multi_day_cached(tk, candles, strategy)
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)

    r = _compute_result(None, None, all_trades)
    pf = r["old_pf"]
    trades = r["old_trades"]
    pnl = r["old_pnl"]

    print(f"\n  old period trades  : {trades}")
    print(f"  old period PF      : {pf:.3f}  (expected {BASELINE_EXPECTED_PF:.3f} ± {BASELINE_TOLERANCE_PF})")
    print(f"  old period PnL     : {pnl:+,.0f}")

    ok_pf = abs(pf - BASELINE_EXPECTED_PF) <= BASELINE_TOLERANCE_PF
    ok_tr = abs(trades - BASELINE_EXPECTED_TRADES) <= BASELINE_TOLERANCE_TRADES
    status = "PASS" if ok_pf and ok_tr else "FAIL"
    print(f"\n  결과: {status}")
    if not ok_pf:
        print(f"  ⚠ PF 차이: {pf:.3f} vs {BASELINE_EXPECTED_PF:.3f}")
    if not ok_tr:
        print(f"  ⚠ 거래 건수 차이: {trades} vs {BASELINE_EXPECTED_TRADES}")


# ────────────────────────────────────────────────────────────────────────
# 그리드 실행
# ────────────────────────────────────────────────────────────────────────

def run_grid(start: str, end: str, workers: int):
    base_config, bt_config, candles_cache, ticker_to_market, market_map, _ = \
        asyncio.run(load_candles_and_market(start, end))

    combos = list(product(VBT_RATIOS, SURGE_RATIOS))
    print(f"\n[GRID] {len(combos)}combos x {len(candles_cache)}tickers -- workers={workers}", flush=True)

    candles_bytes = pickle.dumps(candles_cache)
    market_map_bytes = pickle.dumps(market_map)

    args_list = [
        (combo, candles_bytes, market_map_bytes, ticker_to_market, base_config, bt_config)
        for combo in combos
    ]

    t0 = time.time()
    ctx = mp.get_context("spawn")
    results = []
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        futures = {pool.submit(_worker_run_combo, args): args[0] for args in args_list}
        for i, future in enumerate(futures, 1):
            combo = futures[future]
            r = future.result()
            results.append(r)
            print(
                f"  [{i}/{len(combos)}] vbt={combo[0]} surge={combo[1]} "
                f"old_PF={r['old_pf']:.3f} new_PF={r['new_pf']:.3f} "
                f"old_tr={r['old_trades']} new_tr={r['new_trades']}",
                flush=True,
            )

    elapsed = time.time() - t0
    print(f"\n[DONE] {elapsed:.1f}초\n")

    _print_table(results)
    _write_report(results, start, end)


def _print_table(results: list[dict]):
    # 기존 구간 PF 기준 내림차순 정렬
    sorted_r = sorted(results, key=lambda x: x["old_pf"], reverse=True)

    hdr = f"{'vbt':>5} {'surge':>6} | {'old_PF':>7} {'old_tr':>7} {'old_PnL':>10} | {'new_PF':>7} {'new_tr':>7} {'new_PnL':>10}"
    print("=" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    for r in sorted_r:
        print(
            f"{r['vbt_ratio']:>5.1f} {r['surge_ratio']:>6.1f} | "
            f"{r['old_pf']:>7.3f} {r['old_trades']:>7d} {r['old_pnl']:>10,.0f} | "
            f"{r['new_pf']:>7.3f} {r['new_trades']:>7d} {r['new_pnl']:>10,.0f}"
        )
    print("=" * len(hdr))

    # 선정 기준: 기존 PF >= 3.5 + 신규 PF 최대
    qualified = [r for r in results if r["old_pf"] >= 3.5 and r["old_pnl"] >= 250_000]
    if qualified:
        best = max(qualified, key=lambda x: x["new_pf"])
        print(f"\n최적 조합 (old PF>=3.5, PnL>=250K, new PF 최대):")
        print(f"  vbt_ratio={best['vbt_ratio']} surge_ratio={best['surge_ratio']}")
        print(f"  old PF={best['old_pf']:.3f} / new PF={best['new_pf']:.3f}")
        print(f"  old PnL={best['old_pnl']:+,.0f} / new PnL={best['new_pnl']:+,.0f}")
    else:
        print("\n선정 기준(old PF>=3.5 + PnL>=250K) 만족 조합 없음")


def _write_report(results: list[dict], start: str, end: str):
    from datetime import datetime
    out = Path("reports/grid_volume_filters.md")
    out.parent.mkdir(exist_ok=True)

    lines = [
        f"# Grid: volume_by_time × breakout_surge ({start} ~ {end})",
        f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "| vbt_ratio | surge_ratio | old_PF | old_tr | old_PnL | new_PF | new_tr | new_PnL |",
        "|-----------|-------------|--------|--------|---------|--------|--------|---------|",
    ]
    for r in sorted(results, key=lambda x: x["old_pf"], reverse=True):
        lines.append(
            f"| {r['vbt_ratio']} | {r['surge_ratio']} "
            f"| {r['old_pf']:.3f} | {r['old_trades']} | {r['old_pnl']:+,.0f} "
            f"| {r['new_pf']:.3f} | {r['new_trades']} | {r['new_pnl']:+,.0f} |"
        )

    qualified = [r for r in results if r["old_pf"] >= 3.5 and r["old_pnl"] >= 250_000]
    if qualified:
        best = max(qualified, key=lambda x: x["new_pf"])
        lines += [
            "",
            "## 최적 조합",
            f"- vbt_ratio: **{best['vbt_ratio']}**",
            f"- surge_ratio: **{best['surge_ratio']}**",
            f"- 기존 구간 PF: {best['old_pf']:.3f} / PnL: {best['old_pnl']:+,.0f}",
            f"- 신규 구간 PF: {best['new_pf']:.3f} / PnL: {best['new_pnl']:+,.0f}",
        ]

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n리포트 저장: {out}")


# ────────────────────────────────────────────────────────────────────────
# Entry
# ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="baseline 검증 (필터 비활성)")
    parser.add_argument("--start-old", default="2025-04-01")
    parser.add_argument("--end-old", default="2026-04-10")
    parser.add_argument("--start-new", default="2026-04-11")
    parser.add_argument("--end-new", default="2026-05-12")
    parser.add_argument("--workers", type=int, default=min(mp.cpu_count(), 9))
    args = parser.parse_args()

    start = args.start_old
    end = args.end_new  # 전체 기간 한 번에 로드

    if args.verify:
        asyncio.run(run_verify(start, args.end_old))
    else:
        run_grid(start, end, args.workers)


if __name__ == "__main__":
    main()
