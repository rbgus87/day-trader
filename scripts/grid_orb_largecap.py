"""scripts/grid_orb_largecap.py — ORB 전략 18조합 그리드 (KOSPI 대형주).

grid_orb.py 최선 파라미터 주변 조합만 테스트.
유니버스: config/universe_largecap.yaml (KOSPI 50종목)

파라미터 격자:
  sl_ratio          : [1.0, 1.5, 2.0]
  tp_ratio          : [2.0, 3.0, 4.0]
  entry_deadline    : ["09:30"]          ← 원본 최선 고정
  breakout_buffer   : [0.0]              ← 원본 최선 고정
  use_volume_filter : [True, False]

총 조합: 3 × 3 × 1 × 1 × 2 = 18

구간:
  OLD: 2025-04-01 ~ 2026-04-10
  NEW: 2026-04-11 ~ 2026-05-21

선정 기준 (OLD 구간 기준):
  PF ≥ 1.5  AND  거래수 ≥ 20  AND  연속 손실 ≤ 8
  + NEW 구간도 PF > 1.0

결과: reports/orb_largecap_grid_result.md

사용:
    python -u scripts/grid_orb_largecap.py          # 전체 18조합
    python -u scripts/grid_orb_largecap.py --verify # 최선 파라미터 단일 검증
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
NEW_END   = "2026-05-21"

UNIVERSE_PATH = "config/universe_largecap.yaml"

# ---------------------------------------------------------------------------
# 그리드 파라미터 — 원본 최선 조합 주변 18개
# ---------------------------------------------------------------------------

SL_RATIOS        = [1.0, 1.5, 2.0]
TP_RATIOS        = [2.0, 3.0, 4.0]
ENTRY_DEADLINES  = ["09:30"]   # 원본 최선: 09:30 고정
BREAKOUT_BUFFERS = [0.0]       # 원본 최선: 0.0 고정
USE_VOL_FILTERS  = [True, False]

# ---------------------------------------------------------------------------
# 선정 기준
# ---------------------------------------------------------------------------

MIN_PF          = 1.5
MIN_TRADES      = 20
MAX_CONSEC_LOSS = 8
MIN_NEW_PF      = 1.0

# ---------------------------------------------------------------------------
# KPI 계산
# ---------------------------------------------------------------------------

def _compute_orb_stats(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {
            "pf": 0.0, "pnl": 0, "trades": 0,
            "win_rate": 0.0, "fc_pct": 0.0,
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

def _orb_config_factory(params: dict, base_config: object) -> object:
    return dataclasses.replace(
        base_config,
        orb_enabled=True,
        orb_sl_ratio=params["sl_ratio"],
        orb_tp_ratio=params["tp_ratio"],
        orb_entry_deadline=params["entry_deadline"],
        orb_breakout_buffer=params["breakout_buffer"],
        orb_use_volume_filter=params["use_volume_filter"],
        orb_range_minutes=5,
        orb_min_range_pct=0.005,
        orb_max_range_pct=0.05,
        orb_rvol_min=1.5,
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
    for sl in SL_RATIOS:
        for tp in TP_RATIOS:
            for dl in ENTRY_DEADLINES:
                for bb in BREAKOUT_BUFFERS:
                    for vf in USE_VOL_FILTERS:
                        tag = (
                            f"sl{sl:.1f}_tp{tp:.1f}_dl{dl.replace(':', '')}"
                            f"_buf{bb:.2f}_vol{'Y' if vf else 'N'}"
                        )
                        combos.append({
                            "tag":               tag,
                            "sl_ratio":          sl,
                            "tp_ratio":          tp,
                            "entry_deadline":    dl,
                            "breakout_buffer":   bb,
                            "use_volume_filter": vf,
                        })
    return combos


# ---------------------------------------------------------------------------
# 워커 (ProcessPool spawn 필수)
# ---------------------------------------------------------------------------

def _orb_worker(args: tuple) -> dict:
    config, candles_bytes, market_map_bytes, ticker_to_market, bt_config, params_dict = args

    import sys, pickle as _p, asyncio as _a
    from loguru import logger as _l
    _l.remove()
    _l.add(sys.stderr, level="WARNING")

    from backtest.backtester_fast import ORBFastBacktester as _OBT
    from strategy.orb_strategy import ORBStrategy as _OS

    candles_cache: dict = _p.loads(candles_bytes)
    market_map: dict    = _p.loads(market_map_bytes)

    all_trades: list[dict] = []
    for tk, df in candles_cache.items():
        market = ticker_to_market.get(tk, "unknown")
        bt = _OBT(
            db=None, config=config, backtest_config=bt_config,
            ticker_market=market, market_strong_by_date=market_map,
        )
        strat = _OS(config)
        result = _a.run(bt.run_multi_day_cached(tk, df, strat))
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)

    stats = _compute_orb_stats(all_trades)
    return {**params_dict, **stats}


# ---------------------------------------------------------------------------
# 병렬 그리드 실행
# ---------------------------------------------------------------------------

def _run_orb_grid(combos: list[dict], cache: GridCache) -> list[dict]:
    cache.prepare_bytes()
    n_workers = max(2, min(4, (os.cpu_count() or 4) - 1))

    worker_args = [
        (
            _orb_config_factory(p, cache.base_config),
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
    print(f"[ORB GRID LARGECAP] {n}조합 × {len(cache.candles)}종목  workers={n_workers}", flush=True)

    try:
        from tqdm import tqdm as _tqdm
        _use_tqdm = True
    except ImportError:
        _use_tqdm = False

    ctx = mp.get_context("spawn")
    try:
        from concurrent.futures import ProcessPoolExecutor as _PPE
        with _PPE(max_workers=n_workers, mp_context=ctx) as ex:
            it = ex.map(_orb_worker, worker_args)
            if _use_tqdm:
                it = _tqdm(it, total=n, desc="orb largecap", unit="combo")
            for i, r in enumerate(it, 1):
                results.append(r)
                if not _use_tqdm:
                    elapsed = _time.time() - t0
                    eta = elapsed / i * (n - i) if i < n else 0
                    ok = _is_passing(r)
                    print(
                        f"  [{i:>2}/{n}] {r.get('tag',''):<45} "
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
            r = _orb_worker(wargs)
            results.append(r)
            if not _use_tqdm:
                elapsed = _time.time() - t0
                print(
                    f"  [{i:>2}/{n}] {r.get('tag',''):<45} "
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


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------

def _print_table(results: list[dict], title: str) -> None:
    print(f"\n{title}", flush=True)
    hdr = (
        f"{'태그':>45} | "
        f"{'PF':>6} {'PnL':>10} {'거래#':>5} {'승률':>6} "
        f"{'CL':>3} {'보유':>5} {'OK':>3}"
    )
    sep = "-" * len(hdr)
    print(sep, flush=True)
    print(hdr, flush=True)
    print(sep, flush=True)
    for r in sorted(results, key=lambda x: x.get("pf", 0.0), reverse=True):
        ok = "Y" if _is_passing(r) else ""
        print(
            f"{r.get('tag', ''):<45} | "
            f"{r.get('pf', 0):>6.3f} {int(r.get('pnl', 0)):>+10,} "
            f"{int(r.get('trades', 0)):>5} {r.get('win_rate', 0):>6.1%} "
            f"{r.get('max_consec_loss', 0):>3} {r.get('avg_hold_min', 0):>5.1f} "
            f"{ok:>3}",
            flush=True,
        )
    print(sep, flush=True)


# ---------------------------------------------------------------------------
# 보고서
# ---------------------------------------------------------------------------

def _write_report(
    old_results: list[dict],
    new_results: list[dict],
    passing: list[dict],
) -> None:
    out = Path("reports/orb_largecap_grid_result.md")
    out.parent.mkdir(exist_ok=True)

    def _md_rows(results: list[dict]) -> list[str]:
        cols = ["tag", "sl_ratio", "tp_ratio", "entry_deadline",
                "breakout_buffer", "use_volume_filter",
                "pf", "pnl", "trades", "win_rate",
                "max_consec_loss", "avg_hold_min", "tp_pct", "sl_pct", "fc_pct"]
        header = "| " + " | ".join(cols) + " |"
        sep    = "| " + " | ".join("---" for _ in cols) + " |"
        lines  = [header, sep]
        for r in sorted(results, key=lambda x: x.get("pf", 0.0), reverse=True):
            vals = []
            for c in cols:
                v = r.get(c, "-")
                if isinstance(v, float):
                    vals.append(f"{v:.4f}")
                elif isinstance(v, bool):
                    vals.append("Y" if v else "N")
                else:
                    vals.append(str(v))
            ok = " ✓" if _is_passing(r) else ""
            lines.append("| " + " | ".join(vals) + f"{ok} |")
        return lines

    lines: list[str] = [
        "# ORB(Opening Range Breakout) 전략 그리드 서치 — KOSPI 대형주",
        f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> 유니버스: {UNIVERSE_PATH} (KOSPI 50종목)",
        f"> OLD 구간: {OLD_START} ~ {OLD_END}",
        f"> NEW 구간: {NEW_START} ~ {NEW_END}",
        f"> 그리드: sl×tp×deadline×buf×vol "
        f"= {len(SL_RATIOS)}×{len(TP_RATIOS)}×{len(ENTRY_DEADLINES)}"
        f"×{len(BREAKOUT_BUFFERS)}×{len(USE_VOL_FILTERS)} = {len(old_results)}조합",
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

    lines += ["## 선정 기준 통과 조합", ""]

    if passing:
        lines += [
            "| 태그 | sl | tp | deadline | buf | vol | PF(OLD) | PnL(OLD) | 거래# | 승률 | CL | NEW PF |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for r in passing:
            lines.append(
                f"| {r.get('tag','')} "
                f"| {r.get('sl_ratio','')} "
                f"| {r.get('tp_ratio','')} "
                f"| {r.get('entry_deadline','')} "
                f"| {r.get('breakout_buffer','')} "
                f"| {'Y' if r.get('use_volume_filter') else 'N'} "
                f"| {r.get('pf',0):.3f} "
                f"| {int(r.get('pnl',0)):+,} "
                f"| {r.get('trades',0)} "
                f"| {r.get('win_rate',0):.1%} "
                f"| {r.get('max_consec_loss',0)} "
                f"| {r.get('new_pf',0):.3f} |"
            )
    else:
        lines += [
            "선정 기준 미달 — KOSPI 대형주 유니버스에서 ORB 전략 유효 조합 없음.",
            "",
            f"미달: PF≥{MIN_PF} AND 거래≥{MIN_TRADES} AND CL≤{MAX_CONSEC_LOSS} AND NEW PF>{MIN_NEW_PF}",
        ]

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[SAVED] {out}", flush=True)


# ---------------------------------------------------------------------------
# --verify 단일 백테스트 (원본 최선 파라미터: sl=1.5, tp=3.0, dl=09:30)
# ---------------------------------------------------------------------------

async def _run_verify(cache: GridCache, label: str) -> None:
    from backtest.backtester_fast import ORBFastBacktester as _OBT
    from strategy.orb_strategy import ORBStrategy as _OS

    cfg = _orb_config_factory(
        {"sl_ratio": 1.5, "tp_ratio": 3.0,
         "entry_deadline": "09:30", "breakout_buffer": 0.0,
         "use_volume_filter": True},
        cache.base_config,
    )

    all_trades: list[dict] = []
    for tk, df in cache.candles.items():
        market = cache.ticker_to_market.get(tk, "unknown")
        bt = _OBT(
            db=None, config=cfg, backtest_config=cache.bt_config,
            ticker_market=market, market_strong_by_date=cache.market_map,
        )
        strat = _OS(cfg)
        result = await bt.run_multi_day_cached(tk, df, strat)
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)

    s = _compute_orb_stats(all_trades)
    print(
        f"[VERIFY {label}] PF={s['pf']:.3f}  PnL={s['pnl']:+,}  "
        f"거래#{s['trades']}  승률={s['win_rate']:.1%}  "
        f"CL={s['max_consec_loss']}  보유={s['avg_hold_min']:.1f}분",
        flush=True,
    )
    print(f"  청산: {s['exit_counts']}", flush=True)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="ORB 전략 18조합 그리드 (대형주)")
    parser.add_argument("--verify", action="store_true",
                        help="원본 최선 파라미터(sl=1.5/tp=3.0/dl=09:30) 단일 검증")
    args = parser.parse_args()

    print(f"캔들 캐시 로드 중... ({UNIVERSE_PATH})", flush=True)
    cache_all = await load_candle_cache(OLD_START, NEW_END, universe_path=UNIVERSE_PATH)
    print(f"  {len(cache_all.candles)}종목 로드 완료", flush=True)

    old_cache = cache_all.filter_dates(OLD_START, OLD_END)
    new_cache = cache_all.filter_dates(NEW_START, NEW_END)

    if args.verify:
        print("\n[VERIFY] 원본 최선 파라미터 단일 실행...", flush=True)
        await _run_verify(old_cache, "OLD")
        if new_cache.candles:
            await _run_verify(new_cache, "NEW")
        return

    combos = _build_combos()
    print(
        f"\n총 {len(combos)}조합 "
        f"(sl×tp×deadline×buf×vol = "
        f"{len(SL_RATIOS)}×{len(TP_RATIOS)}×{len(ENTRY_DEADLINES)}"
        f"×{len(BREAKOUT_BUFFERS)}×{len(USE_VOL_FILTERS)})",
        flush=True,
    )

    print(f"\n[GRID OLD] {OLD_START} ~ {OLD_END}", flush=True)
    old_results = _run_orb_grid(combos, old_cache)
    _print_table(old_results, f"ORB 그리드 대형주 (OLD, {len(old_results)}조합)")

    print(f"\n[GRID NEW] {NEW_START} ~ {NEW_END}", flush=True)
    if new_cache.candles:
        new_results = _run_orb_grid(combos, new_cache)
        _print_table(new_results, f"ORB 그리드 대형주 (NEW, {len(new_results)}조합)")
    else:
        new_results = []
        print("  NEW 캔들 없음 — 생략", flush=True)

    new_map = {r["tag"]: r for r in new_results}
    passing = _select_best_combos(old_results, new_map)

    print(f"\n[선정] 기준 통과 조합: {len(passing)}개", flush=True)
    for r in passing:
        print(
            f"  {r['tag']}  OLD PF={r['pf']:.3f}  NEW PF={r['new_pf']:.3f}  "
            f"거래#{r['trades']}  CL={r['max_consec_loss']}",
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
