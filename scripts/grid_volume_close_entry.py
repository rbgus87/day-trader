"""scripts/grid_volume_close_entry.py — 거래량 비율 × 진입 상한(전일종가 대비) 16조합 그리드.

파라미터:
  volume_ratio:              [1.0, 1.2, 1.5, 2.0]  (momentum_volume_ratio)
  max_entry_above_close_pct: [10, 12, 15, 999]      (999 = 사실상 무제한)

구간:
  OLD: 2025-04-01 ~ 2026-04-10
  NEW: 2026-04-11 ~ 2026-05-19

선정 기준 (OLD 구간):
  PF >= 2.0
  거래 건수 >= 30
  최대 연속 손실 <= 8건
  MDD <= -15%

추가 집계:
  avg_entry_chg_from_close: 진입 시 전일종가 대비 평균 등락률
  avg_hold_min: 평균 보유 시간 (분)
  sl_pct / be_pct / fc_pct: 각 청산 비율

결과: reports/volume_close_entry_grid.md

사용:
    python -u scripts/grid_volume_close_entry.py
    python -u scripts/grid_volume_close_entry.py --verify
    python -u scripts/grid_volume_close_entry.py --no-update
"""
from __future__ import annotations

import asyncio
import dataclasses
import sys
import time as _time
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")

from utils.grid_runner import GridCache, compute_extended_stats, load_candle_cache

# ---------------------------------------------------------------------------
# 날짜 구간
# ---------------------------------------------------------------------------

OLD_START = "2025-04-01"
OLD_END   = "2026-04-10"
NEW_START = "2026-04-11"
NEW_END   = "2026-05-19"

# ---------------------------------------------------------------------------
# 그리드 파라미터
# ---------------------------------------------------------------------------

VOLUME_RATIO_VALS     = [1.0, 1.2, 1.5, 2.0]
MAX_CLOSE_PCT_VALS    = [10, 12, 15, 999]

# ---------------------------------------------------------------------------
# 선정 기준
# ---------------------------------------------------------------------------

PF_THRESHOLD          = 2.0
MIN_TRADES            = 30
MAX_CONSEC_LOSS       = 8
MAX_MDD_PCT           = -15.0   # MDD <= -15%


# ---------------------------------------------------------------------------
# 확장 stats (entry_chg_from_close, hold_min, 청산별 비율)
# ---------------------------------------------------------------------------

def compute_extended_stats(trades: list[dict]) -> dict:
    """기본 stats + avg_entry_chg_from_close / avg_hold_min / consec_loss / mdd"""
    n = len(trades)
    if n == 0:
        return {
            "pf": 0.0, "pnl": 0, "trades": 0, "win_rate": 0.0,
            "fc_pct": 0.0, "sl_pct": 0.0, "be_pct": 0.0,
            "avg_entry_chg_from_close": 0.0, "avg_hold_min": 0.0,
            "max_consec_loss": 0, "mdd_pct": 0.0, "exit_counts": {},
        }

    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    pnl_total = sum(t["pnl"] for t in trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    exits = Counter(t.get("exit_reason", "?") for t in trades)

    # avg_entry_chg_from_close
    chg_vals = [t.get("entry_chg_from_close", 0.0) for t in trades]
    avg_chg = sum(chg_vals) / n if n > 0 else 0.0

    # avg_hold_min
    hold_mins = []
    for t in trades:
        e_ts = t.get("entry_ts")
        x_ts = t.get("exit_ts")
        if e_ts and x_ts:
            hold_mins.append((x_ts - e_ts).total_seconds() / 60.0)
    avg_hold = sum(hold_mins) / len(hold_mins) if hold_mins else 0.0

    # max consecutive loss
    max_cl = 0
    cur_cl = 0
    for t in sorted(trades, key=lambda x: x.get("entry_ts") or datetime.min):
        if t["pnl"] < 0:
            cur_cl += 1
            max_cl = max(max_cl, cur_cl)
        else:
            cur_cl = 0

    # MDD (equity curve)
    sorted_tr = sorted(trades, key=lambda x: x.get("entry_ts") or datetime.min)
    eq = 0.0
    peak = 0.0
    mdd = 0.0
    for t in sorted_tr:
        eq += t["pnl"]
        if eq > peak:
            peak = eq
        dd = (eq - peak) / abs(peak) * 100.0 if peak != 0 else 0.0
        if dd < mdd:
            mdd = dd

    return {
        "pf":          round(gp / gl, 4) if gl > 0 else float("inf"),
        "pnl":         int(pnl_total),
        "trades":      n,
        "win_rate":    round(wins / n, 4),
        "fc_pct":      round(exits.get("forced_close", 0) / n * 100, 2),
        "sl_pct":      round(exits.get("stop_loss", 0) / n * 100, 2),
        "be_pct":      round(exits.get("breakeven_stop", 0) / n * 100, 2),
        "avg_entry_chg_from_close": round(avg_chg * 100, 2),
        "avg_hold_min": round(avg_hold, 1),
        "max_consec_loss": max_cl,
        "mdd_pct":     round(mdd, 2),
        "exit_counts": dict(exits),
    }


# ---------------------------------------------------------------------------
# 워커 — top-level (ProcessPool spawn 필수)
# ---------------------------------------------------------------------------

def _worker(args: tuple) -> dict:
    (config, candles_bytes, market_map_bytes,
     ticker_to_market, bt_config, params_dict) = args

    from loguru import logger as _l
    _l.remove()
    _l.add(sys.stderr, level="WARNING")

    import asyncio as _asyncio
    import pickle as _pickle
    from backtest.backtester_fast import FastBacktester as _FBT
    from strategy.momentum_strategy import MomentumStrategy as _MS

    candles_cache: dict = _pickle.loads(candles_bytes)
    market_map: dict    = _pickle.loads(market_map_bytes)

    all_trades: list[dict] = []
    for tk, df in candles_cache.items():
        market = ticker_to_market.get(tk, "unknown")
        bt = _FBT(
            db=None, config=config, backtest_config=bt_config,
            ticker_market=market, market_strong_by_date=market_map,
        )
        strat = _MS(config)
        result = _asyncio.run(bt.run_multi_day_cached(tk, df, strat))
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)

    from utils.grid_runner import compute_extended_stats as _ces
    stats = _ces(all_trades)
    return {**params_dict, **stats}


# ---------------------------------------------------------------------------
# 조합 빌더
# ---------------------------------------------------------------------------

def _build_combos() -> list[dict]:
    combos = []
    for vr in VOLUME_RATIO_VALS:
        for cp in MAX_CLOSE_PCT_VALS:
            tag = f"vr{vr:.1f}_cp{cp if cp < 999 else 'inf'}"
            combos.append({
                "tag": tag,
                "momentum_volume_ratio": vr,
                "max_entry_above_close_pct": float(cp),
            })
    return combos


# ---------------------------------------------------------------------------
# 병렬 실행
# ---------------------------------------------------------------------------

def _run_grid(combos: list[dict], cache: GridCache) -> list[dict]:
    import multiprocessing as mp
    import os
    import pickle
    from concurrent.futures import ProcessPoolExecutor

    cache.prepare_bytes()
    n_workers = max(2, min(4, (os.cpu_count() or 4) - 1))

    def _factory(p: dict, base_cfg) -> object:
        return dataclasses.replace(
            base_cfg,
            momentum_volume_ratio=p["momentum_volume_ratio"],
            max_entry_above_close_pct=p["max_entry_above_close_pct"],
        )

    worker_args = [
        (
            _factory(p, cache.base_config),
            cache.candles_bytes,
            cache.market_map_bytes,
            cache.ticker_to_market,
            cache.bt_config,
            p,
        )
        for p in combos
    ]

    n = len(combos)
    results: list[dict] = []
    t0 = _time.time()
    print(f"[GRID] {n}조합 × {len(cache.candles)}종목  workers={n_workers}", flush=True)

    try:
        from tqdm import tqdm as _tqdm
        _use_tqdm = True
    except ImportError:
        _use_tqdm = False

    ctx = mp.get_context("spawn")
    try:
        with ProcessPoolExecutor(max_workers=n_workers, mp_context=ctx) as ex:
            it = ex.map(_worker, worker_args)
            if _use_tqdm:
                it = _tqdm(it, total=n, desc="grid", unit="combo")
            for i, r in enumerate(it, 1):
                results.append(r)
                if not _use_tqdm:
                    elapsed = _time.time() - t0
                    eta = elapsed / i * (n - i) if i < n else 0
                    ok = _is_ok(r)
                    print(
                        f"  [{i:>2}/{n}] {r.get('tag','?'):<20} "
                        f"vr={r.get('momentum_volume_ratio','?')} "
                        f"cp={r.get('max_entry_above_close_pct','?')} "
                        f"pf={r.get('pf', 0):.3f} "
                        f"trades={r.get('trades', 0)} "
                        f"chg={r.get('avg_entry_chg_from_close', 0):.1f}% "
                        f"{'[OK]' if ok else ''} "
                        f"(ETA {eta:.0f}s)",
                        flush=True,
                    )
    except Exception as exc:
        print(f"[WARN] Pool 실패 ({exc}), 순차 실행 전환", flush=True)
        results = []
        for i, wargs in enumerate(worker_args, 1):
            r = _worker(wargs)
            results.append(r)
            elapsed = _time.time() - t0
            ok = _is_ok(r)
            print(
                f"  [{i:>2}/{n}] {r.get('tag','?'):<20} "
                f"pf={r.get('pf', 0):.3f} "
                f"trades={r.get('trades', 0)} "
                f"chg={r.get('avg_entry_chg_from_close', 0):.1f}% "
                f"{'[OK]' if ok else ''} "
                f"({elapsed:.0f}s)",
                flush=True,
            )

    elapsed = _time.time() - t0
    print(f"[DONE] {n}조합 완료 ({elapsed:.1f}s)", flush=True)
    return results


def _is_ok(r: dict) -> bool:
    return (
        r.get("pf", 0.0) >= PF_THRESHOLD
        and int(r.get("trades", 0)) >= MIN_TRADES
        and int(r.get("max_consec_loss", 999)) <= MAX_CONSEC_LOSS
        and r.get("mdd_pct", -100.0) >= MAX_MDD_PCT
    )


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------

def _print_table(results: list[dict], title: str, baseline_pf: float = 0.0) -> None:
    print(f"\n{title}", flush=True)
    hdr = (
        f"{'태그':>22} | "
        f"{'vr':>4} {'cp':>4} | "
        f"{'PF':>6} {'PnL':>10} {'#':>4} {'win%':>5} | "
        f"{'chg%':>6} {'hold':>5} | "
        f"{'SL%':>5} {'BE%':>5} {'FC%':>5} | "
        f"{'maxCL':>5} {'MDD%':>6} {'OK':>3}"
    )
    sep = "-" * len(hdr)
    print(sep, flush=True)
    print(hdr, flush=True)
    print(sep, flush=True)

    for r in sorted(results, key=lambda x: x.get("pf", 0.0), reverse=True):
        ok = _is_ok(r)
        print(
            f"{r.get('tag','?'):>22} | "
            f"{r.get('momentum_volume_ratio', 0):>4.1f} "
            f"{r.get('max_entry_above_close_pct', 0):>4.0f} | "
            f"{r.get('pf', 0):>6.3f} {r.get('pnl', 0):>+10,} "
            f"{r.get('trades', 0):>4} {r.get('win_rate', 0):>5.1%} | "
            f"{r.get('avg_entry_chg_from_close', 0):>6.1f} "
            f"{r.get('avg_hold_min', 0):>5.0f} | "
            f"{r.get('sl_pct', 0):>5.1f} {r.get('be_pct', 0):>5.1f} "
            f"{r.get('fc_pct', 0):>5.1f} | "
            f"{r.get('max_consec_loss', 0):>5} "
            f"{r.get('mdd_pct', 0):>6.1f} "
            f"{'Y' if ok else '':>3}",
            flush=True,
        )
    print(sep, flush=True)

    # 상위 3 하이라이트
    top3 = sorted(results, key=lambda x: x.get("pf", 0.0), reverse=True)[:3]
    print("\n▶ PF 상위 3:", flush=True)
    for i, r in enumerate(top3, 1):
        print(
            f"  {i}. {r.get('tag')}  PF={r.get('pf',0):.3f}  "
            f"PnL={r.get('pnl',0):+,}  #={r.get('trades',0)}  "
            f"chg={r.get('avg_entry_chg_from_close',0):.1f}%  "
            f"hold={r.get('avg_hold_min',0):.0f}분  "
            f"MDD={r.get('mdd_pct',0):.1f}%",
            flush=True,
        )


# ---------------------------------------------------------------------------
# 보고서 저장
# ---------------------------------------------------------------------------

def _write_report(
    old_results: list[dict],
    new_results: list[dict],
    old_baseline: dict,
    new_baseline: dict,
) -> None:
    out = Path("reports/volume_close_entry_grid.md")
    out.parent.mkdir(exist_ok=True)

    cols = [
        "tag", "momentum_volume_ratio", "max_entry_above_close_pct",
        "pf", "pnl", "trades", "win_rate",
        "avg_entry_chg_from_close", "avg_hold_min",
        "sl_pct", "be_pct", "fc_pct",
        "max_consec_loss", "mdd_pct",
    ]

    def _md_rows(results: list[dict]) -> list[str]:
        header = "| " + " | ".join(cols) + " | OK |"
        sep    = "| " + " | ".join("---" for _ in cols) + " | --- |"
        lines  = [header, sep]
        for r in sorted(results, key=lambda x: x.get("pf", 0.0), reverse=True):
            vals = []
            for c in cols:
                v = r.get(c, "-")
                if isinstance(v, float):
                    vals.append(f"{v:.4f}")
                else:
                    vals.append(str(v))
            ok = " ✓" if _is_ok(r) else ""
            lines.append("| " + " | ".join(vals) + f" |{ok}|")
        return lines

    lines: list[str] = [
        "# 거래량 비율 × 진입 상한(전일종가 대비) 그리드",
        f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> OLD: {OLD_START}~{OLD_END} / NEW: {NEW_START}~{NEW_END}",
        f"> 선정 기준: PF≥{PF_THRESHOLD} AND 거래≥{MIN_TRADES}건 "
        f"AND 연속손실≤{MAX_CONSEC_LOSS}건 AND MDD≥{MAX_MDD_PCT}%",
        "",
        "## Baseline (현재 설정: vr=2.0, cp=15)",
        "",
        "| 구간 | PF | PnL | 거래수 | 승률 |",
        "| --- | --- | --- | --- | --- |",
        (f"| OLD | {old_baseline.get('pf', 0):.3f} | "
         f"{int(old_baseline.get('pnl', 0)):+,} | "
         f"{old_baseline.get('trades', 0)} | "
         f"{old_baseline.get('win_rate', 0):.1%} |"),
        (f"| NEW | {new_baseline.get('pf', 0):.3f} | "
         f"{int(new_baseline.get('pnl', 0)):+,} | "
         f"{new_baseline.get('trades', 0)} | "
         f"{new_baseline.get('win_rate', 0):.1%} |"),
        "",
        f"## 그리드 — OLD ({len(old_results)}조합)",
        "",
    ] + _md_rows(old_results) + [""]

    if new_results:
        lines += [
            f"## 그리드 — NEW ({len(new_results)}조합)",
            "",
        ] + _md_rows(new_results) + [""]

    # 선정 기준 통과 조합
    ok_old = [r for r in old_results if _is_ok(r)]
    lines += ["## 선정 기준 통과 조합 (OLD 기준)", ""]
    if ok_old:
        best = max(ok_old, key=lambda x: x.get("pnl", 0))
        lines += [
            f"**통과 수**: {len(ok_old)}개 / {len(old_results)}개",
            "",
            f"**최고 PnL 조합**: `{best.get('tag')}`",
            f"  - volume_ratio = {best.get('momentum_volume_ratio')}",
            f"  - max_entry_above_close_pct = {best.get('max_entry_above_close_pct')}",
            f"  - PF = {best.get('pf', 0):.3f} / PnL = {int(best.get('pnl', 0)):+,}",
            f"  - avg_entry_chg_from_close = {best.get('avg_entry_chg_from_close', 0):.2f}%",
            f"  - avg_hold_min = {best.get('avg_hold_min', 0):.0f}분",
            f"  - MDD = {best.get('mdd_pct', 0):.1f}%",
        ]
    else:
        lines += ["선정 기준 미달 조합 없음."]

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[SAVED] {out}", flush=True)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="거래량×진입상한 16조합 그리드")
    parser.add_argument("--verify",    action="store_true", help="baseline만 측정")
    parser.add_argument("--no-update", action="store_true", help="config 갱신 건너뜀")
    args = parser.parse_args()

    print("캔들 캐시 로드 중...", flush=True)
    cache = await load_candle_cache(OLD_START, NEW_END)
    print(f"  {len(cache.candles)}종목 로드 완료", flush=True)

    old_cache = cache.filter_dates(OLD_START, OLD_END)
    new_cache = cache.filter_dates(NEW_START, NEW_END)

    # ── baseline (vr=2.0, cp=15) ──────────────────────────────────────────
    print("\n[BASELINE] 현재 설정 측정 (vr=2.0, cp=15)...", flush=True)

    async def _run_baseline(c: GridCache) -> dict:
        from backtest.backtester_fast import FastBacktester as _FBT
        from strategy.momentum_strategy import MomentumStrategy as _MS
        trades_: list[dict] = []
        for tk, df in c.candles.items():
            market = c.ticker_to_market.get(tk, "unknown")
            bt = _FBT(
                db=None, config=c.base_config, backtest_config=c.bt_config,
                ticker_market=market, market_strong_by_date=c.market_map,
            )
            strat = _MS(c.base_config)
            result = await bt.run_multi_day_cached(tk, df, strat)
            for t in result.get("trades", []):
                t["ticker"] = tk
                trades_.append(t)
        return compute_extended_stats(trades_)

    old_bl = await _run_baseline(old_cache)
    new_bl = await _run_baseline(new_cache)

    print(
        f"  OLD: PF={old_bl['pf']:.3f} PnL={old_bl['pnl']:+,} "
        f"trades={old_bl['trades']} win={old_bl['win_rate']:.1%} "
        f"chg={old_bl['avg_entry_chg_from_close']:.1f}%",
        flush=True,
    )
    print(
        f"  NEW: PF={new_bl['pf']:.3f} PnL={new_bl['pnl']:+,} "
        f"trades={new_bl['trades']} win={new_bl['win_rate']:.1%} "
        f"chg={new_bl['avg_entry_chg_from_close']:.1f}%",
        flush=True,
    )

    if args.verify:
        print("\n[VERIFY 완료] baseline만 측정함", flush=True)
        return

    combos = _build_combos()
    print(
        f"\n총 {len(combos)}조합 "
        f"(vr {len(VOLUME_RATIO_VALS)}개 × cp {len(MAX_CLOSE_PCT_VALS)}개)",
        flush=True,
    )

    # ── OLD 그리드 ──────────────────────────────────────────────────────────
    print(f"\n[GRID OLD] {OLD_START} ~ {OLD_END}", flush=True)
    old_results = _run_grid(combos, old_cache)
    _print_table(
        old_results,
        f"OLD 그리드 (baseline PF={old_bl['pf']:.3f})",
        old_bl["pf"],
    )

    # ── NEW 그리드 ──────────────────────────────────────────────────────────
    print(f"\n[GRID NEW] {NEW_START} ~ {NEW_END}", flush=True)
    if new_cache.candles:
        new_results = _run_grid(combos, new_cache)
        _print_table(
            new_results,
            f"NEW 그리드 (baseline PF={new_bl['pf']:.3f})",
            new_bl["pf"],
        )
    else:
        new_results = []
        print("  NEW 캔들 없음 - 생략", flush=True)

    # ── 선정 결과 출력 ──────────────────────────────────────────────────────
    ok_combos = [r for r in old_results if _is_ok(r)]
    print(
        f"\n[선정] 기준 통과: {len(ok_combos)}개 / {len(old_results)}개 "
        f"(PF≥{PF_THRESHOLD} / 거래≥{MIN_TRADES} / 연속손실≤{MAX_CONSEC_LOSS} / MDD≥{MAX_MDD_PCT}%)",
        flush=True,
    )
    if ok_combos:
        best = max(ok_combos, key=lambda x: x.get("pnl", 0))
        print(
            f"  ▶ 최고 PnL: {best['tag']}  "
            f"PF={best['pf']:.3f}  PnL={best['pnl']:+,}  "
            f"chg={best['avg_entry_chg_from_close']:.1f}%",
            flush=True,
        )

    _write_report(old_results, new_results, old_bl, new_bl)


if __name__ == "__main__":
    asyncio.run(main())
