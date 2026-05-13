"""scripts/grid_stale_afternoon.py -- stale_exit + afternoon_entry 파라미터 그리드.

Stage 1: stale_exit 파라미터 그리드 (기존 구간)
Stage 2: afternoon_entry 파라미터 그리드 (Stage 1 최적 + 기존 구간)
Stage 3: 최종 조합 기존 + 확장 구간 백테스트

사용:
    python -u scripts/grid_stale_afternoon.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import pickle
import sys
from collections import Counter
from datetime import date
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")

from utils.grid_runner import GridCache, compute_stats, load_candle_cache, run_parallel_grid

OLD_START = "2025-04-01"
OLD_END   = "2026-04-10"
NEW_START = "2026-04-11"
NEW_END   = "2026-05-12"


# ────────────────────────────────────────────────────────────────────────────
# Stage 2 커스텀 워커 (top-level -- ProcessPoolExecutor spawn 필수)
# ────────────────────────────────────────────────────────────────────────────

def _afternoon_worker(args: tuple) -> dict:
    """Stage 2 워커: 표준 백테스트 + 오후 진입(entry_ts.hour >= 12) 별도 통계."""
    config, candles_bytes, market_map_bytes, ticker_to_market, bt_config, params_dict = args

    from loguru import logger as _l
    _l.remove()
    _l.add(sys.stderr, level="WARNING")

    import asyncio as _a
    import pandas as _pd
    from backtest.backtester import Backtester as _BT
    from strategy.momentum_strategy import MomentumStrategy as _MS
    from utils.grid_runner import compute_stats as _stats

    candles: dict = pickle.loads(candles_bytes)
    market_map: dict = pickle.loads(market_map_bytes)

    all_trades: list[dict] = []
    for tk, df in candles.items():
        market = ticker_to_market.get(tk, "unknown")
        bt = _BT(
            db=None, config=config, backtest_config=bt_config,
            ticker_market=market, market_strong_by_date=market_map,
        )
        strat = _MS(config)
        result = _a.run(bt.run_multi_day_cached(tk, df, strat))
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)

    stats = _stats(all_trades)

    # 오후 진입 건만 분리 (entry_ts 기준)
    aft = [
        t for t in all_trades
        if _pd.to_datetime(t["entry_ts"]).hour >= 12
    ]
    aft_st = _stats(aft)

    return {
        **params_dict,
        **stats,
        "aft_count": aft_st["trades"],
        "aft_pf": aft_st["pf"],
        "aft_pnl": aft_st["pnl"],
    }


# ────────────────────────────────────────────────────────────────────────────
# 공통 유틸
# ────────────────────────────────────────────────────────────────────────────

def _fmt_row(r: dict, keys: list[str], widths: list[int]) -> str:
    """컬럼 키 목록 + 폭 목록으로 한 행 포맷."""
    parts = []
    for k, w in zip(keys, widths):
        v = r.get(k, "")
        if isinstance(v, float):
            parts.append(f"{v:>{w}.3f}")
        elif isinstance(v, int):
            parts.append(f"{v:>{w},}")
        else:
            parts.append(f"{str(v):>{w}}")
    return "  ".join(parts)


async def _run_period_stats(cache: GridCache) -> dict:
    """전 종목 순차 백테스트 -- stats dict."""
    from backtest.backtester import Backtester
    from strategy.momentum_strategy import MomentumStrategy

    all_trades: list[dict] = []
    for tk, df in cache.candles.items():
        market = cache.ticker_to_market.get(tk, "unknown")
        bt = Backtester(
            db=None, config=cache.base_config, backtest_config=cache.bt_config,
            ticker_market=market, market_strong_by_date=cache.market_map,
        )
        strat = MomentumStrategy(cache.base_config)
        result = await bt.run_multi_day_cached(tk, df, strat)
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)
    return compute_stats(all_trades)


# ────────────────────────────────────────────────────────────────────────────
# Stage 1: stale_exit 그리드
# ────────────────────────────────────────────────────────────────────────────

def build_stale_combos() -> list[dict]:
    check_minutes_vals = [20, 30, 45, 60]
    min_profit_vals = [0.003, 0.005, 0.008, 0.01]
    combos = [{"stale": False}]  # baseline (비활성)
    for cm, mp in product(check_minutes_vals, min_profit_vals):
        combos.append({
            "stale": True,
            "stale_position_check_minutes": cm,
            "stale_position_min_profit": mp,
        })
    return combos


def stale_config_factory(p: dict, base) -> object:
    enabled = p.get("stale", False)
    kwargs = dict(
        stale_position_exit_enabled=enabled,
        stale_position_check_minutes=p.get("stale_position_check_minutes",
                                            base.stale_position_check_minutes),
        stale_position_min_profit=p.get("stale_position_min_profit",
                                         base.stale_position_min_profit),
    )
    return dataclasses.replace(base, **kwargs)


def run_stage1(old_cache: GridCache) -> tuple[dict, dict]:
    """Stage 1 실행. (baseline_stats, best_stale_params) 반환."""
    combos = build_stale_combos()
    print(f"\n{'='*68}")
    print(f" Stage 1: stale_exit 그리드  ({len(combos)-1}조합 + baseline)")
    print(f"{'='*68}", flush=True)

    df = run_parallel_grid(combos, stale_config_factory, old_cache)

    # exit_counts에서 stale_count 추출
    df["stale_count"] = df["exit_counts"].apply(lambda x: x.get("stale_exit", 0))

    # baseline row (stale=False)
    base_row = df[df["stale"] == False].iloc[0]
    base_pf = base_row["pf"]
    base_fc = base_row["fc_pct"]

    # 출력
    print(f"\n{'min':>4} {'profit':>8} {'trades':>7} {'PF':>7} {'PnL':>11} "
          f"{'fc%':>6} {'stale#':>7} {'pass':>5}", flush=True)
    print("-" * 70, flush=True)

    # baseline 먼저
    print(
        f"{'BASE':>4} {'baseline':>8} {int(base_row['trades']):>7,} "
        f"{base_row['pf']:>7.3f} {int(base_row['pnl']):>+11,} "
        f"{base_row['fc_pct']:>6.1f} {int(base_row['stale_count']):>7}",
        flush=True,
    )

    active = df[df["stale"] == True].copy()
    active["_pass"] = (
        (active["pf"] >= base_pf * 0.95) &
        (active["fc_pct"] < base_fc)
    )
    for _, r in active.sort_values(["stale_position_check_minutes",
                                     "stale_position_min_profit"]).iterrows():
        flag = "PASS" if r["_pass"] else ""
        print(
            f"{int(r['stale_position_check_minutes']):>4} "
            f"{r['stale_position_min_profit']:>8.3f} "
            f"{int(r['trades']):>7,} {r['pf']:>7.3f} "
            f"{int(r['pnl']):>+11,} {r['fc_pct']:>6.1f} "
            f"{int(r['stale_count']):>7}  {flag}",
            flush=True,
        )

    # 최적 선정
    passed = active[active["_pass"]]
    if passed.empty:
        print("\n[WARN] PASS 조합 없음 -- stale_exit 비활성 유지", flush=True)
        best_params = {"stale": False}
    else:
        best = passed.loc[passed["pnl"].idxmax()]
        best_params = {
            "stale": True,
            "stale_position_check_minutes": int(best["stale_position_check_minutes"]),
            "stale_position_min_profit": float(best["stale_position_min_profit"]),
        }
        print(
            f"\n[BEST stale_exit] check_min={best_params['stale_position_check_minutes']}, "
            f"min_profit={best_params['stale_position_min_profit']:.3f}  "
            f"PF={best['pf']:.3f}  PnL={int(best['pnl']):+,}  "
            f"fc%={best['fc_pct']:.1f}  stale#={int(best['stale_count'])}",
            flush=True,
        )

    # 보고서 저장
    Path("reports").mkdir(exist_ok=True)
    lines = [
        "# stale_exit 파라미터 그리드",
        "",
        f"기간: {OLD_START} ~ {OLD_END}",
        f"baseline PF: {base_pf:.3f} / fc%: {base_fc:.1f}%",
        "",
        "| check_min | min_profit | trades | PF | PnL | fc% | stale# | pass |",
        "|-----------|-----------|--------|-----|-----|-----|--------|------|",
        f"| baseline | - | {int(base_row['trades'])} | {base_pf:.3f} | "
        f"{int(base_row['pnl']):+,} | {base_fc:.1f}% | 0 | - |",
    ]
    for _, r in active.sort_values("pnl", ascending=False).iterrows():
        flag = "PASS" if r["_pass"] else ""
        lines.append(
            f"| {int(r['stale_position_check_minutes'])} "
            f"| {r['stale_position_min_profit']:.3f} "
            f"| {int(r['trades'])} | {r['pf']:.3f} | {int(r['pnl']):+,} "
            f"| {r['fc_pct']:.1f}% | {int(r['stale_count'])} | {flag} |"
        )
    if not passed.empty:
        best_r = passed.loc[passed["pnl"].idxmax()]
        lines += [
            "",
            f"**최적**: check_min={int(best_r['stale_position_check_minutes'])}, "
            f"min_profit={float(best_r['stale_position_min_profit']):.3f}",
        ]
    Path("reports/stale_exit_grid.md").write_text("\n".join(lines), encoding="utf-8")
    print("[SAVED] reports/stale_exit_grid.md", flush=True)

    return {"pf": base_pf, "fc_pct": base_fc, "pnl": int(base_row["pnl"])}, best_params


# ────────────────────────────────────────────────────────────────────────────
# Stage 2: afternoon_entry 그리드
# ────────────────────────────────────────────────────────────────────────────

def build_afternoon_combos(best_stale: dict) -> list[dict]:
    ae_end_vals    = ["13:00", "14:00"]
    ae_bp_vals     = [0.05, 0.07]
    ae_vr_vals     = [2.5, 3.0]
    combos = [{**best_stale, "afternoon": False}]  # baseline
    for ae_end, ae_bp, ae_vr in product(ae_end_vals, ae_bp_vals, ae_vr_vals):
        combos.append({
            **best_stale,
            "afternoon": True,
            "afternoon_end": ae_end,
            "afternoon_min_breakout_pct": ae_bp,
            "afternoon_min_volume_ratio": ae_vr,
        })
    return combos


def afternoon_config_factory(p: dict, base) -> object:
    kwargs = dict(
        stale_position_exit_enabled=p.get("stale", False),
        stale_position_check_minutes=p.get("stale_position_check_minutes",
                                            base.stale_position_check_minutes),
        stale_position_min_profit=p.get("stale_position_min_profit",
                                         base.stale_position_min_profit),
        afternoon_entry_enabled=p.get("afternoon", False),
        afternoon_end=p.get("afternoon_end", base.afternoon_end),
        afternoon_min_breakout_pct=p.get("afternoon_min_breakout_pct",
                                          base.afternoon_min_breakout_pct),
        afternoon_min_volume_ratio=p.get("afternoon_min_volume_ratio",
                                          base.afternoon_min_volume_ratio),
    )
    return dataclasses.replace(base, **kwargs)


def run_stage2(old_cache: GridCache, best_stale: dict) -> dict:
    """Stage 2 실행. best_afternoon_params 반환."""
    combos = build_afternoon_combos(best_stale)
    print(f"\n{'='*68}")
    print(f" Stage 2: afternoon_entry 그리드  ({len(combos)-1}조합 + baseline)")
    print(f"{'='*68}", flush=True)

    df = run_parallel_grid(
        combos,
        afternoon_config_factory,
        old_cache,
        worker_fn=_afternoon_worker,
    )

    base_row = df[df["afternoon"] == False].iloc[0]
    base_pf = base_row["pf"]

    print(f"\n{'end':>6} {'bp':>5} {'vr':>5} {'trades':>7} {'PF':>7} {'PnL':>11} "
          f"{'aft#':>6} {'aft_PF':>8} {'pass':>5}", flush=True)
    print("-" * 78, flush=True)
    print(
        f"{'BASE':>6} {'-':>5} {'-':>5} {int(base_row['trades']):>7,} "
        f"{base_row['pf']:>7.3f} {int(base_row['pnl']):>+11,} "
        f"{int(base_row['aft_count']):>6}",
        flush=True,
    )

    active = df[df["afternoon"] == True].copy()
    active["_pass"] = (
        (active["pf"] >= base_pf * 0.95) &
        (active["aft_pf"] >= 1.0)
    )
    for _, r in active.sort_values(["afternoon_end",
                                     "afternoon_min_breakout_pct",
                                     "afternoon_min_volume_ratio"]).iterrows():
        flag = "PASS" if r["_pass"] else ""
        print(
            f"{r['afternoon_end']:>6} {r['afternoon_min_breakout_pct']:>5.0%} "
            f"{r['afternoon_min_volume_ratio']:>5.1f} {int(r['trades']):>7,} "
            f"{r['pf']:>7.3f} {int(r['pnl']):>+11,} "
            f"{int(r['aft_count']):>6} {r['aft_pf']:>8.3f}  {flag}",
            flush=True,
        )

    # 오후 진입 PF 모두 < 1.0 -> 비활성
    all_aft_pf_below1 = (active["aft_pf"] < 1.0).all()
    if all_aft_pf_below1:
        print("\n[판정] 모든 조합 aft_PF < 1.0 -> afternoon_entry 비활성 확정", flush=True)
        best_afternoon = {"afternoon": False}
    else:
        passed = active[active["_pass"]]
        if passed.empty:
            print("\n[WARN] PASS 조합 없음 (PF 기준 미달 또는 aft_PF < 1.0) -> 비활성", flush=True)
            best_afternoon = {"afternoon": False}
        else:
            best = passed.loc[passed["pnl"].idxmax()]
            best_afternoon = {
                "afternoon": True,
                "afternoon_end": best["afternoon_end"],
                "afternoon_min_breakout_pct": float(best["afternoon_min_breakout_pct"]),
                "afternoon_min_volume_ratio": float(best["afternoon_min_volume_ratio"]),
            }
            print(
                f"\n[BEST afternoon] end={best_afternoon['afternoon_end']}, "
                f"bp={best_afternoon['afternoon_min_breakout_pct']:.0%}, "
                f"vr={best_afternoon['afternoon_min_volume_ratio']:.1f}  "
                f"PF={best['pf']:.3f}  aft_PF={best['aft_pf']:.3f}  "
                f"PnL={int(best['pnl']):+,}",
                flush=True,
            )

    # 보고서 저장
    lines = [
        "# afternoon_entry 파라미터 그리드",
        "",
        f"기간: {OLD_START} ~ {OLD_END}",
        f"stale_exit 적용: {best_stale}",
        f"baseline PF: {base_pf:.3f}",
        "",
        "| end | bp | vr | trades | PF | PnL | aft# | aft_PF | pass |",
        "|-----|-----|-----|--------|-----|-----|------|--------|------|",
        f"| baseline | - | - | {int(base_row['trades'])} | {base_pf:.3f} | "
        f"{int(base_row['pnl']):+,} | {int(base_row['aft_count'])} | - | - |",
    ]
    for _, r in active.sort_values("pnl", ascending=False).iterrows():
        flag = "PASS" if r["_pass"] else ""
        lines.append(
            f"| {r['afternoon_end']} | {r['afternoon_min_breakout_pct']:.0%} "
            f"| {r['afternoon_min_volume_ratio']:.1f} | {int(r['trades'])} "
            f"| {r['pf']:.3f} | {int(r['pnl']):+,} "
            f"| {int(r['aft_count'])} | {r['aft_pf']:.3f} | {flag} |"
        )
    if not all_aft_pf_below1 and best_afternoon.get("afternoon"):
        lines += [
            "",
            f"**최적**: end={best_afternoon['afternoon_end']}, "
            f"bp={best_afternoon['afternoon_min_breakout_pct']:.0%}, "
            f"vr={best_afternoon['afternoon_min_volume_ratio']:.1f}",
        ]
    else:
        lines += ["", "**판정**: afternoon_entry 비활성 확정"]
    Path("reports/afternoon_entry_grid.md").write_text("\n".join(lines), encoding="utf-8")
    print("[SAVED] reports/afternoon_entry_grid.md", flush=True)

    return best_afternoon


# ────────────────────────────────────────────────────────────────────────────
# Stage 3: 최종 조합 기존 + 확장 구간
# ────────────────────────────────────────────────────────────────────────────

def build_final_config(best_stale: dict, best_afternoon: dict, base):
    p = {**best_stale, **best_afternoon}
    return afternoon_config_factory(p, base)


async def run_stage3(full_cache: GridCache, best_stale: dict, best_afternoon: dict) -> None:
    """Stage 3: 최종 config로 기존 + 확장 구간 백테스트."""
    print(f"\n{'='*68}")
    print(" Stage 3: 최종 조합 기존 + 확장 구간")
    print(f"  stale_exit : {best_stale}")
    print(f"  afternoon  : {best_afternoon}")
    print(f"{'='*68}", flush=True)

    final_cfg = build_final_config(best_stale, best_afternoon, full_cache.base_config)

    # 기존 구간
    old_cache = GridCache(
        candles=full_cache.filter_dates(OLD_START, OLD_END).candles,
        ticker_to_market=full_cache.ticker_to_market,
        market_map=full_cache.market_map,
        base_config=final_cfg,
        bt_config=full_cache.bt_config,
    )
    print(f"\n[RUN] 기존 구간 ({OLD_START} ~ {OLD_END})...", flush=True)
    old_stats = await _run_period_stats(old_cache)

    # 확장 구간
    new_cache = GridCache(
        candles=full_cache.filter_dates(NEW_START, NEW_END).candles,
        ticker_to_market=full_cache.ticker_to_market,
        market_map=full_cache.market_map,
        base_config=final_cfg,
        bt_config=full_cache.bt_config,
    )
    print(f"[RUN] 확장 구간 ({NEW_START} ~ {NEW_END})...", flush=True)
    new_stats = await _run_period_stats(new_cache)

    print(f"\n{'구간':<12} {'trades':>7} {'PF':>7} {'PnL':>11} {'win%':>7} {'fc%':>6}")
    print("-" * 60)
    print(
        f"{'기존':>12} {old_stats['trades']:>7,} {old_stats['pf']:>7.3f} "
        f"{old_stats['pnl']:>+11,} {old_stats['win_rate']:>7.1%} {old_stats['fc_pct']:>6.1f}%"
    )
    print(
        f"{'확장':>12} {new_stats['trades']:>7,} {new_stats['pf']:>7.3f} "
        f"{new_stats['pnl']:>+11,} {new_stats['win_rate']:>7.1%} {new_stats['fc_pct']:>6.1f}%"
    )

    _print_claude_md_hint(old_stats, new_stats, best_stale, best_afternoon)


def _print_claude_md_hint(old: dict, new: dict, stale: dict, aft: dict) -> None:
    stale_line = (
        f"stale_exit({stale['stale_position_check_minutes']}min,"
        f"{stale['stale_position_min_profit']:.3f})"
        if stale.get("stale") else "stale_exit=off"
    )
    aft_line = (
        f"afternoon(end={aft['afternoon_end']},"
        f"bp={aft['afternoon_min_breakout_pct']:.0%},"
        f"vr={aft['afternoon_min_volume_ratio']:.1f})"
        if aft.get("afternoon") else "afternoon_entry=off"
    )
    exits_old = old["exit_counts"]
    n = old["trades"]
    exit_dist = " / ".join(
        f"{r} {cnt} ({cnt/n*100:.1f}%)"
        for r, cnt in sorted(exits_old.items(), key=lambda x: -x[1])
    )
    print(f"""
=== CLAUDE.md 갱신 제안 ===
  기존 구간: PF {old['pf']:.2f} / {old['trades']}건 / PnL {old['pnl']:+,}
  확장 구간: PF {new['pf']:.2f} / {new['trades']}건 / PnL {new['pnl']:+,}
  파라미터 : {stale_line} + {aft_line}
  청산 분포: {exit_dist}
==========================""", flush=True)


async def main(skip_stage1: bool = False) -> None:
    print("=" * 68, flush=True)
    print(" stale_exit + afternoon_entry 그리드 (3단계)", flush=True)
    print(f" 기존 구간: {OLD_START}~{OLD_END}  확장: {NEW_START}~{NEW_END}", flush=True)
    print("=" * 68, flush=True)

    # 전 기간 캔들 1회 로드
    full_cache = await load_candle_cache("2025-04-01", "2026-05-12")
    old_cache = full_cache.filter_dates(OLD_START, OLD_END)

    # Stage 1
    if skip_stage1:
        print("\n[SKIP] Stage 1 생략 -- stale_exit 비활성으로 고정", flush=True)
        # Stage 1 결과: 모든 조합 PF < baseline*0.95 -> 비활성
        baseline_stats = {"pf": 4.817, "fc_pct": 39.3, "pnl": 293532}
        best_stale: dict = {"stale": False}
    else:
        baseline_stats, best_stale = run_stage1(old_cache)

    # Stage 2
    best_afternoon = run_stage2(old_cache, best_stale)

    # Stage 3
    await run_stage3(full_cache, best_stale, best_afternoon)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-stage1", action="store_true",
                    help="Stage 1 건너뛰기 (stale_exit 비활성 확정 후 Stage 2/3만 실행)")
    args = ap.parse_args()
    asyncio.run(main(skip_stage1=args.skip_stage1))
