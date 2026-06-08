"""scripts/grid_trvol.py — TRVOL(개별 분봉 상대 거래량) 7조합 그리드.

기존 "전일 전체 누적거래량 × 2.0" 대비 "전일 동시간대 5분봉 × N배" 필터의 성능을
PF/PnL뿐 아니라 진입지연·수익캡처율·허수돌파율로 정량 비교한다.

비교 모드:
  baseline      : trvol_enabled=False, volume_ratio=2.0 (현행)
  trvol_only    : trvol만 사용, cumvol 비활성
  trvol_or_cumvol: trvol OR cumvol 중 하나만 충족해도 진입

파라미터 격자:
  trvol_ratio : [2.0, 3.0, 5.0]
  mode        : [trvol_only, trvol_or_cumvol]
  → 6조합 + baseline 1 = 총 7조합

추가 측정 지표:
  - 진입지연(분): BREAKOUT 최초 감지 → 실제 진입 시각 차이 (median, mean)
  - 수익캡처율(%): (peak_price - entry_price) / (peak_price - prev_close) median
  - 허수돌파율(%): peak_price < entry_price × 1.01 거래 비율

구간:
  OLD: 2025-04-01 ~ 2026-04-10
  NEW: 2026-04-11 ~ 2026-05-19

현행 baseline:
  OLD PF=4.881 / PnL=+295,690 / 거래=228

사용:
    python -u scripts/grid_trvol.py           # 전체 실행
    python -u scripts/grid_trvol.py --verify  # baseline 단일 검증만
"""
from __future__ import annotations

import asyncio
import dataclasses
import multiprocessing as mp
import os
import statistics
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

BASELINE = {
    "tag": "baseline",
    "pf": 4.881, "pnl": 295_690, "trades": 228,
    "win_rate": 0.557, "fc_pct": 40.4,
}

# ---------------------------------------------------------------------------
# 그리드 파라미터
# ---------------------------------------------------------------------------

TRVOL_RATIOS = [2.0, 3.0, 5.0]
MODES        = ["trvol_only", "trvol_or_cumvol"]

# ---------------------------------------------------------------------------
# KPI + 추가 지표 계산
# ---------------------------------------------------------------------------

def _compute_stats(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {
            "pf": 0.0, "pnl": 0, "trades": 0,
            "win_rate": 0.0, "avg_win": 0, "avg_loss": 0,
            "sl_cnt": 0, "trail_cnt": 0, "be_cnt": 0,
            "fade_cnt": 0, "fc_cnt": 0, "lu_cnt": 0,
            "fc_pct": 0.0, "sl_pct_exit": 0.0,
            "max_consec_loss": 0, "avg_hold_min": 0.0,
            "entry_delay_median": 0.0, "entry_delay_mean": 0.0,
            "capture_rate_median": 0.0, "fake_breakout_pct": 0.0,
            "exit_counts": {},
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

    # ── 추가 지표 ──────────────────────────────────────────────────────────
    delays: list[float] = []
    capture_rates: list[float] = []
    fake_count = 0

    for t in trades:
        entry_ts   = t.get("entry_ts")
        breakout_ts = t.get("breakout_ts")
        entry_price = t.get("entry_price", 0.0)
        highest    = t.get("highest_price", 0.0)
        chg_close  = t.get("entry_chg_from_close", 0.0)

        # 진입지연 (breakout_ts가 있는 거래만 계산)
        if entry_ts and breakout_ts:
            try:
                delay_min = (entry_ts - breakout_ts).total_seconds() / 60.0
                if delay_min >= 0:
                    delays.append(delay_min)
            except Exception:
                pass

        # 수익 캡처율: (peak - entry) / (peak - prev_close)
        if entry_price > 0 and highest > 0:
            # prev_close 역산: entry_chg_from_close = (entry - prev_close) / prev_close
            # → prev_close = entry_price / (1 + chg_close)
            if chg_close > -1.0:
                prev_close = entry_price / (1.0 + chg_close)
            else:
                prev_close = 0.0
            if highest > prev_close > 0:
                cap = (highest - entry_price) / (highest - prev_close)
                capture_rates.append(max(0.0, min(1.0, cap)))

        # 허수 돌파: peak < entry × 1.01
        if entry_price > 0 and highest > 0:
            if highest < entry_price * 1.01:
                fake_count += 1

    entry_delay_median = statistics.median(delays) if delays else 0.0
    entry_delay_mean   = sum(delays) / len(delays) if delays else 0.0
    capture_median     = statistics.median(capture_rates) if capture_rates else 0.0
    fake_breakout_pct  = fake_count / n * 100.0

    sl_cnt    = exits.get("stop_loss", 0)
    trail_cnt = exits.get("trailing_stop", 0)
    be_cnt    = exits.get("breakeven_stop", 0)
    fade_cnt  = exits.get("momentum_fade", 0)
    fc_cnt    = exits.get("forced_close", 0)
    lu_cnt    = exits.get("limit_up_exit", 0)

    return {
        "pf":                  round(gp / gl, 4) if gl > 0 else float("inf"),
        "pnl":                 int(pnl),
        "trades":              n,
        "win_rate":            round(len(wins) / n, 4),
        "avg_win":             int(sum(wins) / len(wins)) if wins else 0,
        "avg_loss":            int(sum(loss) / len(loss)) if loss else 0,
        "sl_cnt":              sl_cnt,
        "trail_cnt":           trail_cnt,
        "be_cnt":              be_cnt,
        "fade_cnt":            fade_cnt,
        "fc_cnt":              fc_cnt,
        "lu_cnt":              lu_cnt,
        "fc_pct":              round(fc_cnt / n * 100, 1),
        "sl_pct_exit":         round(sl_cnt / n * 100, 1),
        "max_consec_loss":     max_cl,
        "avg_hold_min":        round(avg_hold, 1),
        "entry_delay_median":  round(entry_delay_median, 1),
        "entry_delay_mean":    round(entry_delay_mean, 1),
        "capture_rate_median": round(capture_median * 100, 1),
        "fake_breakout_pct":   round(fake_breakout_pct, 1),
        "exit_counts":         dict(exits),
    }


# ---------------------------------------------------------------------------
# config factory
# ---------------------------------------------------------------------------

def _trvol_config_factory(
    mode: str, trvol_ratio: float, base_config: object
) -> object:
    """7조합 중 하나에 맞는 config 생성."""
    if mode == "baseline":
        return dataclasses.replace(
            base_config,
            trvol_enabled=False,
        )
    trvol_only = (mode == "trvol_only")
    return dataclasses.replace(
        base_config,
        trvol_enabled=True,
        trvol_ratio=trvol_ratio,
        trvol_only_mode=trvol_only,
        trvol_min_prev_volume=1000,
    )


# ---------------------------------------------------------------------------
# 워커 (subprocess)
# ---------------------------------------------------------------------------

def _trvol_worker(args: tuple) -> dict:
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
        print(f"[ERROR] worker 실패 {params_dict.get('tag','?')}: {exc}", flush=True)
        traceback.print_exc()
        return {**params_dict, **_compute_stats([])}


# ---------------------------------------------------------------------------
# 조합 빌더
# ---------------------------------------------------------------------------

def _build_combos() -> list[dict]:
    combos = [{"mode": "baseline", "trvol_ratio": 0.0, "tag": "baseline"}]
    for mode in MODES:
        for ratio in TRVOL_RATIOS:
            tag = f"{mode[:7]}_r{ratio:.0f}"
            combos.append({"mode": mode, "trvol_ratio": ratio, "tag": tag})
    return combos


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
            _trvol_config_factory(p["mode"], p["trvol_ratio"], cache.base_config),
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
            for i, r in enumerate(ex.map(_trvol_worker, worker_args), 1):
                results.append(r)
                elapsed = _time.time() - t0
                is_baseline = r.get("mode") == "baseline"
                marker = " ← baseline" if is_baseline else ""
                print(
                    f"  [{i}/{n}] {r.get('tag',''):<16}  "
                    f"PF={r.get('pf', 0):.3f}  PnL={r.get('pnl', 0):+,}  "
                    f"거래={r.get('trades', 0)}  승률={r.get('win_rate', 0):.1%}  "
                    f"지연={r.get('entry_delay_median', 0):.1f}분  "
                    f"캡처={r.get('capture_rate_median', 0):.1f}%  "
                    f"허수={r.get('fake_breakout_pct', 0):.1f}%  "
                    f"({elapsed:.0f}s){marker}",
                    flush=True,
                )
    except Exception as exc:
        print(f"[WARN] Pool 실패 ({exc}), 순차 실행 전환", flush=True)
        results = []
        for i, wargs in enumerate(worker_args, 1):
            r = _trvol_worker(wargs)
            results.append(r)
            elapsed = _time.time() - t0
            print(
                f"  [{i}/{n}] {r.get('tag',''):<16}  PF={r.get('pf', 0):.3f}  ({elapsed:.0f}s)",
                flush=True,
            )

    elapsed = _time.time() - t0
    print(f"[DONE] {n}조합 완료 ({elapsed:.1f}s)", flush=True)
    return results


# ---------------------------------------------------------------------------
# 콘솔 출력
# ---------------------------------------------------------------------------

_COMBO_ORDER = ["baseline"] + [
    f"{mode[:7]}_r{ratio:.0f}"
    for mode in MODES
    for ratio in TRVOL_RATIOS
]


def _tag_order(r: dict) -> int:
    tag = r.get("tag", "")
    try:
        return _COMBO_ORDER.index(tag)
    except ValueError:
        return 99


def _print_table(results: list[dict], title: str) -> None:
    print(f"\n{title}", flush=True)
    hdr = (
        f"{'tag':>18} | "
        f"{'PF':>6} {'PnL':>10} {'거래#':>5} {'승률':>6} "
        f"{'지연med':>7} {'지연avg':>7} {'캡처%':>6} {'허수%':>6} "
        f"{'FC%':>5} {'CL':>3} {'보유(분)':>8}"
    )
    sep = "-" * len(hdr)
    print(sep, flush=True)
    print(hdr, flush=True)
    print(sep, flush=True)
    for r in sorted(results, key=_tag_order):
        is_bl = r.get("mode") == "baseline"
        marker = " ←" if is_bl else "  "
        print(
            f"{r.get('tag', ''):>18} | "
            f"{r.get('pf', 0):>6.3f} {int(r.get('pnl', 0)):>+10,} "
            f"{int(r.get('trades', 0)):>5} {r.get('win_rate', 0):>6.1%} "
            f"{r.get('entry_delay_median', 0):>7.1f} {r.get('entry_delay_mean', 0):>7.1f} "
            f"{r.get('capture_rate_median', 0):>6.1f} {r.get('fake_breakout_pct', 0):>6.1f} "
            f"{r.get('fc_pct', 0):>5.1f} {r.get('max_consec_loss', 0):>3} "
            f"{r.get('avg_hold_min', 0):>8.1f}{marker}",
            flush=True,
        )
    print(sep, flush=True)


# ---------------------------------------------------------------------------
# 보고서
# ---------------------------------------------------------------------------

def _write_report(
    old_results: list[dict],
    new_results: list[dict],
    old_elapsed: float,
    new_elapsed: float,
) -> None:
    out = Path("reports/trvol_grid_result.md")
    out.parent.mkdir(exist_ok=True)

    def _md_rows(results: list[dict]) -> list[str]:
        header = (
            "| 조합 | PF | PnL | 거래# | 승률 | "
            "지연(분)med | 지연(분)avg | 캡처율% | 허수돌파% | "
            "FC% | SL% | CL | 보유(분) |"
        )
        sep = "| --- " * 14 + "|"
        lines = [header, sep]
        for r in sorted(results, key=_tag_order):
            is_bl = r.get("mode") == "baseline"
            note = " ✓" if is_bl else ""
            n = max(r.get("trades", 1), 1)
            sl_pct = round(r.get("sl_cnt", 0) / n * 100, 1)
            lines.append(
                f"| {r.get('tag', '')}{note} "
                f"| {r.get('pf', 0):.4f} "
                f"| {int(r.get('pnl', 0)):+,} "
                f"| {r.get('trades', 0)} "
                f"| {r.get('win_rate', 0):.1%} "
                f"| {r.get('entry_delay_median', 0):.1f} "
                f"| {r.get('entry_delay_mean', 0):.1f} "
                f"| {r.get('capture_rate_median', 0):.1f}% "
                f"| {r.get('fake_breakout_pct', 0):.1f}% "
                f"| {r.get('fc_pct', 0):.1f}% "
                f"| {sl_pct:.1f}% "
                f"| {r.get('max_consec_loss', 0)} "
                f"| {r.get('avg_hold_min', 0):.1f} |"
            )
        return lines

    new_map = {r.get("tag", ""): r for r in new_results}

    lines: list[str] = [
        "# TRVOL 그리드 서치 — 개별 분봉 상대 거래량 필터",
        f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> OLD 구간: {OLD_START} ~ {OLD_END}  ({old_elapsed:.0f}s)",
        f"> NEW 구간: {NEW_START} ~ {NEW_END}  ({new_elapsed:.0f}s)",
        "",
        "## 현행 baseline (volume_ratio=2.0, TRVOL 비활성)",
        "",
        "| 구분 | PF | PnL | 거래# | 승률 | FC% |",
        "| --- | --- | --- | --- | --- | --- |",
        f"| OLD | {BASELINE['pf']:.3f} | +{BASELINE['pnl']:,} "
        f"| {BASELINE['trades']} | {BASELINE['win_rate']:.1%} | {BASELINE['fc_pct']:.1f}% |",
        "",
        "## 추가 지표 설명",
        "",
        "- **지연(분)**: BREAKOUT 최초 감지 → 진입 시각 (분). 낮을수록 조기 진입.",
        "- **캡처율%**: `(peak_price − entry_price) / (peak_price − prev_close)` × 100 (median). 높을수록 상승 초반 진입.",
        "- **허수돌파%**: 진입 후 peak < entry × 1.01 거래 비율. 낮을수록 진짜 돌파.",
        "",
        "## 검증 기준",
        "",
        "- OLD PF ≥ 3.0 (baseline 4.881 대비 −20% 이내)",
        "- NEW PF > 0.69 (baseline 대비 개선)",
        "- 진입지연 median ≤ 10분 (baseline 대비 50% 이상 단축)",
        "- 허수돌파율 ≤ 30%",
        "",
        f"## 그리드 결과 - OLD ({len(old_results)}조합)",
        "",
    ] + _md_rows(old_results) + [""]

    if new_results:
        lines += [
            f"## 그리드 결과 - NEW ({len(new_results)}조합)",
            "",
        ] + _md_rows(new_results) + [""]

    # OLD / NEW 비교표
    lines += [
        "## OLD / NEW 비교",
        "",
        "| 조합 | OLD PF | OLD PnL | OLD 거래# | OLD 지연med | OLD 캡처% "
        "| NEW PF | NEW PnL | NEW 거래# |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in sorted(old_results, key=_tag_order):
        tag = r.get("tag", "")
        nr = new_map.get(tag, {})
        is_bl = r.get("mode") == "baseline"
        note = " ✓" if is_bl else ""
        lines.append(
            f"| {tag}{note} "
            f"| {r.get('pf', 0):.4f} "
            f"| {int(r.get('pnl', 0)):+,} "
            f"| {r.get('trades', 0)} "
            f"| {r.get('entry_delay_median', 0):.1f}분 "
            f"| {r.get('capture_rate_median', 0):.1f}% "
            f"| {nr.get('pf', 0):.4f} "
            f"| {int(nr.get('pnl', 0)):+,} "
            f"| {nr.get('trades', 0)} |"
        )

    lines += [
        "",
        "## 선정 결론",
        "",
        "<!-- 그리드 실행 후 수동 기재 -->",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[SAVED] {out}", flush=True)


# ---------------------------------------------------------------------------
# --verify 단일 백테스트
# ---------------------------------------------------------------------------

async def _run_verify(cache: GridCache) -> None:
    from backtest.backtester_fast import FastBacktester as _FBT
    from strategy.momentum_strategy import MomentumStrategy as _MS

    cfg = _trvol_config_factory("baseline", 0.0, cache.base_config)

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
    print("\n[VERIFY] baseline 단일 백테스트 (trvol_enabled=False, volume_ratio=2.0)")
    print(
        f"  PF={stats['pf']:.3f}  PnL={stats['pnl']:+,}  "
        f"거래#{stats['trades']}  승률={stats['win_rate']:.1%}  "
        f"CL={stats['max_consec_loss']}  보유={stats['avg_hold_min']:.1f}분"
    )
    print(
        f"  진입지연 median={stats['entry_delay_median']:.1f}분 "
        f"mean={stats['entry_delay_mean']:.1f}분  "
        f"캡처율={stats['capture_rate_median']:.1f}%  "
        f"허수돌파={stats['fake_breakout_pct']:.1f}%"
    )
    print(f"  청산분포: {stats['exit_counts']}")
    print(f"\n  baseline: PF={BASELINE['pf']:.3f} / PnL=+{BASELINE['pnl']:,}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="TRVOL 7조합 그리드")
    parser.add_argument("--verify", action="store_true", help="baseline 단일 검증만")
    args = parser.parse_args()

    print("캔들 캐시 로드 중...", flush=True)
    cache_all = asyncio.run(load_candle_cache(OLD_START, NEW_END))
    print(f"  {len(cache_all.candles)}종목 로드 완료", flush=True)

    old_cache = cache_all.filter_dates(OLD_START, OLD_END)
    new_cache = cache_all.filter_dates(NEW_START, NEW_END)

    if args.verify:
        print("\n[VERIFY] OLD 구간 baseline 재현...", flush=True)
        asyncio.run(_run_verify(old_cache))
        print("\n[VERIFY 완료]", flush=True)
        return

    combos = _build_combos()
    print(
        f"\n총 {len(combos)}조합 -- baseline + {len(MODES)} 모드 x {len(TRVOL_RATIOS)} ratio",
        flush=True,
    )
    print(f"  baseline: PF={BASELINE['pf']:.3f} / PnL=+{BASELINE['pnl']:,}", flush=True)

    # OLD 구간
    print(f"\n[GRID OLD] {OLD_START} ~ {OLD_END}", flush=True)
    t0_old = _time.time()
    old_results = _run_grid(combos, old_cache)
    old_elapsed = _time.time() - t0_old
    _print_table(old_results, f"TRVOL 그리드 OLD ({len(old_results)}조합)")

    # NEW 구간
    print(f"\n[GRID NEW] {NEW_START} ~ {NEW_END}", flush=True)
    t0_new = _time.time()
    if new_cache.candles:
        new_results = _run_grid(combos, new_cache)
        new_elapsed = _time.time() - t0_new
        _print_table(new_results, f"TRVOL 그리드 NEW ({len(new_results)}조합)")
    else:
        new_results = []
        new_elapsed = 0.0
        print("  NEW 캔들 없음 - 생략", flush=True)

    _write_report(old_results, new_results, old_elapsed, new_elapsed)


if __name__ == "__main__":
    main()
