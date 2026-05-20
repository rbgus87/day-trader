"""scripts/grid_pullback.py — 눌림목(Pullback) 전략 216조합 그리드 서치.

파라미터 격자:
  surge_pct        : [0.03, 0.05, 0.07, 0.10]
  pullback_depth   : [0.015, 0.020, 0.030]
  sl_from_high_pct : [0.03, 0.05, 0.07]
  tp_above_high_pct: [0.000, 0.010, 0.020]
  entry_end        : ["11:00", "13:00"]

총 조합: 4 × 3 × 3 × 3 × 2 = 216

구간:
  OLD: 2025-04-01 ~ 2026-04-10
  NEW: 2026-04-11 ~ 2026-05-19

선정 기준 (OLD 구간 기준):
  PF ≥ 1.5  AND  거래수 ≥ 30  AND  연속 손실 ≤ 8
  + NEW 구간도 PF > 1.0

사용:
    python -u scripts/grid_pullback.py           # 전체 실행
    python -u scripts/grid_pullback.py --verify  # 단일 파라미터 검증만
"""
from __future__ import annotations

import asyncio
import dataclasses
import multiprocessing as mp
import os
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

from utils.grid_runner import GridCache, load_candle_cache

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

SURGE_PCTS        = [0.03, 0.05, 0.07, 0.10]
PULLBACK_DEPTHS   = [0.015, 0.020, 0.030]
SL_FROM_HIGHS     = [0.03, 0.05, 0.07]
TP_ABOVE_HIGHS    = [0.000, 0.010, 0.020]
ENTRY_ENDS        = ["11:00", "13:00"]

# ---------------------------------------------------------------------------
# 선정 기준
# ---------------------------------------------------------------------------

MIN_PF          = 1.5
MIN_TRADES      = 30
MAX_CONSEC_LOSS = 8
MIN_NEW_PF      = 1.0

# ---------------------------------------------------------------------------
# 확장 KPI 계산
# ---------------------------------------------------------------------------

def _compute_pullback_stats(trades: list[dict]) -> dict:
    """눌림목 거래 목록 → KPI dict."""
    n = len(trades)
    if n == 0:
        return {
            "pf": 0.0, "pnl": 0, "trades": 0,
            "win_rate": 0.0, "fc_pct": 0.0, "tp_pct": 0.0, "sl_pct": 0.0,
            "max_consec_loss": 0, "avg_hold_min": 0.0, "exit_counts": {},
        }

    gp   = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl   = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    pnl  = sum(t["pnl"] for t in trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    exits = Counter(t.get("exit_reason", "?") for t in trades)

    sorted_trades = sorted(trades, key=lambda x: x.get("entry_ts") or datetime.min)
    max_cl, cur_cl = 0, 0
    for t in sorted_trades:
        if t["pnl"] < 0:
            cur_cl += 1
            max_cl = max(max_cl, cur_cl)
        else:
            cur_cl = 0

    hold_mins = []
    for t in sorted_trades:
        e_ts, x_ts = t.get("entry_ts"), t.get("exit_ts")
        if e_ts and x_ts:
            hold_mins.append((x_ts - e_ts).total_seconds() / 60.0)
    avg_hold = sum(hold_mins) / len(hold_mins) if hold_mins else 0.0

    return {
        "pf":              round(gp / gl, 4) if gl > 0 else float("inf"),
        "pnl":             int(pnl),
        "trades":          n,
        "win_rate":        round(wins / n, 4),
        "fc_pct":          round(exits.get("forced_close", 0) / n * 100, 2),
        "tp_pct":          round(exits.get("tp_exit", 0) / n * 100, 2),
        "sl_pct":          round(exits.get("stop_loss", 0) / n * 100, 2),
        "max_consec_loss": max_cl,
        "avg_hold_min":    round(avg_hold, 1),
        "exit_counts":     dict(exits),
    }


# ---------------------------------------------------------------------------
# config factory
# ---------------------------------------------------------------------------

def _pb_config_factory(params: dict, base_config: object) -> object:
    return dataclasses.replace(
        base_config,
        pb_enabled=True,
        pb_surge_pct=params["surge_pct"],
        pb_pullback_depth=params["pullback_depth"],
        pb_sl_from_high_pct=params["sl_from_high_pct"],
        pb_tp_above_high_pct=params["tp_above_high_pct"],
        pb_entry_start="09:30",
        pb_entry_end=params["entry_end"],
        pb_min_above_close_pct=0.01,
        pb_min_volume=50000,
        # 그리드 단독 측정용 필터 비활성
        market_filter_enabled=False,
        intraday_market_filter_enabled=False,
        blacklist_enabled=False,
        consecutive_loss_rest_enabled=False,
        volatility_sizing_enabled=False,
        max_trades_per_day=1,
        cooldown_minutes=999,
        adx_enabled=False,
    )


# ---------------------------------------------------------------------------
# 조합 빌더
# ---------------------------------------------------------------------------

def _build_combos() -> list[dict]:
    combos = []
    for sg in SURGE_PCTS:
        for pb in PULLBACK_DEPTHS:
            for sl in SL_FROM_HIGHS:
                for tp in TP_ABOVE_HIGHS:
                    for ee in ENTRY_ENDS:
                        tag = (
                            f"sg{int(sg*100)}_pb{int(pb*1000)}"
                            f"_sl{int(sl*100)}_tp{int(tp*1000)}"
                            f"_ee{ee.replace(':', '')}"
                        )
                        combos.append({
                            "tag":             tag,
                            "surge_pct":       sg,
                            "pullback_depth":  pb,
                            "sl_from_high_pct": sl,
                            "tp_above_high_pct": tp,
                            "entry_end":       ee,
                        })
    return combos


# ---------------------------------------------------------------------------
# 워커
# ---------------------------------------------------------------------------

def _pb_worker(args: tuple) -> dict:
    """눌림목 단일 조합 백테스트 — subprocess 실행용."""
    config, candles_bytes, market_map_bytes, ticker_to_market, bt_config, params_dict = args

    import sys as _sys, pickle as _p, asyncio as _a
    from loguru import logger as _l
    _l.remove()
    _l.add(_sys.stderr, level="WARNING")

    from backtest.backtester_fast import PullbackFastBacktester as _PBT
    from strategy.pullback_strategy import PullbackStrategy as _PS

    candles_cache: dict = _p.loads(candles_bytes)
    market_map: dict    = _p.loads(market_map_bytes)

    all_trades: list[dict] = []
    for tk, df in candles_cache.items():
        market = ticker_to_market.get(tk, "unknown")
        bt = _PBT(
            db=None, config=config, backtest_config=bt_config,
            ticker_market=market, market_strong_by_date=market_map,
        )
        strat = _PS(config)
        result = _a.run(bt.run_multi_day_cached(tk, df, strat))
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)

    stats = _compute_pullback_stats(all_trades)
    return {**params_dict, **stats}


# ---------------------------------------------------------------------------
# 병렬 그리드 실행
# ---------------------------------------------------------------------------

def _run_pb_grid(
    combos: list[dict], cache: GridCache, *, max_workers: int | None = None
) -> list[dict]:
    import pickle

    cache.prepare_bytes()
    n_workers = max_workers or max(2, min(4, (os.cpu_count() or 4) - 1))

    worker_args = [
        (
            _pb_config_factory(p, cache.base_config),
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
    print(f"[PB GRID] {n}조합 × {len(cache.candles)}종목  workers={n_workers}", flush=True)

    try:
        from tqdm import tqdm as _tqdm
        _use_tqdm = True
    except ImportError:
        _use_tqdm = False

    ctx = mp.get_context("spawn")
    try:
        from concurrent.futures import ProcessPoolExecutor as _PPE
        with _PPE(max_workers=n_workers, mp_context=ctx) as ex:
            it = ex.map(_pb_worker, worker_args)
            if _use_tqdm:
                it = _tqdm(it, total=n, desc="pullback grid", unit="combo")
            for i, r in enumerate(it, 1):
                results.append(r)
                if not _use_tqdm:
                    elapsed = _time.time() - t0
                    eta = elapsed / i * (n - i) if i < n else 0
                    ok = _is_passing(r)
                    print(
                        f"  [{i:>3}/{n}] {r.get('tag',''):<45} "
                        f"pf={r.get('pf', 0):.3f} tr={r.get('trades', 0):>4} "
                        f"win={r.get('win_rate', 0):.1%} "
                        f"cl={r.get('max_consec_loss', 0):>2} "
                        f"{'OK' if ok else '  '} (ETA {eta:.0f}s)",
                        flush=True,
                    )
    except Exception as exc:
        print(f"[WARN] Pool 실패 ({exc}), 순차 실행 전환", flush=True)
        results = []
        for i, wargs in enumerate(worker_args, 1):
            r = _pb_worker(wargs)
            results.append(r)
            if not _use_tqdm:
                elapsed = _time.time() - t0
                print(
                    f"  [{i:>3}/{n}] {r.get('tag',''):<45} "
                    f"pf={r.get('pf', 0):.3f} ({elapsed:.0f}s)",
                    flush=True,
                )

    elapsed = _time.time() - t0
    print(f"[DONE] {n}조합 완료 ({elapsed:.1f}s)", flush=True)
    return results


# ---------------------------------------------------------------------------
# 선정 기준
# ---------------------------------------------------------------------------

def _is_passing(r: dict, *, new_pf: float | None = None) -> bool:
    ok = (
        r.get("pf", 0.0) >= MIN_PF
        and int(r.get("trades", 0)) >= MIN_TRADES
        and int(r.get("max_consec_loss", 999)) <= MAX_CONSEC_LOSS
    )
    if ok and new_pf is not None:
        ok = new_pf >= MIN_NEW_PF
    return ok


# ---------------------------------------------------------------------------
# 보고서
# ---------------------------------------------------------------------------

def _print_table(results: list[dict], title: str) -> None:
    print(f"\n{title}", flush=True)
    hdr = (
        f"{'태그':>48} | "
        f"{'PF':>6} {'PnL':>10} {'거래#':>5} {'승률':>6} "
        f"{'TP%':>5} {'CL':>3} {'보유':>5} {'OK':>3}"
    )
    sep = "-" * len(hdr)
    print(sep, flush=True)
    print(hdr, flush=True)
    print(sep, flush=True)
    for r in sorted(results, key=lambda x: x.get("pf", 0.0), reverse=True)[:20]:
        ok = "Y" if _is_passing(r) else ""
        print(
            f"{r.get('tag', ''):<48} | "
            f"{r.get('pf', 0):>6.3f} {int(r.get('pnl', 0)):>+10,} "
            f"{int(r.get('trades', 0)):>5} {r.get('win_rate', 0):>6.1%} "
            f"{r.get('tp_pct', 0):>5.1f} "
            f"{r.get('max_consec_loss', 0):>3} {r.get('avg_hold_min', 0):>5.1f} "
            f"{ok:>3}",
            flush=True,
        )
    print(sep, flush=True)


def _select_best_combos(
    old_results: list[dict],
    new_results_map: dict[str, dict],
) -> list[dict]:
    passing = []
    for r in old_results:
        tag = r.get("tag", "")
        nr = new_results_map.get(tag)
        new_pf = nr.get("pf", 0.0) if nr else None
        if _is_passing(r, new_pf=new_pf):
            passing.append({**r, "new_pf": new_pf or 0.0})
    return sorted(passing, key=lambda x: x.get("pf", 0.0), reverse=True)


def _write_report(
    old_results: list[dict],
    new_results: list[dict],
    passing: list[dict],
) -> None:
    out = Path("reports/pullback_grid_result.md")
    out.parent.mkdir(exist_ok=True)

    cols = [
        "tag", "surge_pct", "pullback_depth", "sl_from_high_pct",
        "tp_above_high_pct", "entry_end",
        "pf", "pnl", "trades", "win_rate",
        "tp_pct", "sl_pct", "fc_pct",
        "max_consec_loss", "avg_hold_min",
    ]

    def _md_rows(results: list[dict]) -> list[str]:
        header = "| " + " | ".join(cols) + " |"
        sep    = "| " + " | ".join("---" for _ in cols) + " |"
        lines  = [header, sep]
        for r in sorted(results, key=lambda x: x.get("pf", 0.0), reverse=True):
            vals = []
            for c in cols:
                v = r.get(c, "-")
                if isinstance(v, float):
                    vals.append(f"{v:.4f}")
                else:
                    vals.append(str(v))
            ok = " ✓" if _is_passing(r) else ""
            lines.append("| " + " | ".join(vals) + f"{ok} |")
        return lines

    lines: list[str] = [
        "# 눌림목(Pullback) 전략 그리드 서치",
        f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> OLD 구간: {OLD_START} ~ {OLD_END}",
        f"> NEW 구간: {NEW_START} ~ {NEW_END}",
        f"> 선정 기준: PF≥{MIN_PF}  AND  거래≥{MIN_TRADES}  "
        f"AND  연속손실≤{MAX_CONSEC_LOSS}  AND  NEW PF>{MIN_NEW_PF}",
        "",
        f"## 그리드 결과 — OLD ({len(old_results)}조합)",
        "",
    ] + _md_rows(old_results) + [""]

    if new_results:
        lines += [
            f"## 그리드 결과 — NEW ({len(new_results)}조합)",
            "",
        ] + _md_rows(new_results) + [""]

    lines += ["## 선정 기준 통과 조합 (OLD 기준, NEW PF>1.0 교차 검증)", ""]
    if passing:
        lines += [
            "| 태그 | surge% | pb% | sl% | tp% | entry_end | PF(OLD) | PnL(OLD) | 거래# | 승률 | TP% | CL | NEW PF |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for r in passing[:10]:
            lines.append(
                f"| {r.get('tag','')} "
                f"| {r.get('surge_pct',0):.0%} "
                f"| {r.get('pullback_depth',0):.1%} "
                f"| {r.get('sl_from_high_pct',0):.0%} "
                f"| {r.get('tp_above_high_pct',0):.1%} "
                f"| {r.get('entry_end','')} "
                f"| {r.get('pf',0):.3f} "
                f"| {int(r.get('pnl',0)):+,} "
                f"| {r.get('trades',0)} "
                f"| {r.get('win_rate',0):.1%} "
                f"| {r.get('tp_pct',0):.1f}% "
                f"| {r.get('max_consec_loss',0)} "
                f"| {r.get('new_pf',0):.3f} |"
            )
    else:
        lines += [
            f"선정 기준 미달 — 전 조합 비활성 (`pullback.enabled: false` 유지).",
        ]

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[SAVED] {out}", flush=True)


# ---------------------------------------------------------------------------
# --verify 단일 백테스트
# ---------------------------------------------------------------------------

async def _run_verify(cache: GridCache) -> None:
    from backtest.backtester_fast import PullbackFastBacktester as _PBT
    from strategy.pullback_strategy import PullbackStrategy as _PS
    import dataclasses

    cfg = _pb_config_factory(
        {
            "surge_pct": 0.05, "pullback_depth": 0.02,
            "sl_from_high_pct": 0.05, "tp_above_high_pct": 0.01,
            "entry_end": "13:00",
        },
        cache.base_config,
    )

    all_trades: list[dict] = []
    for tk, df in cache.candles.items():
        market = cache.ticker_to_market.get(tk, "unknown")
        bt = _PBT(
            db=None, config=cfg, backtest_config=cache.bt_config,
            ticker_market=market, market_strong_by_date=cache.market_map,
        )
        strat = _PS(cfg)
        result = await bt.run_multi_day_cached(tk, df, strat)
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)

    stats = _compute_pullback_stats(all_trades)
    print("\n[VERIFY] 눌림목 단일 백테스트 결과")
    print(f"  (surge=5% / pb=2% / sl=5% / tp=1% / entry_end=13:00)")
    print(f"  PF={stats['pf']:.3f}  PnL={stats['pnl']:+,}  "
          f"거래#{stats['trades']}  승률={stats['win_rate']:.1%}  "
          f"연속손실={stats['max_consec_loss']}  평균보유={stats['avg_hold_min']:.1f}분")
    print(f"  청산분포: {stats['exit_counts']}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="눌림목 전략 216조합 그리드")
    parser.add_argument("--verify", action="store_true", help="단일 파라미터 검증만")
    args = parser.parse_args()

    print("캔들 캐시 로드 중...", flush=True)
    cache_all = await load_candle_cache(OLD_START, NEW_END)
    print(f"  {len(cache_all.candles)}종목 로드 완료", flush=True)

    old_cache = cache_all.filter_dates(OLD_START, OLD_END)
    new_cache = cache_all.filter_dates(NEW_START, NEW_END)

    if args.verify:
        print("\n[VERIFY] OLD 구간 단일 파라미터 테스트...", flush=True)
        await _run_verify(old_cache)
        print("\n[VERIFY 완료]", flush=True)
        return

    combos = _build_combos()
    print(
        f"\n총 {len(combos)}조합 "
        f"(surge×pb×sl×tp×entry_end = "
        f"{len(SURGE_PCTS)}×{len(PULLBACK_DEPTHS)}×{len(SL_FROM_HIGHS)}"
        f"×{len(TP_ABOVE_HIGHS)}×{len(ENTRY_ENDS)})",
        flush=True,
    )

    # ── OLD 구간 ─────────────────────────────────────────────────────────────
    print(f"\n[GRID OLD] {OLD_START} ~ {OLD_END}", flush=True)
    old_results = _run_pb_grid(combos, old_cache)
    _print_table(old_results, f"눌림목 그리드 (OLD, {len(old_results)}조합)")

    # ── NEW 구간 ─────────────────────────────────────────────────────────────
    print(f"\n[GRID NEW] {NEW_START} ~ {NEW_END}", flush=True)
    if new_cache.candles:
        new_results = _run_pb_grid(combos, new_cache)
        _print_table(new_results, f"눌림목 그리드 (NEW, {len(new_results)}조합)")
    else:
        new_results = []
        print("  NEW 캔들 없음 — 생략", flush=True)

    # ── 교차 검증 선정 ────────────────────────────────────────────────────────
    new_map = {r["tag"]: r for r in new_results}
    passing = _select_best_combos(old_results, new_map)

    print(f"\n[선정] 기준 통과 조합: {len(passing)}개", flush=True)
    for r in passing[:10]:
        print(
            f"  {r['tag']}  OLD PF={r['pf']:.3f}  NEW PF={r['new_pf']:.3f}  "
            f"거래#{r['trades']}  CL={r['max_consec_loss']}  TP%={r.get('tp_pct',0):.1f}%",
            flush=True,
        )
    if not passing:
        print(
            f"  선정 기준 미달 (PF≥{MIN_PF} / 거래≥{MIN_TRADES} / "
            f"CL≤{MAX_CONSEC_LOSS} / NEW PF>{MIN_NEW_PF})",
            flush=True,
        )

    _write_report(old_results, new_results, passing)


if __name__ == "__main__":
    asyncio.run(main())
