"""scripts/grid_vi_breakout.py — VI 돌파 전략 192조합 그리드 (백테스트 유니버스).

VI(변동성완화장치) 발동 후 해제 시 재돌파 전략.

분봉 기반 VI 추정:
- 정적 VI: high >= prev_close × (1 + 0.095) → 발동 추정
- VI 발동 분봉 open을 'VI 직전가'로 사용
- 다음 분봉 이후 close > vi_pre_price × (1 + vi_breakout_pct) → 진입

그리드 파라미터:
  vi_breakout_pct : [0.0, 0.5%, 1.0%, 2.0%]          (4)
  sl_pct          : [1.0%, 1.5%, 2.0%]                 (3)
  tp_mode         : ["fixed_2pct", "fixed_3pct",
                     "fixed_5pct", "trail_only"]        (4)
  entry_deadline  : ["11:00", "13:00"]                  (2)
  use_volume      : [True, False]                       (2)
  총 192조합

구간:
  OLD: 2025-04-01 ~ 2026-04-10
  NEW: 2026-04-11 ~ 2026-05-12

선정 기준 (OLD 기준):
  PF >= 1.5 / 거래 >= 20건 / maxCL <= 8 / NEW PF > 1.0

결과: reports/vi_breakout_grid_result.md

사용:
    python -u scripts/grid_vi_breakout.py
    python -u scripts/grid_vi_breakout.py --verify
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

UNIVERSE_PATH = "config/universe_backtest.yaml"

# ---------------------------------------------------------------------------
# 그리드 파라미터
# ---------------------------------------------------------------------------

VI_BREAKOUT_PCT_VALS = [0.0,   0.005, 0.01,  0.02]   # 0%, 0.5%, 1%, 2%
SL_PCT_VALS          = [0.01,  0.015, 0.02]           # 1%, 1.5%, 2%
TP_MODE_VALS         = ["fixed_2pct", "fixed_3pct", "fixed_5pct", "trail_only"]
DEADLINE_VALS        = ["11:00", "13:00"]
VOL_VALS             = [True, False]

# ---------------------------------------------------------------------------
# 선정 기준
# ---------------------------------------------------------------------------

PF_THRESHOLD     = 1.5
MIN_TRADES       = 20
MAX_CONSEC_LOSS  = 8
NEW_PF_THRESHOLD = 1.0

# VI 발동 추정 임계 (고정 — 그리드 변수 아님)
VI_STATIC_TRIGGER_PCT = 0.095


# ---------------------------------------------------------------------------
# 통계
# ---------------------------------------------------------------------------

def compute_vi_stats(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {
            "pf": 0.0, "pnl": 0, "trades": 0, "win_rate": 0.0,
            "avg_profit": 0.0, "avg_loss": 0.0, "avg_hold_min": 0.0,
            "max_consec_loss": 0, "fc_pct": 0.0, "sl_pct": 0.0,
            "tp_pct": 0.0, "trail_pct": 0.0, "exit_counts": {},
        }

    gp     = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl     = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    wins   = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] < 0]
    exits  = Counter(t.get("exit_reason", "?") for t in trades)

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
        "pf":              round(gp / gl, 4) if gl > 0 else float("inf"),
        "pnl":             int(sum(t["pnl"] for t in trades)),
        "trades":          n,
        "win_rate":        round(len(wins) / n, 4),
        "avg_profit":      round(sum(wins) / len(wins), 1) if wins else 0.0,
        "avg_loss":        round(sum(losses) / len(losses), 1) if losses else 0.0,
        "avg_hold_min":    round(sum(hold_mins) / len(hold_mins), 1) if hold_mins else 0.0,
        "max_consec_loss": max_cl,
        "fc_pct":          round(exits.get("forced_close", 0)  / n * 100, 2),
        "sl_pct":          round(exits.get("stop_loss", 0)     / n * 100, 2),
        "tp_pct":          round(exits.get("tp_exit", 0)       / n * 100, 2),
        "trail_pct":       round(exits.get("trailing_stop", 0) / n * 100, 2),
        "exit_counts":     dict(exits),
    }


# ---------------------------------------------------------------------------
# 워커 (ProcessPool spawn — top-level 필수)
# ---------------------------------------------------------------------------

def _vi_worker(args: tuple) -> dict:
    (config, candles_bytes, _mm_bytes, _tm, bt_config, params_dict) = args

    from loguru import logger as _l
    _l.remove()
    _l.add(sys.stderr, level="WARNING")

    import asyncio as _asyncio
    import pickle as _pickle
    from backtest.backtester_fast import VIBreakoutFastBacktester as _VIT

    candles_cache: dict = _pickle.loads(candles_bytes)

    all_trades: list[dict] = []
    for tk, df in candles_cache.items():
        bt = _VIT(db=None, config=config, backtest_config=bt_config)
        result = _asyncio.run(bt.run_multi_day_cached(tk, df))
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)

    stats = compute_vi_stats(all_trades)
    return {**params_dict, **{k: v for k, v in stats.items() if k != "exit_counts"}}


# ---------------------------------------------------------------------------
# 조합 빌더
# ---------------------------------------------------------------------------

def _build_combos() -> list[dict]:
    combos = []
    for bp in VI_BREAKOUT_PCT_VALS:
        for sl in SL_PCT_VALS:
            for tp_mode in TP_MODE_VALS:
                for dl in DEADLINE_VALS:
                    for vc in VOL_VALS:
                        if tp_mode == "trail_only":
                            tp_pct, use_trailing, trail_pct = 0.0, True, 0.015
                        elif tp_mode == "fixed_2pct":
                            tp_pct, use_trailing, trail_pct = 0.02, False, 0.015
                        elif tp_mode == "fixed_3pct":
                            tp_pct, use_trailing, trail_pct = 0.03, False, 0.015
                        else:  # fixed_5pct
                            tp_pct, use_trailing, trail_pct = 0.05, False, 0.015

                        bp_label = f"{bp * 100:.1f}pct"
                        sl_label = f"{sl * 100:.1f}pct"
                        tag = (
                            f"bp{bp_label}_sl{sl_label}"
                            f"_tp{tp_mode}"
                            f"_dl{dl.replace(':','')}"
                            f"_vc{'Y' if vc else 'N'}"
                        )
                        combos.append({
                            "tag":                    tag,
                            "vi_breakout_pct":        bp,
                            "vi_sl_pct":              sl,
                            "vi_tp_pct":              tp_pct,
                            "vi_use_trailing":        use_trailing,
                            "vi_trail_pct":           trail_pct,
                            "vi_entry_deadline":      dl,
                            "vi_use_volume":          vc,
                            "_tp_mode":               tp_mode,
                        })
    return combos


def _vi_config_factory(params: dict, base_config) -> object:
    return dataclasses.replace(
        base_config,
        vi_breakout_enabled=True,
        vi_static_trigger_pct=VI_STATIC_TRIGGER_PCT,
        vi_breakout_pct=params["vi_breakout_pct"],
        vi_sl_pct=params["vi_sl_pct"],
        vi_tp_pct=params["vi_tp_pct"],
        vi_use_trailing=params["vi_use_trailing"],
        vi_trail_pct=params["vi_trail_pct"],
        vi_entry_deadline=params["vi_entry_deadline"],
        vi_use_volume=params["vi_use_volume"],
        vi_volume_ratio=2.0,
        vi_min_prev_volume=50000,
    )


# ---------------------------------------------------------------------------
# 병렬 그리드 실행
# ---------------------------------------------------------------------------

def _run_vi_grid(combos: list[dict], cache: GridCache) -> list[dict]:
    cache.prepare_bytes()
    n_workers = max(2, min(4, (os.cpu_count() or 4) - 1))

    worker_args = [
        (
            _vi_config_factory(p, cache.base_config),
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
    print(
        f"[VI GRID] {n}조합 × {len(cache.candles)}종목  workers={n_workers}",
        flush=True,
    )

    try:
        from tqdm import tqdm as _tqdm
        _use_tqdm = True
    except ImportError:
        _use_tqdm = False

    ctx = mp.get_context("spawn")
    try:
        from concurrent.futures import ProcessPoolExecutor as _PPE
        with _PPE(max_workers=n_workers, mp_context=ctx) as ex:
            it = ex.map(_vi_worker, worker_args)
            if _use_tqdm:
                it = _tqdm(it, total=n, desc="vi breakout", unit="combo")
            for i, r in enumerate(it, 1):
                results.append(r)
                if not _use_tqdm:
                    elapsed = _time.time() - t0
                    eta = elapsed / i * (n - i) if i < n else 0
                    print(
                        f"  [{i:>3}/{n}] {r.get('tag','?'):<62} "
                        f"pf={r.get('pf', 0):.3f} "
                        f"tr={r.get('trades', 0):>4} "
                        f"pnl={r.get('pnl', 0):>+10,} "
                        f"(ETA {eta:.0f}s)",
                        flush=True,
                    )
    except Exception as exc:
        print(f"[WARN] Pool 실패 ({exc}), 순차 실행 전환", flush=True)
        results = []
        for i, wargs in enumerate(worker_args, 1):
            r = _vi_worker(wargs)
            results.append(r)
            elapsed = _time.time() - t0
            if not _use_tqdm:
                print(
                    f"  [{i:>3}/{n}] {r.get('tag','?'):<62} "
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
    out = Path("reports/vi_breakout_grid_result.md")
    out.parent.mkdir(exist_ok=True)

    new_pf_map = {r.get("tag"): r.get("pf", 0.0) for r in new_results}

    param_cols = [
        "vi_breakout_pct", "vi_sl_pct", "_tp_mode",
        "vi_entry_deadline", "vi_use_volume",
    ]
    stat_cols = [
        "pf", "pnl", "trades", "win_rate",
        "avg_profit", "avg_loss", "avg_hold_min",
        "fc_pct", "sl_pct", "tp_pct", "trail_pct", "max_consec_loss",
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
                    if c in ("pf", "win_rate"):
                        vals.append(f"{v:.4f}")
                    elif c in ("vi_breakout_pct", "vi_sl_pct"):
                        vals.append(f"{v * 100:.1f}%")
                    else:
                        vals.append(f"{v:.1f}")
                elif isinstance(v, bool):
                    vals.append("Y" if v else "N")
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

    n_combos = (
        len(VI_BREAKOUT_PCT_VALS) * len(SL_PCT_VALS)
        * len(TP_MODE_VALS) * len(DEADLINE_VALS) * len(VOL_VALS)
    )

    lines: list[str] = [
        "# VI 돌파 전략 그리드 서치",
        f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> 유니버스: {UNIVERSE_PATH} (백테스트 고정 41종목)",
        f"> OLD 구간: {OLD_START} ~ {OLD_END} / NEW 구간: {NEW_START} ~ {NEW_END}",
        f"> 그리드: vi_breakout_pct × sl_pct × tp_mode × deadline × use_volume "
        f"= {len(VI_BREAKOUT_PCT_VALS)} × {len(SL_PCT_VALS)} × {len(TP_MODE_VALS)} "
        f"× {len(DEADLINE_VALS)} × {len(VOL_VALS)} = {n_combos}조합",
        f"> 선정 기준: PF≥{PF_THRESHOLD} AND 거래≥{MIN_TRADES}건 "
        f"AND maxCL≤{MAX_CONSEC_LOSS} AND NEW_PF>{NEW_PF_THRESHOLD}",
        "",
        "## VI 추정 방식",
        f"- 정적 VI: high >= prev_close × (1 + {VI_STATIC_TRIGGER_PCT:.3f}) 시 발동 추정",
        "- VI 발동 분봉의 open을 'VI 직전가(vi_pre_price)'로 사용",
        "- 발동 분봉은 단일가 매매 중으로 간주, 다음 분봉부터 진입 탐색",
        f"- 진입: close > vi_pre_price × (1 + vi_breakout_pct)",
        "",
        "## tp_mode 정의",
        "- `fixed_2pct` : TP 진입가 +2%, 트레일링 없음",
        "- `fixed_3pct` : TP 진입가 +3%, 트레일링 없음",
        "- `fixed_5pct` : TP 진입가 +5%, 트레일링 없음",
        "- `trail_only` : TP 없음, 고점 대비 trail 1.5% 트레일링 스톱, 15:10 강제 청산",
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
            f"| `vi_breakout_pct` | {best.get('vi_breakout_pct', 0) * 100:.1f}% |",
            f"| `sl_pct` | {best.get('vi_sl_pct', 0) * 100:.1f}% |",
            f"| `tp_mode` | {best.get('_tp_mode', '-')} |",
            f"| `entry_deadline` | {best.get('vi_entry_deadline', '13:00')} |",
            f"| `use_volume` | {'Y' if best.get('vi_use_volume') else 'N'} |",
            "",
            "| 지표 | OLD | NEW |",
            "| --- | --- | --- |",
            f"| PF | {best.get('pf', 0):.3f} | {npf:.3f} |",
            f"| PnL | {int(best.get('pnl', 0)):+,} | - |",
            f"| 거래수 | {best.get('trades', 0)} | - |",
            f"| 승률 | {best.get('win_rate', 0):.1%} | - |",
            f"| 평균 보유(분) | {best.get('avg_hold_min', 0):.1f} | - |",
            f"| 최대 연속 손실 | {best.get('max_consec_loss', 0)} | - |",
            "",
            "> 선정 기준 통과 — config.yaml vi_breakout 섹션 검토 후 활성화 여부 결정",
        ]
    else:
        lines += [
            "선정 기준 미달 — 전 조합 선정 기준 미충족",
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
    parser = argparse.ArgumentParser(description="VI 돌파 192조합 그리드")
    parser.add_argument("--verify", action="store_true", help="기본 파라미터 단일 실행")
    args = parser.parse_args()

    print(f"캔들 캐시 로드 중... ({UNIVERSE_PATH})", flush=True)
    cache = await load_candle_cache(OLD_START, NEW_END, universe_path=UNIVERSE_PATH)
    print(f"  {len(cache.candles)}종목 로드 완료", flush=True)

    old_cache = cache.filter_dates(OLD_START, OLD_END)
    new_cache = cache.filter_dates(NEW_START, NEW_END)

    if args.verify:
        from backtest.backtester_fast import VIBreakoutFastBacktester as _VIT

        async def _run_verify(c_cache: GridCache, label: str) -> None:
            default_params = {
                "vi_breakout_pct":   0.005,
                "vi_sl_pct":         0.015,
                "vi_tp_pct":         0.03,
                "vi_use_trailing":   False,
                "vi_trail_pct":      0.015,
                "vi_entry_deadline": "13:00",
                "vi_use_volume":     True,
                "_tp_mode":          "fixed_3pct",
            }
            config = _vi_config_factory(default_params, c_cache.base_config)
            all_trades: list[dict] = []
            for tk, df in c_cache.candles.items():
                bt = _VIT(db=None, config=config, backtest_config=c_cache.bt_config)
                result = await bt.run_multi_day_cached(tk, df)
                for t in result.get("trades", []):
                    t["ticker"] = tk
                    all_trades.append(t)
            s = compute_vi_stats(all_trades)
            print(
                f"[VERIFY {label}] PF={s['pf']:.3f}  "
                f"PnL={s['pnl']:+,}  거래={s['trades']}  "
                f"승률={s['win_rate']:.1%}  "
                f"avgHold={s['avg_hold_min']:.1f}분  "
                f"maxCL={s['max_consec_loss']}  "
                f"FC={s['fc_pct']:.1f}%  SL={s['sl_pct']:.1f}%  TP={s['tp_pct']:.1f}%",
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
    old_results = _run_vi_grid(combos, old_cache)

    print(f"\n[GRID NEW] {NEW_START} ~ {NEW_END}", flush=True)
    if new_cache.candles:
        new_results = _run_vi_grid(combos, new_cache)
    else:
        new_results = []
        print("  NEW 캔들 없음 - 생략", flush=True)

    best = _select_best(old_results, new_results)
    if best:
        npf = {r.get("tag"): r.get("pf", 0.0) for r in new_results}.get(
            best.get("tag", ""), 0.0
        )
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
            f"\n[선정] 기준 미달 (PF≥{PF_THRESHOLD}/거래≥{MIN_TRADES}"
            f"/maxCL≤{MAX_CONSEC_LOSS}/NEW_PF>{NEW_PF_THRESHOLD})",
            flush=True,
        )

    _write_report(old_results, new_results, best)


if __name__ == "__main__":
    asyncio.run(main())
