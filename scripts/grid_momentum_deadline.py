"""scripts/grid_momentum_deadline.py - 모멘텀 전략 entry_deadline 5조합 그리드.

현재 운영 파라미터(vr2.0, ATR 청산, BE3, trail 등)를 그대로 유지하면서
매수 허용 시간(buy_time_end)만 변경했을 때의 성능을 검증한다.

파라미터 격자:
  entry_deadline: ["10:00", "11:00", "12:00", "13:00", "14:00"]  (5조합)

구간:
  OLD: 2025-04-01 ~ 2026-04-10
  NEW: 2026-04-11 ~ 2026-05-19

현행 baseline (entry_deadline=12:00):
  OLD PF=4.881 / PnL=+295,690 / 거래=228

사용:
    python -u scripts/grid_momentum_deadline.py           # 전체 실행
    python -u scripts/grid_momentum_deadline.py --verify  # 단일 파라미터 검증만
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

# 현행 baseline (entry_deadline=12:00, ATR 청산, 장중 필터 제외)
BASELINE = {
    "deadline": "12:00",
    "pf": 4.881, "pnl": 295_690, "trades": 228,
    "win_rate": 0.557, "fc_pct": 40.4,
}

# ---------------------------------------------------------------------------
# 그리드 파라미터
# ---------------------------------------------------------------------------

ENTRY_DEADLINES = ["10:00", "11:00", "12:00", "13:00", "14:00"]

# ---------------------------------------------------------------------------
# KPI 계산
# ---------------------------------------------------------------------------

def _compute_stats(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {
            "pf": 0.0, "pnl": 0, "trades": 0,
            "win_rate": 0.0, "avg_win": 0, "avg_loss": 0,
            "tp_cnt": 0, "sl_cnt": 0, "trail_cnt": 0, "be_cnt": 0,
            "fade_cnt": 0, "fc_cnt": 0,
            "fc_pct": 0.0, "sl_pct_exit": 0.0,
            "max_consec_loss": 0, "avg_hold_min": 0.0, "exit_counts": {},
        }

    gp   = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl   = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    pnl  = sum(t["pnl"] for t in trades)
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    loss = [t["pnl"] for t in trades if t["pnl"] < 0]
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

    sl_cnt    = exits.get("stop_loss", 0)
    trail_cnt = exits.get("trailing_stop", 0)
    be_cnt    = exits.get("breakeven_stop", 0)
    fade_cnt  = exits.get("momentum_fade", 0)
    fc_cnt    = exits.get("forced_close", 0)
    lu_cnt    = exits.get("limit_up_exit", 0)
    tp_cnt    = exits.get("tp_exit", 0)

    return {
        "pf":              round(gp / gl, 4) if gl > 0 else float("inf"),
        "pnl":             int(pnl),
        "trades":          n,
        "win_rate":        round(len(wins) / n, 4),
        "avg_win":         int(sum(wins) / len(wins)) if wins else 0,
        "avg_loss":        int(sum(loss) / len(loss)) if loss else 0,
        "sl_cnt":          sl_cnt,
        "trail_cnt":       trail_cnt,
        "be_cnt":          be_cnt,
        "fade_cnt":        fade_cnt,
        "fc_cnt":          fc_cnt,
        "lu_cnt":          lu_cnt,
        "tp_cnt":          tp_cnt,
        "fc_pct":          round(fc_cnt / n * 100, 1),
        "sl_pct_exit":     round(sl_cnt / n * 100, 1),
        "max_consec_loss": max_cl,
        "avg_hold_min":    round(avg_hold, 1),
        "exit_counts":     dict(exits),
    }


# ---------------------------------------------------------------------------
# config factory - buy_time_end만 변경, 나머지 현행 유지
# ---------------------------------------------------------------------------

def _deadline_config_factory(deadline: str, base_config: object) -> object:
    return dataclasses.replace(
        base_config,
        buy_time_limit_enabled=True,
        buy_time_end=deadline,
    )


# ---------------------------------------------------------------------------
# 워커
# ---------------------------------------------------------------------------

def _deadline_worker(args: tuple) -> dict:
    """단일 entry_deadline 백테스트 - subprocess 실행용."""
    config, candles_bytes, market_map_bytes, ticker_to_market, bt_config, params_dict = args

    import sys as _sys, pickle as _p, asyncio as _a
    from loguru import logger as _l
    _l.remove()
    _l.add(_sys.stderr, level="WARNING")

    try:
        from backtest.backtester_fast import FastBacktester as _FBT
        from strategy.momentum_strategy import MomentumStrategy as _MS

        candles_cache: dict = _p.loads(candles_bytes)
        market_map: dict    = _p.loads(market_map_bytes)

        all_trades: list[dict] = []
        for tk, df in candles_cache.items():
            market = ticker_to_market.get(tk, "unknown")
            bt = _FBT(
                db=None, config=config, backtest_config=bt_config,
                ticker_market=market, market_strong_by_date=market_map,
            )
            strat = _MS(config)
            result = _a.run(bt.run_multi_day_cached(tk, df, strat))
            for t in result.get("trades", []):
                t["ticker"] = tk
                all_trades.append(t)

        stats = _compute_stats(all_trades)
        return {**params_dict, **stats}
    except Exception as exc:
        import traceback
        print(f"[ERROR] worker 실패 {params_dict.get('deadline','?')}: {exc}", flush=True)
        traceback.print_exc()
        return {**params_dict, **_compute_stats([])}


# ---------------------------------------------------------------------------
# 조합 빌더
# ---------------------------------------------------------------------------

def _build_combos() -> list[dict]:
    return [{"deadline": d, "tag": f"dl{d.replace(':', '')}"} for d in ENTRY_DEADLINES]


# ---------------------------------------------------------------------------
# 병렬 그리드 실행
# ---------------------------------------------------------------------------

def _run_grid(
    combos: list[dict], cache: GridCache, *, max_workers: int | None = None
) -> list[dict]:
    cache.prepare_bytes()
    n_workers = max_workers or min(len(combos), max(2, (os.cpu_count() or 4) - 1))

    worker_args = [
        (
            _deadline_config_factory(p["deadline"], cache.base_config),
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

    ctx = mp.get_context("spawn")
    try:
        from concurrent.futures import ProcessPoolExecutor as _PPE
        with _PPE(max_workers=n_workers, mp_context=ctx) as ex:
            for i, r in enumerate(ex.map(_deadline_worker, worker_args), 1):
                results.append(r)
                elapsed = _time.time() - t0
                is_baseline = r.get("deadline") == BASELINE["deadline"]
                marker = " ← baseline" if is_baseline else ""
                print(
                    f"  [{i}/{n}] {r.get('tag',''):<10}  "
                    f"PF={r.get('pf', 0):.3f}  PnL={r.get('pnl', 0):+,}  "
                    f"거래={r.get('trades', 0)}  승률={r.get('win_rate', 0):.1%}  "
                    f"FC%={r.get('fc_pct', 0):.1f}  보유={r.get('avg_hold_min', 0):.1f}분  "
                    f"CL={r.get('max_consec_loss', 0)}  ({elapsed:.0f}s){marker}",
                    flush=True,
                )
    except Exception as exc:
        print(f"[WARN] Pool 실패 ({exc}), 순차 실행 전환", flush=True)
        results = []
        for i, wargs in enumerate(worker_args, 1):
            r = _deadline_worker(wargs)
            results.append(r)
            elapsed = _time.time() - t0
            print(
                f"  [{i}/{n}] {r.get('tag',''):<10}  "
                f"PF={r.get('pf', 0):.3f}  ({elapsed:.0f}s)",
                flush=True,
            )

    elapsed = _time.time() - t0
    print(f"[DONE] {n}조합 완료 ({elapsed:.1f}s)", flush=True)
    return results


# ---------------------------------------------------------------------------
# 콘솔 출력
# ---------------------------------------------------------------------------

def _print_table(results: list[dict], title: str) -> None:
    print(f"\n{title}", flush=True)
    hdr = (
        f"{'deadline':>10} | "
        f"{'PF':>6} {'PnL':>10} {'거래#':>5} {'승률':>6} "
        f"{'SL%':>5} {'trail%':>6} {'BE%':>5} {'fade%':>5} "
        f"{'FC%':>5} {'CL':>3} {'보유(분)':>8}"
    )
    sep = "-" * len(hdr)
    print(sep, flush=True)
    print(hdr, flush=True)
    print(sep, flush=True)
    for r in sorted(results, key=lambda x: x.get("pf", 0.0), reverse=True):
        n = max(r.get("trades", 1), 1)
        sl_pct   = round(r.get("sl_cnt",    0) / n * 100, 1)
        trail_pct= round(r.get("trail_cnt", 0) / n * 100, 1)
        be_pct   = round(r.get("be_cnt",    0) / n * 100, 1)
        fade_pct = round(r.get("fade_cnt",  0) / n * 100, 1)
        is_bl = r.get("deadline") == BASELINE["deadline"]
        marker = " ←" if is_bl else "  "
        print(
            f"{r.get('deadline', ''):>10} | "
            f"{r.get('pf', 0):>6.3f} {int(r.get('pnl', 0)):>+10,} "
            f"{int(r.get('trades', 0)):>5} {r.get('win_rate', 0):>6.1%} "
            f"{sl_pct:>5.1f} {trail_pct:>6.1f} {be_pct:>5.1f} {fade_pct:>5.1f} "
            f"{r.get('fc_pct', 0):>5.1f} {r.get('max_consec_loss', 0):>3} "
            f"{r.get('avg_hold_min', 0):>8.1f}{marker}",
            flush=True,
        )
    print(sep, flush=True)


# ---------------------------------------------------------------------------
# 보고서 생성
# ---------------------------------------------------------------------------

def _write_report(
    old_results: list[dict],
    new_results: list[dict],
    old_elapsed: float,
    new_elapsed: float,
) -> None:
    out = Path("reports/momentum_deadline_grid_result.md")
    out.parent.mkdir(exist_ok=True)

    def _md_rows(results: list[dict]) -> list[str]:
        header = (
            "| deadline | PF | PnL | 거래# | 승률 | "
            "SL청산% | trail청산% | BE청산% | fade청산% | FC% | CL | 보유(분) |"
        )
        sep = "| --- " * 12 + "|"
        lines = [header, sep]
        for r in sorted(results, key=lambda x: ENTRY_DEADLINES.index(x.get("deadline", "12:00"))):
            n = max(r.get("trades", 1), 1)
            sl_pct   = round(r.get("sl_cnt",    0) / n * 100, 1)
            trail_pct= round(r.get("trail_cnt", 0) / n * 100, 1)
            be_pct   = round(r.get("be_cnt",    0) / n * 100, 1)
            fade_pct = round(r.get("fade_cnt",  0) / n * 100, 1)
            is_bl = r.get("deadline") == BASELINE["deadline"]
            note = " ✓ baseline" if is_bl else ""
            lines.append(
                f"| {r.get('deadline','')} "
                f"| {r.get('pf',0):.4f} "
                f"| {int(r.get('pnl',0)):+,} "
                f"| {r.get('trades',0)} "
                f"| {r.get('win_rate',0):.1%} "
                f"| {sl_pct:.1f}% "
                f"| {trail_pct:.1f}% "
                f"| {be_pct:.1f}% "
                f"| {fade_pct:.1f}% "
                f"| {r.get('fc_pct',0):.1f}% "
                f"| {r.get('max_consec_loss',0)} "
                f"| {r.get('avg_hold_min',0):.1f}{note} |"
            )
        return lines

    # NEW 구간 PF 맵
    new_map = {r.get("deadline", ""): r for r in new_results}

    lines: list[str] = [
        "# 모멘텀 entry_deadline 그리드 서치",
        f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> OLD 구간: {OLD_START} ~ {OLD_END}  ({old_elapsed:.0f}s)",
        f"> NEW 구간: {NEW_START} ~ {NEW_END}  ({new_elapsed:.0f}s)",
        f"> 검증 파라미터: entry_deadline = {ENTRY_DEADLINES}",
        f"> 나머지 파라미터: 현행 모멘텀 설정 유지 (vr2.0, ATR 청산, trail, BE3, fade 등)",
        "",
        "## 현행 baseline (entry_deadline=12:00, ATR 청산)",
        "",
        "| 구분 | PF | PnL | 거래# | 승률 | FC% |",
        "| --- | --- | --- | --- | --- | --- |",
        f"| OLD (장중 필터 제외) | {BASELINE['pf']:.3f} | +{BASELINE['pnl']:,} "
        f"| {BASELINE['trades']} | {BASELINE['win_rate']:.1%} | {BASELINE['fc_pct']:.1f}% |",
        "",
        f"## 그리드 결과 - OLD ({len(old_results)}조합)",
        "",
    ] + _md_rows(old_results) + [""]

    if new_results:
        lines += [
            f"## 그리드 결과 - NEW ({len(new_results)}조합)",
            "",
        ] + _md_rows(new_results) + [""]

    # OLD/NEW 비교표
    lines += [
        "## OLD / NEW 비교 (deadline 순)",
        "",
        "| deadline | OLD PF | OLD PnL | OLD 거래# | NEW PF | NEW PnL | NEW 거래# |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in sorted(old_results, key=lambda x: ENTRY_DEADLINES.index(x.get("deadline", "12:00"))):
        dl = r.get("deadline", "")
        nr = new_map.get(dl, {})
        is_bl = dl == BASELINE["deadline"]
        note = " ✓" if is_bl else ""
        lines.append(
            f"| {dl}{note} "
            f"| {r.get('pf',0):.4f} "
            f"| {int(r.get('pnl',0)):+,} "
            f"| {r.get('trades',0)} "
            f"| {nr.get('pf',0):.4f} "
            f"| {int(nr.get('pnl',0)):+,} "
            f"| {nr.get('trades',0)} |"
        )

    lines += [""]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[SAVED] {out}", flush=True)


# ---------------------------------------------------------------------------
# --verify 단일 백테스트 (baseline 재현)
# ---------------------------------------------------------------------------

async def _run_verify(cache: GridCache) -> None:
    from backtest.backtester_fast import FastBacktester as _FBT
    from strategy.momentum_strategy import MomentumStrategy as _MS

    cfg = _deadline_config_factory("12:00", cache.base_config)

    all_trades: list[dict] = []
    for tk, df in cache.candles.items():
        market = cache.ticker_to_market.get(tk, "unknown")
        bt = _FBT(
            db=None, config=cfg, backtest_config=cache.bt_config,
            ticker_market=market, market_strong_by_date=cache.market_map,
        )
        strat = _MS(cfg)
        result = await bt.run_multi_day_cached(tk, df, strat)
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)

    stats = _compute_stats(all_trades)
    print("\n[VERIFY] 현행 설정 단일 백테스트 (entry_deadline=12:00)")
    print(
        f"  PF={stats['pf']:.3f}  PnL={stats['pnl']:+,}  "
        f"거래#{stats['trades']}  승률={stats['win_rate']:.1%}  "
        f"연속손실={stats['max_consec_loss']}  평균보유={stats['avg_hold_min']:.1f}분"
    )
    print(f"  청산분포: {stats['exit_counts']}")
    print(f"  FC={stats['fc_pct']:.1f}%  SL={stats['sl_pct_exit']:.1f}%")
    print(f"\n  baseline: PF={BASELINE['pf']:.3f} / PnL=+{BASELINE['pnl']:,}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="모멘텀 entry_deadline 5조합 그리드")
    parser.add_argument("--verify", action="store_true", help="baseline 단일 파라미터 검증만")
    args = parser.parse_args()

    print("캔들 캐시 로드 중...", flush=True)
    cache_all = asyncio.run(load_candle_cache(OLD_START, NEW_END))
    print(f"  {len(cache_all.candles)}종목 로드 완료", flush=True)

    old_cache = cache_all.filter_dates(OLD_START, OLD_END)
    new_cache = cache_all.filter_dates(NEW_START, NEW_END)

    if args.verify:
        print("\n[VERIFY] OLD 구간 baseline 재현 (entry_deadline=12:00)...", flush=True)
        asyncio.run(_run_verify(old_cache))
        print("\n[VERIFY 완료]", flush=True)
        return

    combos = _build_combos()
    print(
        f"\n총 {len(combos)}조합 - entry_deadline: {ENTRY_DEADLINES}",
        flush=True,
    )
    print(f"  baseline: PF={BASELINE['pf']:.3f} / PnL=+{BASELINE['pnl']:,} (deadline=12:00)", flush=True)

    # ── OLD 구간 ──────────────────────────────────────────────────────────────
    print(f"\n[GRID OLD] {OLD_START} ~ {OLD_END}", flush=True)
    t0_old = _time.time()
    old_results = _run_grid(combos, old_cache)
    old_elapsed = _time.time() - t0_old
    _print_table(old_results, f"entry_deadline 그리드 (OLD, {len(old_results)}조합)")

    # ── NEW 구간 ──────────────────────────────────────────────────────────────
    print(f"\n[GRID NEW] {NEW_START} ~ {NEW_END}", flush=True)
    t0_new = _time.time()
    if new_cache.candles:
        new_results = _run_grid(combos, new_cache)
        new_elapsed = _time.time() - t0_new
        _print_table(new_results, f"entry_deadline 그리드 (NEW, {len(new_results)}조합)")
    else:
        new_results = []
        new_elapsed = 0.0
        print("  NEW 캔들 없음 - 생략", flush=True)

    _write_report(old_results, new_results, old_elapsed, new_elapsed)


if __name__ == "__main__":
    main()
