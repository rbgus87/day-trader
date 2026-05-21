"""scripts/grid_vwap_largecap.py — VWAP 리버전 전략 72조합 그리드 (KOSPI 대형주).

grid_vwap.py 와 동일 파라미터·선정 기준,
유니버스만 config/universe_largecap.yaml (KOSPI 50종목) 사용.

그리드 파라미터:
  entry_deviation : [-1.0%, -1.5%, -2.0%, -2.5%]
  stop_loss_pct   : [1.0%, 1.5%, 2.0%]
  tp_above_vwap   : [0.0%, 0.3%, 0.5%]
  entry_end       : ["13:00", "14:00"]
  총 72조합

구간:
  OLD: 2025-04-01 ~ 2026-04-10
  NEW: 2026-04-11 ~ 2026-05-12

선정 기준 (OLD 기준):
  PF >= 1.5 / 거래 >= 30건 / maxCL <= 8 / NEW PF > 1.0

결과: reports/vwap_largecap_grid_result.md

사용:
    python -u scripts/grid_vwap_largecap.py
    python -u scripts/grid_vwap_largecap.py --verify
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
NEW_END   = "2026-05-12"

UNIVERSE_PATH = "config/universe_largecap.yaml"

# ---------------------------------------------------------------------------
# 그리드 파라미터
# ---------------------------------------------------------------------------

ENTRY_DEV_VALS = [-0.010, -0.015, -0.020, -0.025]
SL_PCT_VALS    = [0.010,  0.015,  0.020]
TP_VALS        = [0.000,  0.003,  0.005]
ENTRY_END_VALS = ["13:00", "14:00"]

# ---------------------------------------------------------------------------
# 선정 기준
# ---------------------------------------------------------------------------

PF_THRESHOLD     = 1.5
MIN_TRADES       = 30
MAX_CONSEC_LOSS  = 8
NEW_PF_THRESHOLD = 1.0


# ---------------------------------------------------------------------------
# 통계
# ---------------------------------------------------------------------------

def compute_vwap_stats(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {
            "pf": 0.0, "pnl": 0, "trades": 0, "win_rate": 0.0,
            "vwap_return_rate": 0.0, "avg_profit": 0.0, "avg_loss": 0.0,
            "avg_hold_min": 0.0, "max_consec_loss": 0,
            "fc_pct": 0.0, "sl_pct": 0.0, "exit_counts": {},
        }

    gp   = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl   = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] < 0]
    exits = Counter(t.get("exit_reason", "?") for t in trades)

    hold_mins = []
    for t in trades:
        ets, xts = t.get("entry_ts"), t.get("exit_ts")
        if ets and xts:
            hold_mins.append((xts - ets).total_seconds() / 60.0)

    max_cl, cur_cl = 0, 0
    for t in sorted(trades, key=lambda x: x.get("entry_ts") or datetime.min):
        if t["pnl"] < 0:
            cur_cl += 1
            max_cl = max(max_cl, cur_cl)
        else:
            cur_cl = 0

    return {
        "pf":               round(gp / gl, 4) if gl > 0 else float("inf"),
        "pnl":              int(sum(t["pnl"] for t in trades)),
        "trades":           n,
        "win_rate":         round(len(wins) / n, 4),
        "vwap_return_rate": round(exits.get("vwap_exit", 0) / n, 4),
        "avg_profit":       round(sum(wins) / len(wins), 1) if wins else 0.0,
        "avg_loss":         round(sum(losses) / len(losses), 1) if losses else 0.0,
        "avg_hold_min":     round(sum(hold_mins) / len(hold_mins), 1) if hold_mins else 0.0,
        "max_consec_loss":  max_cl,
        "fc_pct":           round(exits.get("forced_close", 0) / n * 100, 2),
        "sl_pct":           round(exits.get("stop_loss", 0) / n * 100, 2),
        "exit_counts":      dict(exits),
    }


# ---------------------------------------------------------------------------
# 워커 (ProcessPool spawn — top-level 필수)
# ---------------------------------------------------------------------------

def _vwap_worker(args: tuple) -> dict:
    (config, candles_bytes, _mm_bytes, _tm, bt_config, params_dict) = args

    from loguru import logger as _l
    _l.remove()
    _l.add(sys.stderr, level="WARNING")

    import asyncio as _asyncio
    import pickle as _pickle
    from backtest.backtester_fast import VWAPReversionFastBacktester as _VBT

    candles_cache: dict = _pickle.loads(candles_bytes)

    all_trades: list[dict] = []
    for tk, df in candles_cache.items():
        bt = _VBT(db=None, config=config, backtest_config=bt_config)
        result = _asyncio.run(bt.run_multi_day_cached(tk, df))
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)

    stats = compute_vwap_stats(all_trades)
    return {**params_dict, **{k: v for k, v in stats.items() if k != "exit_counts"}}


# ---------------------------------------------------------------------------
# 조합 빌더
# ---------------------------------------------------------------------------

def _build_combos() -> list[dict]:
    combos = []
    for dev in ENTRY_DEV_VALS:
        for sl in SL_PCT_VALS:
            for tp in TP_VALS:
                for ee in ENTRY_END_VALS:
                    tag = (
                        f"dev{dev*100:.1f}%_sl{sl*100:.1f}%"
                        f"_tp{tp*100:.1f}%_end{ee.replace(':', '')}"
                    )
                    combos.append({
                        "tag":                      tag,
                        "vwap_rev_entry_deviation": dev,
                        "vwap_rev_stop_loss_pct":   sl,
                        "vwap_rev_tp_above_vwap":   tp,
                        "vwap_rev_entry_end":        ee,
                    })
    return combos


def _vwap_config_factory(params: dict, base_config) -> object:
    return dataclasses.replace(
        base_config,
        vwap_rev_enabled=True,
        vwap_rev_entry_deviation=params.get("vwap_rev_entry_deviation", -0.015),
        vwap_rev_stop_loss_pct=params.get("vwap_rev_stop_loss_pct", 0.015),
        vwap_rev_tp_above_vwap=params.get("vwap_rev_tp_above_vwap", 0.003),
        vwap_rev_entry_end=params.get("vwap_rev_entry_end", "14:00"),
        vwap_rev_entry_start="09:30",
        vwap_rev_min_prev_volume=50000,
        vwap_rev_max_daily_drop=-0.07,
    )


# ---------------------------------------------------------------------------
# 병렬 그리드 실행
# ---------------------------------------------------------------------------

def _run_vwap_grid(combos: list[dict], cache: GridCache) -> list[dict]:
    cache.prepare_bytes()
    n_workers = max(2, min(4, (os.cpu_count() or 4) - 1))

    worker_args = [
        (
            _vwap_config_factory(p, cache.base_config),
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
    print(f"[VWAP GRID LARGECAP] {n}조합 × {len(cache.candles)}종목  workers={n_workers}", flush=True)

    try:
        from tqdm import tqdm as _tqdm
        _use_tqdm = True
    except ImportError:
        _use_tqdm = False

    ctx = mp.get_context("spawn")
    try:
        from concurrent.futures import ProcessPoolExecutor as _PPE
        with _PPE(max_workers=n_workers, mp_context=ctx) as ex:
            it = ex.map(_vwap_worker, worker_args)
            if _use_tqdm:
                it = _tqdm(it, total=n, desc="vwap largecap", unit="combo")
            for i, r in enumerate(it, 1):
                results.append(r)
                if not _use_tqdm:
                    elapsed = _time.time() - t0
                    eta = elapsed / i * (n - i) if i < n else 0
                    print(
                        f"  [{i:>3}/{n}] {r.get('tag','?'):<45} "
                        f"pf={r.get('pf', 0):.3f} "
                        f"tr={r.get('trades', 0):>3} "
                        f"(ETA {eta:.0f}s)",
                        flush=True,
                    )
    except Exception as exc:
        print(f"[WARN] Pool 실패 ({exc}), 순차 실행 전환", flush=True)
        results = []
        for i, wargs in enumerate(worker_args, 1):
            r = _vwap_worker(wargs)
            results.append(r)
            elapsed = _time.time() - t0
            if not _use_tqdm:
                print(
                    f"  [{i:>3}/{n}] {r.get('tag','?'):<45} "
                    f"pf={r.get('pf', 0):.3f} ({elapsed:.0f}s)",
                    flush=True,
                )

    elapsed = _time.time() - t0
    print(f"[DONE] {n}조합 완료 ({elapsed:.1f}s)", flush=True)
    return results


# ---------------------------------------------------------------------------
# 선정
# ---------------------------------------------------------------------------

def _select_best(old_results: list[dict], new_results: list[dict]) -> dict | None:
    new_pf_map = {r.get("tag"): r.get("pf", 0.0) for r in new_results}
    candidates = [
        r for r in old_results
        if (
            r.get("pf", 0.0) >= PF_THRESHOLD
            and int(r.get("trades", 0)) >= MIN_TRADES
            and int(r.get("max_consec_loss", 0)) <= MAX_CONSEC_LOSS
            and new_pf_map.get(r.get("tag"), 0.0) > NEW_PF_THRESHOLD
        )
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r.get("pnl", 0))


# ---------------------------------------------------------------------------
# 보고서
# ---------------------------------------------------------------------------

def _write_report(
    old_results: list[dict],
    new_results: list[dict],
    best: dict | None,
) -> None:
    out = Path("reports/vwap_largecap_grid_result.md")
    out.parent.mkdir(exist_ok=True)

    new_pf_map = {r.get("tag"): r.get("pf", 0.0) for r in new_results}

    param_cols = [
        "vwap_rev_entry_deviation", "vwap_rev_stop_loss_pct",
        "vwap_rev_tp_above_vwap", "vwap_rev_entry_end",
    ]
    stat_cols = [
        "pf", "pnl", "trades", "win_rate",
        "vwap_return_rate", "avg_profit", "avg_loss",
        "avg_hold_min", "max_consec_loss",
    ]
    all_cols = ["tag"] + param_cols + stat_cols

    def _md_rows(results: list[dict], mark_new: bool = False) -> list[str]:
        header = "| " + " | ".join(all_cols) + (" | NEW_PF |" if mark_new else " |")
        sep    = "| " + " | ".join("---" for _ in all_cols) + (" | --- |" if mark_new else " |")
        lines  = [header, sep]
        for r in sorted(results, key=lambda x: x.get("pf", 0.0), reverse=True):
            vals = []
            for c in all_cols:
                v = r.get(c, "-")
                if isinstance(v, float):
                    if c in ("pf", "vwap_return_rate", "win_rate"):
                        vals.append(f"{v:.4f}")
                    else:
                        vals.append(f"{v:.1f}")
                else:
                    vals.append(str(v))
            ok = ""
            if (
                r.get("pf", 0) >= PF_THRESHOLD
                and int(r.get("trades", 0)) >= MIN_TRADES
                and int(r.get("max_consec_loss", 0)) <= MAX_CONSEC_LOSS
            ):
                ok = " ✓"
            row = "| " + " | ".join(vals) + ok + " |"
            if mark_new:
                npf = new_pf_map.get(r.get("tag"), 0.0)
                row = row[:-1] + f" {npf:.4f} |"
            lines.append(row)
        return lines

    lines: list[str] = [
        "# VWAP 리버전 전략 그리드 서치 — KOSPI 대형주",
        f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> 유니버스: {UNIVERSE_PATH} (KOSPI 50종목)",
        f"> OLD 구간: {OLD_START} ~ {OLD_END} / NEW 구간: {NEW_START} ~ {NEW_END}",
        f"> 그리드: entry_deviation × stop_loss_pct × tp_above_vwap × entry_end "
        f"= {len(ENTRY_DEV_VALS)} × {len(SL_PCT_VALS)} × {len(TP_VALS)} × {len(ENTRY_END_VALS)} "
        f"= {len(old_results)}조합",
        f"> 선정 기준: PF≥{PF_THRESHOLD} AND 거래≥{MIN_TRADES}건 "
        f"AND maxCL≤{MAX_CONSEC_LOSS} AND NEW_PF>{NEW_PF_THRESHOLD}",
        "",
        f"## 그리드 결과 — OLD ({OLD_START}~{OLD_END})",
        "",
    ] + _md_rows(old_results, mark_new=bool(new_results)) + [""]

    if new_results:
        lines += [
            f"## 그리드 결과 — NEW ({NEW_START}~{NEW_END})",
            "",
        ] + _md_rows(new_results) + [""]

    lines += ["## 선정 결과", ""]

    if best is not None:
        npf = new_pf_map.get(best.get("tag", ""), 0.0)
        lines += [
            f"**최적 조합**: `{best.get('tag')}`",
            "",
            "| 파라미터 | 값 |",
            "| --- | --- |",
            f"| `entry_deviation` | {best.get('vwap_rev_entry_deviation', 0)*100:.1f}% |",
            f"| `stop_loss_pct` | {best.get('vwap_rev_stop_loss_pct', 0)*100:.1f}% |",
            f"| `tp_above_vwap` | {best.get('vwap_rev_tp_above_vwap', 0)*100:.1f}% |",
            f"| `entry_end` | {best.get('vwap_rev_entry_end', '14:00')} |",
            "",
            "| 지표 | OLD | NEW |",
            "| --- | --- | --- |",
            f"| PF | {best.get('pf', 0):.3f} | {npf:.3f} |",
            f"| PnL | {int(best.get('pnl', 0)):+,} | - |",
            f"| 거래수 | {best.get('trades', 0)} | - |",
            f"| 승률 | {best.get('win_rate', 0):.1%} | - |",
            f"| VWAP 복귀율 | {best.get('vwap_return_rate', 0):.1%} | - |",
            f"| 평균 보유(분) | {best.get('avg_hold_min', 0):.1f} | - |",
            f"| 최대 연속 손실 | {best.get('max_consec_loss', 0)} | - |",
            "",
            "> config.yaml 자동 갱신 없음 — 대형주 검증용 결과",
        ]
    else:
        lines += [
            "선정 기준 미달 — KOSPI 대형주 유니버스에서 VWAP 리버전 전략 유효 조합 없음",
            "",
            f"미달: PF≥{PF_THRESHOLD} AND 거래≥{MIN_TRADES} AND maxCL≤{MAX_CONSEC_LOSS} "
            f"AND NEW_PF>{NEW_PF_THRESHOLD} 충족 없음",
        ]

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[SAVED] {out}", flush=True)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="VWAP 리버전 72조합 그리드 (대형주)")
    parser.add_argument("--verify", action="store_true", help="기본 파라미터 단일 실행")
    args = parser.parse_args()

    print(f"캔들 캐시 로드 중... ({UNIVERSE_PATH})", flush=True)
    cache = await load_candle_cache(OLD_START, NEW_END, universe_path=UNIVERSE_PATH)
    print(f"  {len(cache.candles)}종목 로드 완료", flush=True)

    old_cache = cache.filter_dates(OLD_START, OLD_END)
    new_cache = cache.filter_dates(NEW_START, NEW_END)

    if args.verify:
        from backtest.backtester_fast import VWAPReversionFastBacktester as _VBT

        async def _run_verify(c_cache: GridCache, label: str) -> None:
            default_params = {
                "vwap_rev_entry_deviation": -0.015,
                "vwap_rev_stop_loss_pct":   0.015,
                "vwap_rev_tp_above_vwap":   0.003,
                "vwap_rev_entry_end":        "14:00",
            }
            config = _vwap_config_factory(default_params, c_cache.base_config)
            all_trades: list[dict] = []
            for tk, df in c_cache.candles.items():
                bt = _VBT(db=None, config=config, backtest_config=c_cache.bt_config)
                result = await bt.run_multi_day_cached(tk, df)
                for t in result.get("trades", []):
                    t["ticker"] = tk
                    all_trades.append(t)
            s = compute_vwap_stats(all_trades)
            print(
                f"[VERIFY {label}] PF={s['pf']:.3f}  "
                f"PnL={s['pnl']:+,}  거래={s['trades']}  "
                f"승률={s['win_rate']:.1%}  "
                f"VWAP복귀={s['vwap_return_rate']:.1%}  "
                f"avgHold={s['avg_hold_min']:.1f}분  "
                f"maxCL={s['max_consec_loss']}",
                flush=True,
            )

        print("\n[VERIFY] 기본 파라미터 단일 실행...", flush=True)
        await _run_verify(old_cache, "OLD")
        if new_cache.candles:
            await _run_verify(new_cache, "NEW")
        return

    combos = _build_combos()
    print(f"\n총 {len(combos)}조합", flush=True)

    print(f"\n[GRID OLD] {OLD_START} ~ {OLD_END}", flush=True)
    old_results = _run_vwap_grid(combos, old_cache)

    print(f"\n[GRID NEW] {NEW_START} ~ {NEW_END}", flush=True)
    if new_cache.candles:
        new_results = _run_vwap_grid(combos, new_cache)
    else:
        new_results = []
        print("  NEW 캔들 없음 - 생략", flush=True)

    best = _select_best(old_results, new_results)
    if best:
        npf = {r.get("tag"): r.get("pf", 0.0) for r in new_results}.get(best.get("tag", ""), 0.0)
        print(
            f"\n[선정] {best['tag']}"
            f" PF={best.get('pf', 0):.3f}"
            f" 거래={best.get('trades', 0)}"
            f" PnL={int(best.get('pnl', 0)):+,}"
            f" NEW_PF={npf:.3f}",
            flush=True,
        )
    else:
        print(
            f"\n[선정] 기준 미달 (PF≥{PF_THRESHOLD}/거래≥{MIN_TRADES}/maxCL≤{MAX_CONSEC_LOSS}/NEW_PF>{NEW_PF_THRESHOLD})",
            flush=True,
        )

    _write_report(old_results, new_results, best)


if __name__ == "__main__":
    asyncio.run(main())
