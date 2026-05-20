"""scripts/grid_volatility_breakout.py — 변동성 돌파 전략 80조합 그리드 서치.

k_value          : [0.3, 0.4, 0.5, 0.6, 0.7]
entry_deadline   : ["11:00", "14:00"]
sl_mode          : ["open", "fixed"]   fixed=진입가 -2%
tp_mode          : ["none_trail", "fixed_3pct"]
                     none_trail  : TP 없음 + trail 2.0%
                     fixed_3pct  : TP +3% + trail 없음
use_volume_confirm: [true, false]

전체 조합: 5 × 2 × 2 × 2 × 2 = 80개
구간:
  OLD: 2025-04-01 ~ 2026-04-10
  NEW: 2026-04-11 ~ 2026-05-12

선정 기준 (OLD 구간 기준):
  1. PF >= 1.5
  2. 거래수 >= 30건
  3. 연속 손실(maxCL) <= 8
  4. NEW PF > 1.0 (OUT-OF-SAMPLE 검증)

결과: reports/volatility_breakout_grid_result.md

사용:
    python -u scripts/grid_volatility_breakout.py          # 전체 80조합
    python -u scripts/grid_volatility_breakout.py --verify # 기본값 단일 실행
    python -u scripts/grid_volatility_breakout.py --no-update  # config.yaml 갱신 건너뜀
"""
from __future__ import annotations

import asyncio
import dataclasses
import multiprocessing as mp
import os
import pickle
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
NEW_END   = "2026-05-12"

# ---------------------------------------------------------------------------
# 그리드 파라미터
# ---------------------------------------------------------------------------

K_VALS        = [0.3, 0.4, 0.5, 0.6, 0.7]
DEADLINE_VALS = ["11:00", "14:00"]
SL_MODE_VALS  = ["open", "fixed"]     # fixed → sl_pct=2%
TP_MODE_VALS  = ["none_trail", "fixed_3pct"]
VOL_CONF_VALS = [True, False]

# ---------------------------------------------------------------------------
# 선정 기준
# ---------------------------------------------------------------------------

PF_THRESHOLD     = 1.5
MIN_TRADES       = 30
MAX_CONSEC_LOSS  = 8
NEW_PF_THRESHOLD = 1.0


# ---------------------------------------------------------------------------
# 통계 계산
# ---------------------------------------------------------------------------

def compute_vb_stats(trades: list[dict]) -> dict:
    """변동성 돌파 전략 통계."""
    n = len(trades)
    if n == 0:
        return {
            "pf": 0.0, "pnl": 0, "trades": 0, "win_rate": 0.0,
            "avg_profit": 0.0, "avg_loss": 0.0, "avg_hold_min": 0.0,
            "max_consec_loss": 0, "fc_pct": 0.0, "sl_pct": 0.0,
            "tp_pct": 0.0, "trail_pct": 0.0, "exit_counts": {},
        }

    gp   = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl   = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    pnl  = sum(t["pnl"] for t in trades)
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] < 0]
    exits = Counter(t.get("exit_reason", "?") for t in trades)

    hold_mins = []
    for t in trades:
        ets, xts = t.get("entry_ts"), t.get("exit_ts")
        if ets and xts:
            hold_mins.append((xts - ets).total_seconds() / 60.0)
    avg_hold = sum(hold_mins) / len(hold_mins) if hold_mins else 0.0

    max_cl, cur_cl = 0, 0
    for t in sorted(trades, key=lambda x: x.get("entry_ts") or datetime.min):
        if t["pnl"] < 0:
            cur_cl += 1
            max_cl = max(max_cl, cur_cl)
        else:
            cur_cl = 0

    return {
        "pf":              round(gp / gl, 4) if gl > 0 else float("inf"),
        "pnl":             int(pnl),
        "trades":          n,
        "win_rate":        round(len(wins) / n, 4),
        "avg_profit":      round(sum(wins) / len(wins), 1) if wins else 0.0,
        "avg_loss":        round(sum(losses) / len(losses), 1) if losses else 0.0,
        "avg_hold_min":    round(avg_hold, 1),
        "max_consec_loss": max_cl,
        "fc_pct":          round(exits.get("forced_close", 0) / n * 100, 2),
        "sl_pct":          round(exits.get("stop_loss", 0) / n * 100, 2),
        "tp_pct":          round(exits.get("tp_exit", 0) / n * 100, 2),
        "trail_pct":       round(exits.get("trailing_stop", 0) / n * 100, 2),
        "exit_counts":     dict(exits),
    }


# ---------------------------------------------------------------------------
# 워커 (ProcessPool spawn — top-level 필수)
# ---------------------------------------------------------------------------

def _vb_worker(args: tuple) -> dict:
    """변동성 돌파 전략 단독 백테스트 워커."""
    (config, candles_bytes, _market_map_bytes,
     _ticker_to_market, bt_config, params_dict) = args

    from loguru import logger as _l
    _l.remove()
    _l.add(sys.stderr, level="WARNING")

    import asyncio as _asyncio
    import pickle as _pickle
    from backtest.backtester_fast import VolatilityBreakoutFastBacktester as _VBT

    candles_cache: dict = _pickle.loads(candles_bytes)

    all_trades: list[dict] = []
    for tk, df in candles_cache.items():
        bt = _VBT(db=None, config=config, backtest_config=bt_config)
        result = _asyncio.run(bt.run_multi_day_cached(tk, df))
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)

    stats = compute_vb_stats(all_trades)
    return {**params_dict, **{k: v for k, v in stats.items() if k != "exit_counts"}}


# ---------------------------------------------------------------------------
# 조합 빌더
# ---------------------------------------------------------------------------

def _build_combos() -> list[dict]:
    combos = []
    for k in K_VALS:
        for dl in DEADLINE_VALS:
            for sl in SL_MODE_VALS:
                for tp_mode in TP_MODE_VALS:
                    for vc in VOL_CONF_VALS:
                        # tp_mode → (vb_tp_pct, vb_use_trailing, vb_trail_pct)
                        if tp_mode == "none_trail":
                            tp_pct       = 0.0
                            use_trailing = True
                            trail_pct    = 0.02
                        else:  # fixed_3pct
                            tp_pct       = 0.03
                            use_trailing = False
                            trail_pct    = 0.02

                        tag = (
                            f"k{k:.1f}_dl{dl.replace(':','')}_sl{sl}"
                            f"_tp{tp_mode}_vc{'Y' if vc else 'N'}"
                        )
                        combos.append({
                            "tag":                   tag,
                            "vb_k_value":            k,
                            "vb_entry_deadline":     dl,
                            "vb_sl_mode":            sl,
                            "vb_sl_pct":             0.02,
                            "vb_tp_pct":             tp_pct,
                            "vb_use_trailing":       use_trailing,
                            "vb_trail_pct":          trail_pct,
                            "vb_use_volume_confirm": vc,
                            # 파라미터 문자열 저장 (보고서용)
                            "_tp_mode":              tp_mode,
                        })
    return combos


def _vb_config_factory(params: dict, base_config) -> object:
    return dataclasses.replace(
        base_config,
        vb_enabled=True,
        vb_k_value=params["vb_k_value"],
        vb_entry_deadline=params["vb_entry_deadline"],
        vb_sl_mode=params["vb_sl_mode"],
        vb_sl_pct=params["vb_sl_pct"],
        vb_tp_pct=params["vb_tp_pct"],
        vb_use_trailing=params["vb_use_trailing"],
        vb_trail_pct=params["vb_trail_pct"],
        vb_use_volume_confirm=params["vb_use_volume_confirm"],
        vb_min_range_pct=0.015,
        vb_max_range_pct=0.10,
        vb_min_prev_volume=50000,
    )


# ---------------------------------------------------------------------------
# 병렬 그리드 실행
# ---------------------------------------------------------------------------

def _run_vb_grid(
    combos: list[dict],
    cache: GridCache,
    *,
    max_workers: int | None = None,
) -> list[dict]:
    """변동성 돌파 그리드를 병렬로 실행."""
    cache.prepare_bytes()
    n_workers = max_workers or max(2, min(4, (os.cpu_count() or 4) - 1))

    worker_args = [
        (
            _vb_config_factory(p, cache.base_config),
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
    print(f"[VB GRID] {n}조합 × {len(cache.candles)}종목  workers={n_workers}", flush=True)

    try:
        from tqdm import tqdm
        _use_tqdm = True
    except ImportError:
        _use_tqdm = False

    ctx = mp.get_context("spawn")
    try:
        from concurrent.futures import ProcessPoolExecutor as _PPE
        with _PPE(max_workers=n_workers, mp_context=ctx) as ex:
            it = ex.map(_vb_worker, worker_args)
            if _use_tqdm:
                from tqdm import tqdm as _tqdm
                it = _tqdm(it, total=n, desc="vb grid", unit="combo")
            for i, r in enumerate(it, 1):
                results.append(r)
                if not _use_tqdm:
                    elapsed = _time.time() - t0
                    eta = elapsed / i * (n - i) if i < n else 0
                    print(
                        f"  [{i:>3}/{n}] {r.get('tag','?'):<55} "
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
            r = _vb_worker(wargs)
            results.append(r)
            elapsed = _time.time() - t0
            if not _use_tqdm:
                print(
                    f"  [{i:>3}/{n}] {r.get('tag','?'):<55} "
                    f"pf={r.get('pf', 0):.3f} ({elapsed:.0f}s)",
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
        f"{'태그':>55} | "
        f"{'PF':>6} {'PnL':>10} {'거래':>4} {'승률':>6} | "
        f"{'FC%':>5} {'SL%':>5} {'TP%':>5} {'TR%':>5} "
        f"{'보유(분)':>8} {'maxCL':>5} | {'OK':>3}"
    )
    sep = "-" * len(hdr)
    print(sep, flush=True)
    print(hdr, flush=True)
    print(sep, flush=True)
    for r in sorted(results, key=lambda x: x.get("pf", 0.0), reverse=True)[:20]:
        tag = r.get("tag", "?")
        pf  = r.get("pf", 0.0)
        pnl = int(r.get("pnl", 0))
        tr  = int(r.get("trades", 0))
        wr  = r.get("win_rate", 0.0)
        fc  = r.get("fc_pct", 0.0)
        sl  = r.get("sl_pct", 0.0)
        tp  = r.get("tp_pct", 0.0)
        trl = r.get("trail_pct", 0.0)
        ah  = r.get("avg_hold_min", 0.0)
        cl  = int(r.get("max_consec_loss", 0))
        ok  = "Y" if (
            pf >= PF_THRESHOLD and tr >= MIN_TRADES and cl <= MAX_CONSEC_LOSS
        ) else ""
        print(
            f"{tag:>55} | "
            f"{pf:>6.3f} {pnl:>+10,} {tr:>4} {wr:>6.1%} | "
            f"{fc:>5.1f} {sl:>5.1f} {tp:>5.1f} {trl:>5.1f} "
            f"{ah:>8.1f} {cl:>5} | {ok:>3}",
            flush=True,
        )
    print(sep, flush=True)


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
    out = Path("reports/volatility_breakout_grid_result.md")
    out.parent.mkdir(exist_ok=True)

    new_pf_map = {r.get("tag"): r.get("pf", 0.0) for r in new_results}

    param_cols = [
        "vb_k_value", "vb_entry_deadline", "vb_sl_mode",
        "_tp_mode", "vb_use_volume_confirm",
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

    lines: list[str] = [
        "# 변동성 돌파 전략 그리드 서치 (래리 윌리엄스)",
        f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> OLD 구간: {OLD_START} ~ {OLD_END} / NEW 구간: {NEW_START} ~ {NEW_END}",
        "> 전략: 변동성 돌파 (당일시가 + 전일레인지 × K)",
        f"> 그리드: k × deadline × sl_mode × tp_mode × vol_confirm "
        f"= {len(K_VALS)} × {len(DEADLINE_VALS)} × {len(SL_MODE_VALS)} "
        f"× {len(TP_MODE_VALS)} × {len(VOL_CONF_VALS)} = {len(old_results)}조합",
        f"> 선정 기준: PF≥{PF_THRESHOLD} AND 거래≥{MIN_TRADES}건 "
        f"AND maxCL≤{MAX_CONSEC_LOSS} AND NEW_PF>{NEW_PF_THRESHOLD}",
        "",
        "## tp_mode 정의",
        "- `none_trail`: TP 없음 + 고점 대비 trail 2.0% 트레일링 스톱, 15:10 강제 청산",
        "- `fixed_3pct`: TP +3% 도달 시 청산, trail 없음, 15:10 강제 청산",
        "",
        "## sl_mode 정의",
        "- `open`: 당일 시가 하회 시 손절",
        "- `fixed`: 진입가 대비 -2% 손절",
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
            f"| `k_value` | {best.get('vb_k_value', 0.5)} |",
            f"| `entry_deadline` | {best.get('vb_entry_deadline', '14:00')} |",
            f"| `sl_mode` | {best.get('vb_sl_mode', 'open')} |",
            f"| `tp_mode` | {best.get('_tp_mode', '-')} |",
            f"| `use_volume_confirm` | {'Y' if best.get('vb_use_volume_confirm') else 'N'} |",
            "",
            "| 지표 | OLD | NEW |",
            "| --- | --- | --- |",
            f"| PF | {best.get('pf', 0):.3f} | {npf:.3f} |",
            f"| PnL | {int(best.get('pnl', 0)):+,} | - |",
            f"| 거래수 | {best.get('trades', 0)} | - |",
            f"| 승률 | {best.get('win_rate', 0):.1%} | - |",
            f"| 평균 보유(분) | {best.get('avg_hold_min', 0):.1f} | - |",
            f"| 최대 연속 손실 | {best.get('max_consec_loss', 0)} | - |",
        ]
    else:
        lines += [
            "선정 기준 미달 -- 변동성 돌파 전략 비활성 유지 (`volatility_breakout.enabled: false`)",
            "",
            f"미달 이유: PF>={PF_THRESHOLD} AND 거래>={MIN_TRADES} AND maxCL<={MAX_CONSEC_LOSS} "
            f"AND NEW_PF>{NEW_PF_THRESHOLD} 조건 충족 조합 없음",
        ]

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[SAVED] {out}", flush=True)


# ---------------------------------------------------------------------------
# config.yaml 갱신
# ---------------------------------------------------------------------------

def _update_config_yaml(best: dict) -> None:
    """volatility_breakout 섹션 파라미터와 enabled: true로 갱신."""
    import re
    cfg_path = Path("config.yaml")
    text = cfg_path.read_text(encoding="utf-8")

    k   = best.get("vb_k_value", 0.5)
    dl  = best.get("vb_entry_deadline", "14:00")
    sl  = best.get("vb_sl_mode", "open")
    tpm = best.get("_tp_mode", "none_trail")
    vc  = best.get("vb_use_volume_confirm", True)

    # tp_mode → tp_pct, use_trailing
    if tpm == "none_trail":
        tp_pct_val   = 0.0
        use_trail    = True
        trail_pct_val = 0.02
    else:
        tp_pct_val   = 0.03
        use_trail    = False
        trail_pct_val = 0.02

    text = re.sub(
        r"(  volatility_breakout:\s*\n(?:.*\n)*?    enabled:\s*)false",
        r"\g<1>true",
        text,
    )
    text = re.sub(r"(    k_value:\s*)\S+",      f"\\g<1>{k}", text)
    text = re.sub(r'(    entry_deadline:\s*)["\']?[\w:]+["\']?', f'\\g<1>"{dl}"', text)
    text = re.sub(r'(    sl_mode:\s*)["\']?\w+["\']?', f'\\g<1>"{sl}"', text)
    text = re.sub(r"(    tp_pct:\s*)\S+",       f"\\g<1>{tp_pct_val}", text)
    text = re.sub(r"(    use_trailing:\s*)\S+",  f"\\g<1>{'true' if use_trail else 'false'}", text)
    text = re.sub(r"(    trail_pct:\s*)\S+",     f"\\g<1>{trail_pct_val}", text)
    text = re.sub(
        r"(    use_volume_confirm:\s*)\S+",
        f"\\g<1>{'true' if vc else 'false'}",
        text,
    )

    cfg_path.write_text(text, encoding="utf-8")
    print(
        f"[CONFIG] config.yaml 갱신: enabled=true "
        f"k={k} dl={dl} sl={sl} tp_mode={tpm} vc={vc}",
        flush=True,
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="변동성 돌파 80조합 그리드")
    parser.add_argument("--verify",    action="store_true", help="기본 파라미터 단일 실행 검증")
    parser.add_argument("--no-update", action="store_true", help="config.yaml 갱신 건너뜀")
    args = parser.parse_args()

    print("캔들 캐시 로드 중...", flush=True)
    cache = await load_candle_cache(OLD_START, NEW_END)
    print(f"  {len(cache.candles)}종목 로드 완료", flush=True)

    old_cache = cache.filter_dates(OLD_START, OLD_END)
    new_cache = cache.filter_dates(NEW_START, NEW_END)

    if args.verify:
        from backtest.backtester_fast import VolatilityBreakoutFastBacktester as _VBT

        async def _run_verify(c_cache: GridCache, label: str) -> None:
            default_params = {
                "vb_k_value":            0.5,
                "vb_entry_deadline":     "14:00",
                "vb_sl_mode":            "open",
                "vb_sl_pct":             0.02,
                "vb_tp_pct":             0.03,
                "vb_use_trailing":       True,
                "vb_trail_pct":          0.02,
                "vb_use_volume_confirm": True,
                "_tp_mode":              "fixed_3pct",
            }
            config = _vb_config_factory(default_params, c_cache.base_config)
            all_trades: list[dict] = []
            for tk, df in c_cache.candles.items():
                bt = _VBT(db=None, config=config, backtest_config=c_cache.bt_config)
                result = await bt.run_multi_day_cached(tk, df)
                for t in result.get("trades", []):
                    t["ticker"] = tk
                    all_trades.append(t)
            s = compute_vb_stats(all_trades)
            print(
                f"[VERIFY {label}] PF={s['pf']:.3f}  "
                f"PnL={s['pnl']:+,}  거래={s['trades']}  "
                f"승률={s['win_rate']:.1%}  "
                f"avgHold={s['avg_hold_min']:.1f}분  "
                f"maxCL={s['max_consec_loss']}  "
                f"FC={s['fc_pct']:.1f}%  SL={s['sl_pct']:.1f}%  "
                f"TP={s['tp_pct']:.1f}%  Trail={s['trail_pct']:.1f}%",
                flush=True,
            )

        print("\n[VERIFY] 기본 파라미터 단일 실행...", flush=True)
        await _run_verify(old_cache, "OLD")
        if new_cache.candles:
            await _run_verify(new_cache, "NEW")
        print("\n[VERIFY 완료]", flush=True)
        return

    combos = _build_combos()
    print(
        f"\n총 {len(combos)}조합 "
        f"(k × deadline × sl_mode × tp_mode × vol_confirm = "
        f"{len(K_VALS)} × {len(DEADLINE_VALS)} × {len(SL_MODE_VALS)} "
        f"× {len(TP_MODE_VALS)} × {len(VOL_CONF_VALS)})",
        flush=True,
    )

    # ── OLD 구간 ──────────────────────────────────────────────────────────
    print(f"\n[GRID OLD] {OLD_START} ~ {OLD_END}", flush=True)
    old_results = _run_vb_grid(combos, old_cache)
    _print_table(
        old_results,
        f"변동성 돌파 그리드 (OLD, {len(old_results)}조합, "
        f"선정기준 PF≥{PF_THRESHOLD}/거래≥{MIN_TRADES}/maxCL≤{MAX_CONSEC_LOSS})"
    )

    # ── NEW 구간 ──────────────────────────────────────────────────────────
    print(f"\n[GRID NEW] {NEW_START} ~ {NEW_END}", flush=True)
    if new_cache.candles:
        new_results = _run_vb_grid(combos, new_cache)
        _print_table(
            new_results,
            f"변동성 돌파 그리드 (NEW, {len(new_results)}조합)"
        )
    else:
        new_results = []
        print("  NEW 캔들 없음 - 생략", flush=True)

    # ── 선정 ──────────────────────────────────────────────────────────────
    best = _select_best(old_results, new_results)
    if best:
        npf = {r.get("tag"): r.get("pf", 0.0) for r in new_results}.get(best.get("tag", ""), 0.0)
        print(
            f"\n[선정] {best['tag']}"
            f" — PF={best.get('pf', 0):.3f}"
            f" 거래={best.get('trades', 0)}"
            f" PnL={int(best.get('pnl', 0)):+,}"
            f" avgHold={best.get('avg_hold_min', 0):.1f}분"
            f" maxCL={best.get('max_consec_loss', 0)}"
            f" NEW_PF={npf:.3f}",
            flush=True,
        )
        if not args.no_update:
            _update_config_yaml(best)
    else:
        print(
            f"\n[선정] 기준 미달 "
            f"(PF>={PF_THRESHOLD} / 거래>={MIN_TRADES} / "
            f"maxCL<={MAX_CONSEC_LOSS} / NEW_PF>{NEW_PF_THRESHOLD}) -- "
            "변동성 돌파 비활성 유지",
            flush=True,
        )

    _write_report(old_results, new_results, best)


if __name__ == "__main__":
    asyncio.run(main())
