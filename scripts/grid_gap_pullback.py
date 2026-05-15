"""scripts/grid_gap_pullback.py — 갭업 눌림목 전략 27조합 그리드 서치.

gap_pullback_min_pct:         [0.02, 0.03, 0.04]
gap_pullback_min_pullback_pct: [0.005, 0.01, 0.015]
gap_pullback_force_close:     ["09:30", "09:45", "10:00"]

기존 구간(OLD: 2025-04-01~2026-04-10) + 확장 구간(NEW: 2026-04-11~2026-05-12)

각 조합별 측정:
  - 갭 단독 PF / PnL / 거래수 / 승률
  - 모멘텀 + 갭 합산 PF / PnL / 거래수

선정 기준 (OLD 구간 기준):
  1. 갭 단독 PF >= 1.5
  2. 합산 PF >= 4.637  (baseline 4.881 × 0.95)
  3. 갭 거래수 >= 10건

결과: reports/gap_pullback_grid.md
최적 조합 → config.yaml 갱신 (gap_pullback 섹션)

사용:
    python -u scripts/grid_gap_pullback.py          # 전체 실행
    python -u scripts/grid_gap_pullback.py --verify # baseline PF 재현만
    python -u scripts/grid_gap_pullback.py --no-update  # config.yaml 갱신 건너뜀
"""
from __future__ import annotations

import asyncio
import dataclasses
import multiprocessing as mp
import os
import pickle
import re
import sys
import time as _time
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")

from utils.grid_runner import GridCache, compute_stats, load_candle_cache

# ---------------------------------------------------------------------------
# 날짜 구간
# ---------------------------------------------------------------------------

OLD_START = "2025-04-01"
OLD_END   = "2026-04-10"
NEW_START = "2026-04-11"
NEW_END   = "2026-05-12"

# ---------------------------------------------------------------------------
# 그리드 파라미터
# ---------------------------------------------------------------------------

GAP_MIN_VALS       = [0.02, 0.03, 0.04]
PULLBACK_MIN_VALS  = [0.005, 0.01, 0.015]
FORCE_CLOSE_VALS   = ["09:30", "09:45", "10:00"]

# ---------------------------------------------------------------------------
# 선정 기준
# ---------------------------------------------------------------------------

GAP_PF_THRESHOLD   = 1.5
COMB_PF_THRESHOLD  = 4.637   # baseline 4.881 × 0.95
MIN_GAP_TRADES     = 10


# ---------------------------------------------------------------------------
# 갭 워커 — top-level (ProcessPool spawn 필수)
# ---------------------------------------------------------------------------

def _gap_pullback_worker(args: tuple) -> dict:
    """갭 전략 단독 백테스트 + 모멘텀 baseline 합산 통계 반환.

    args:
        (config, candles_bytes, market_map_bytes, ticker_to_market,
         bt_config, params_dict, baseline_trades_bytes)
    """
    (config, candles_bytes, market_map_bytes,
     ticker_to_market, bt_config, params_dict,
     baseline_trades_bytes) = args

    from loguru import logger as _l
    _l.remove()
    _l.add(sys.stderr, level="WARNING")

    import asyncio as _asyncio
    import pickle as _pickle
    from backtest.backtester import Backtester as _BT
    from strategy.gap_pullback_strategy import GapPullbackStrategy as _GPS
    from utils.grid_runner import compute_stats as _cs

    candles_cache: dict = _pickle.loads(candles_bytes)
    market_map: dict    = _pickle.loads(market_map_bytes)
    baseline_trades: list[dict] = _pickle.loads(baseline_trades_bytes)

    gap_trades: list[dict] = []
    for tk, df in candles_cache.items():
        market = ticker_to_market.get(tk, "unknown")
        bt = _BT(
            db=None, config=config, backtest_config=bt_config,
            ticker_market=market, market_strong_by_date=market_map,
        )
        strat = _GPS(config)
        result = _asyncio.run(bt.run_multi_day_cached(tk, df, strat))
        for t in result.get("trades", []):
            t["ticker"] = tk
            gap_trades.append(t)

    gap_stats  = _cs(gap_trades)
    comb_stats = _cs(baseline_trades + gap_trades)

    return {
        **params_dict,
        **{f"gap_{k}": v  for k, v in gap_stats.items()  if k != "exit_counts"},
        **{f"comb_{k}": v for k, v in comb_stats.items() if k != "exit_counts"},
    }


# ---------------------------------------------------------------------------
# 모멘텀 baseline (async, 메인 프로세스)
# ---------------------------------------------------------------------------

async def _run_momentum_baseline(cache: GridCache) -> list[dict]:
    """MomentumStrategy baseline 거래 목록 반환."""
    from backtest.backtester import Backtester as _BT
    from strategy.momentum_strategy import MomentumStrategy as _MS

    all_trades: list[dict] = []
    for tk, df in cache.candles.items():
        market = cache.ticker_to_market.get(tk, "unknown")
        bt = _BT(
            db=None, config=cache.base_config, backtest_config=cache.bt_config,
            ticker_market=market, market_strong_by_date=cache.market_map,
        )
        strat = _MS(cache.base_config)
        result = await bt.run_multi_day_cached(tk, df, strat)
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)
    return all_trades


# ---------------------------------------------------------------------------
# gap config factory
# ---------------------------------------------------------------------------

def _gap_config_factory(params: dict, base_config) -> object:
    return dataclasses.replace(
        base_config,
        gap_pullback_enabled=True,
        gap_pullback_min_pct=params.get("gap_pullback_min_pct", 0.02),
        gap_pullback_max_pct=0.08,
        gap_pullback_min_pullback_pct=params.get("gap_pullback_min_pullback_pct", 0.01),
        gap_pullback_max_pullback_pct=0.03,
        gap_pullback_force_close=params.get("gap_pullback_force_close", "09:45"),
        gap_pullback_volume_ratio=1.5,
        gap_pullback_atr_stop_mult=0.5,
    )


# ---------------------------------------------------------------------------
# 조합 빌더
# ---------------------------------------------------------------------------

def _build_combos() -> list[dict]:
    combos = []
    for gm in GAP_MIN_VALS:
        for pm in PULLBACK_MIN_VALS:
            for fc in FORCE_CLOSE_VALS:
                tag = f"g{gm:.0%}_pb{pm*100:.1f}_fc{fc.replace(':', '')}"
                combos.append({
                    "tag": tag,
                    "gap_pullback_min_pct": gm,
                    "gap_pullback_min_pullback_pct": pm,
                    "gap_pullback_force_close": fc,
                })
    return combos


# ---------------------------------------------------------------------------
# 병렬 그리드 실행 (커스텀: baseline trades 전달)
# ---------------------------------------------------------------------------

def _run_gap_grid(
    combos: list[dict],
    cache: GridCache,
    baseline_trades: list[dict],
    *,
    max_workers: int | None = None,
) -> list[dict]:
    """갭 전략 그리드를 병렬로 실행하고 결과 list 반환."""
    cache.prepare_bytes()
    baseline_bytes = pickle.dumps(baseline_trades)
    n_workers = max_workers or max(2, min(4, (os.cpu_count() or 4) - 1))

    worker_args = [
        (
            _gap_config_factory(p, cache.base_config),
            cache.candles_bytes,
            cache.market_map_bytes,
            cache.ticker_to_market,
            cache.bt_config,
            p,
            baseline_bytes,
        )
        for p in combos
    ]

    n = len(combos)
    results: list[dict] = []
    t0 = _time.time()
    print(f"[GAP GRID] {n}조합 × {len(cache.candles)}종목  workers={n_workers}", flush=True)

    try:
        from tqdm import tqdm
        _use_tqdm = True
    except ImportError:
        _use_tqdm = False

    ctx = mp.get_context("spawn")
    try:
        from concurrent.futures import ProcessPoolExecutor as _PPE
        with _PPE(max_workers=n_workers, mp_context=ctx) as ex:
            it = ex.map(_gap_pullback_worker, worker_args)
            if _use_tqdm:
                from tqdm import tqdm as _tqdm
                it = _tqdm(it, total=n, desc="gap grid", unit="combo")
            for i, r in enumerate(it, 1):
                results.append(r)
                if not _use_tqdm:
                    elapsed = _time.time() - t0
                    eta = elapsed / i * (n - i) if i < n else 0
                    print(
                        f"  [{i:>3}/{n}] {r.get('tag','?'):<35} "
                        f"gap_pf={r.get('gap_pf', 0):.3f} "
                        f"gap_tr={r.get('gap_trades', 0):>3} "
                        f"comb_pf={r.get('comb_pf', 0):.3f} "
                        f"(ETA {eta:.0f}s)",
                        flush=True,
                    )
    except Exception as exc:
        print(f"[WARN] Pool 실패 ({exc}), 순차 실행 전환", flush=True)
        results = []
        for i, wargs in enumerate(worker_args, 1):
            r = _gap_pullback_worker(wargs)
            results.append(r)
            if not _use_tqdm:
                elapsed = _time.time() - t0
                print(
                    f"  [{i:>3}/{n}] {r.get('tag','?'):<35} "
                    f"gap_pf={r.get('gap_pf', 0):.3f} "
                    f"comb_pf={r.get('comb_pf', 0):.3f} "
                    f"({elapsed:.0f}s)",
                    flush=True,
                )

    elapsed = _time.time() - t0
    print(f"[DONE] {n}조합 완료 ({elapsed:.1f}s)", flush=True)
    return results


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------

def _print_table(results: list[dict], title: str) -> None:
    print(f"\n{title}", flush=True)
    hdr = (
        f"{'태그':>35} | "
        f"{'갭PF':>6} {'갭PnL':>10} {'갭#':>4} {'갭win%':>6} | "
        f"{'합PF':>6} {'합PnL':>10} {'합#':>5} {'합win%':>6} | {'OK':>3}"
    )
    sep = "-" * len(hdr)
    print(sep, flush=True)
    print(hdr, flush=True)
    print(sep, flush=True)
    for r in sorted(results, key=lambda x: x.get("comb_pf", 0.0), reverse=True):
        tag  = r.get("tag", "?")
        gpf  = r.get("gap_pf", 0.0)
        gpnl = int(r.get("gap_pnl", 0))
        gtr  = int(r.get("gap_trades", 0))
        gwin = r.get("gap_win_rate", 0.0)
        cpf  = r.get("comb_pf", 0.0)
        cpnl = int(r.get("comb_pnl", 0))
        ctr  = int(r.get("comb_trades", 0))
        cwin = r.get("comb_win_rate", 0.0)
        ok = "Y" if (
            gpf >= GAP_PF_THRESHOLD
            and cpf >= COMB_PF_THRESHOLD
            and gtr >= MIN_GAP_TRADES
        ) else ""
        print(
            f"{tag:>35} | "
            f"{gpf:>6.3f} {gpnl:>+10,} {gtr:>4} {gwin:>6.1%} | "
            f"{cpf:>6.3f} {cpnl:>+10,} {ctr:>5} {cwin:>6.1%} | {ok:>3}",
            flush=True,
        )
    print(sep, flush=True)


def _select_best(results: list[dict]) -> dict | None:
    """선정 기준 통과 조합 중 합산 PnL 최대."""
    candidates = [
        r for r in results
        if (
            r.get("gap_pf", 0.0) >= GAP_PF_THRESHOLD
            and r.get("comb_pf", 0.0) >= COMB_PF_THRESHOLD
            and int(r.get("gap_trades", 0)) >= MIN_GAP_TRADES
        )
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r.get("comb_pnl", 0))


# ---------------------------------------------------------------------------
# 보고서 저장
# ---------------------------------------------------------------------------

def _write_report(
    old_results: list[dict],
    new_results: list[dict],
    old_baseline: dict,
    new_baseline: dict,
    best: dict | None,
) -> None:
    out = Path("reports/gap_pullback_grid.md")
    out.parent.mkdir(exist_ok=True)

    cols_gap  = ["gap_pullback_min_pct", "gap_pullback_min_pullback_pct",
                 "gap_pullback_force_close", "gap_pf", "gap_pnl",
                 "gap_trades", "gap_win_rate"]
    cols_comb = ["comb_pf", "comb_pnl", "comb_trades", "comb_win_rate"]
    all_cols  = ["tag"] + cols_gap + cols_comb

    def _md_rows(results: list[dict]) -> list[str]:
        header = "| " + " | ".join(all_cols) + " |"
        sep    = "| " + " | ".join("---" for _ in all_cols) + " |"
        lines  = [header, sep]
        for r in sorted(results, key=lambda x: x.get("comb_pf", 0.0), reverse=True):
            vals = []
            for c in all_cols:
                v = r.get(c, "-")
                if isinstance(v, float):
                    vals.append(f"{v:.4f}")
                else:
                    vals.append(str(v))
            ok = (
                " ✓" if (
                    r.get("gap_pf", 0) >= GAP_PF_THRESHOLD
                    and r.get("comb_pf", 0) >= COMB_PF_THRESHOLD
                    and int(r.get("gap_trades", 0)) >= MIN_GAP_TRADES
                ) else ""
            )
            lines.append("| " + " | ".join(vals) + f"{ok} |")
        return lines

    lines: list[str] = [
        "# 갭업 눌림목 전략 그리드 서치",
        f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> OLD 구간: {OLD_START} ~ {OLD_END} / NEW 구간: {NEW_START} ~ {NEW_END}",
        f"> 선정 기준: 갭PF≥{GAP_PF_THRESHOLD} AND 합산PF≥{COMB_PF_THRESHOLD} "
        f"AND 갭거래≥{MIN_GAP_TRADES}건",
        "",
        "## 모멘텀 Baseline",
        "",
        "| 구간 | PF | PnL | 거래수 | 승률 |",
        "| --- | --- | --- | --- | --- |",
        (f"| OLD ({OLD_START}~{OLD_END}) | {old_baseline.get('pf', 0):.3f} | "
         f"{int(old_baseline.get('pnl', 0)):+,} | "
         f"{old_baseline.get('trades', 0)} | "
         f"{old_baseline.get('win_rate', 0):.1%} |"),
        (f"| NEW ({NEW_START}~{NEW_END}) | {new_baseline.get('pf', 0):.3f} | "
         f"{int(new_baseline.get('pnl', 0)):+,} | "
         f"{new_baseline.get('trades', 0)} | "
         f"{new_baseline.get('win_rate', 0):.1%} |"),
        "",
        f"## 그리드 결과 — OLD ({len(old_results)}조합)",
        "",
    ] + _md_rows(old_results) + [""]

    if new_results:
        lines += [
            f"## 그리드 결과 — NEW ({len(new_results)}조합)",
            "",
        ] + _md_rows(new_results) + [""]

    lines += ["## 선정 결과", ""]
    if best is not None:
        lines += [
            f"**최적 조합 (OLD 기준)**: `{best.get('tag')}`",
            "",
            f"| 파라미터 | 값 |",
            "| --- | --- |",
            f"| `gap_pullback_min_pct` | {best.get('gap_pullback_min_pct')} |",
            f"| `gap_pullback_min_pullback_pct` | {best.get('gap_pullback_min_pullback_pct')} |",
            f"| `gap_pullback_force_close` | {best.get('gap_pullback_force_close')} |",
            "",
            "| 지표 | 갭 단독 | 합산 |",
            "| --- | --- | --- |",
            (f"| PF | {best.get('gap_pf', 0):.3f} | {best.get('comb_pf', 0):.3f} |"),
            (f"| PnL | {int(best.get('gap_pnl', 0)):+,} | "
             f"{int(best.get('comb_pnl', 0)):+,} |"),
            (f"| 거래수 | {best.get('gap_trades', 0)} | "
             f"{best.get('comb_trades', 0)} |"),
            (f"| 승률 | {best.get('gap_win_rate', 0):.1%} | "
             f"{best.get('comb_win_rate', 0):.1%} |"),
        ]
    else:
        lines += [
            "선정 기준 미달 — 갭 전략 비활성 유지 (`gap_pullback_enabled: false`)",
        ]

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[SAVED] {out}", flush=True)


# ---------------------------------------------------------------------------
# config.yaml 갱신
# ---------------------------------------------------------------------------

def _update_config_yaml(best: dict) -> None:
    """gap_pullback 섹션의 파라미터와 enabled: true로 갱신."""
    cfg_path = Path("config.yaml")
    text = cfg_path.read_text(encoding="utf-8")

    gm = best.get("gap_pullback_min_pct", 0.02)
    pm = best.get("gap_pullback_min_pullback_pct", 0.01)
    fc = best.get("gap_pullback_force_close", "09:45")

    # gap_pullback 섹션 내 각 줄만 교체 (섹션 밖 enabled 등 건드리지 않음)
    # 패턴: 섹션 내에서 "    enabled: false" → "    enabled: true"
    # gap_pullback 섹션은 config.yaml에서 "  gap_pullback:" 이하에 위치
    text = re.sub(
        r"(  gap_pullback:\s*\n(?:.*\n)*?    enabled:\s*)false",
        r"\g<1>true",
        text,
    )
    text = re.sub(r"(    gap_min_pct:\s*)\S+", f"\\g<1>{gm}", text)
    text = re.sub(r"(    pullback_min_pct:\s*)\S+", f"\\g<1>{pm}", text)
    text = re.sub(r'(    force_close:\s*)["\']?[\w:]+["\']?', f'\\g<1>"{fc}"', text)

    cfg_path.write_text(text, encoding="utf-8")
    print(
        f"[CONFIG] config.yaml 갱신: enabled=true "
        f"gap_min={gm} pb_min={pm} force_close={fc}",
        flush=True,
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="갭 전략 27조합 그리드")
    parser.add_argument("--verify",    action="store_true", help="baseline PF 재현만")
    parser.add_argument("--no-update", action="store_true", help="config.yaml 갱신 건너뜀")
    args = parser.parse_args()

    # 캔들 로드 (전체 구간)
    print("캔들 캐시 로드 중...", flush=True)
    cache = await load_candle_cache(OLD_START, NEW_END)
    print(f"  {len(cache.candles)}종목 로드 완료", flush=True)

    old_cache = cache.filter_dates(OLD_START, OLD_END)
    new_cache = cache.filter_dates(NEW_START, NEW_END)

    # ── 모멘텀 baseline ──────────────────────────────────────────────────────
    print("\n[BASELINE] 모멘텀 baseline (OLD)...", flush=True)
    old_baseline_trades = await _run_momentum_baseline(old_cache)
    old_baseline_stats  = compute_stats(old_baseline_trades)
    print(
        f"  OLD: PF={old_baseline_stats['pf']:.3f}  "
        f"PnL={int(old_baseline_stats['pnl']):+,}  "
        f"trades={old_baseline_stats['trades']}  "
        f"win={old_baseline_stats['win_rate']:.1%}",
        flush=True,
    )

    print("[BASELINE] 모멘텀 baseline (NEW)...", flush=True)
    new_baseline_trades = await _run_momentum_baseline(new_cache)
    new_baseline_stats  = compute_stats(new_baseline_trades)
    print(
        f"  NEW: PF={new_baseline_stats['pf']:.3f}  "
        f"PnL={int(new_baseline_stats['pnl']):+,}  "
        f"trades={new_baseline_stats['trades']}  "
        f"win={new_baseline_stats['win_rate']:.1%}",
        flush=True,
    )

    if args.verify:
        print("\n[VERIFY 완료] --verify 옵션으로 baseline만 측정함", flush=True)
        return

    combos = _build_combos()
    print(f"\n총 {len(combos)}조합 (gap_min×pullback_min×force_close = "
          f"{len(GAP_MIN_VALS)}×{len(PULLBACK_MIN_VALS)}×{len(FORCE_CLOSE_VALS)})",
          flush=True)

    # ── OLD 구간 그리드 ──────────────────────────────────────────────────────
    print(f"\n[GRID OLD] {OLD_START} ~ {OLD_END}", flush=True)
    old_results = _run_gap_grid(combos, old_cache, old_baseline_trades)
    _print_table(old_results, f"갭 전략 그리드 (OLD, baseline PF={old_baseline_stats['pf']:.3f})")

    # ── NEW 구간 그리드 ──────────────────────────────────────────────────────
    print(f"\n[GRID NEW] {NEW_START} ~ {NEW_END}", flush=True)
    if new_cache.candles:
        new_results = _run_gap_grid(combos, new_cache, new_baseline_trades)
        _print_table(new_results, f"갭 전략 그리드 (NEW, baseline PF={new_baseline_stats['pf']:.3f})")
    else:
        new_results = []
        print("  NEW 캔들 없음 - 생략", flush=True)

    # ── 선정 ─────────────────────────────────────────────────────────────────
    best = _select_best(old_results)
    if best:
        print(
            f"\n[선정] {best['tag']}"
            f" — 갭PF={best.get('gap_pf', 0):.3f}"
            f" 갭#={best.get('gap_trades', 0)}"
            f" 합PF={best.get('comb_pf', 0):.3f}"
            f" 합PnL={int(best.get('comb_pnl', 0)):+,}",
            flush=True,
        )
        if not args.no_update:
            _update_config_yaml(best)
    else:
        print(
            f"\n[선정] 기준 미달 (갭PF≥{GAP_PF_THRESHOLD} / "
            f"합PF≥{COMB_PF_THRESHOLD} / 거래≥{MIN_GAP_TRADES}건) — "
            "갭 전략 비활성 유지",
            flush=True,
        )

    _write_report(old_results, new_results, old_baseline_stats, new_baseline_stats, best)


if __name__ == "__main__":
    asyncio.run(main())
