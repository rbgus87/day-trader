"""scripts/grid_gap_score.py -- 갭업 기준가 조정 + 시그널 스코어링 2단계 그리드.

Stage 1: gap_breakout_adjust_enabled × gap_threshold_pct (2×3 = 6조합 + baseline)
Stage 2: signal_min_score (4개 임계값, Stage 1 최적 위에서)

선정 기준: PF >= 4.5  (baseline ~4.798 의 94%)
각 구간: OLD (2025-04-01 ~ 2026-04-10) + NEW (2026-04-11 ~ 2026-05-12)

사용:
    python -u scripts/grid_gap_score.py           -- 전체 2단계
    python -u scripts/grid_gap_score.py --stage 1 -- Stage 1만
    python -u scripts/grid_gap_score.py --stage 2 -- Stage 2 (Stage 1 최적 적용)
    python -u scripts/grid_gap_score.py --verify  -- baseline PF 재현
"""
from __future__ import annotations

import asyncio
import dataclasses
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")

from utils.grid_runner import GridCache, compute_stats, load_candle_cache, run_parallel_grid

OLD_START = "2025-04-01"
OLD_END   = "2026-04-10"
NEW_START = "2026-04-11"
NEW_END   = "2026-05-12"

# Stage 1: 갭업 기준가 조정
GAP_THRESHOLD_VALS  = [0.02, 0.03, 0.05]

# Stage 2: 시그널 스코어링 최소 점수
SCORE_MIN_VALS      = [40.0, 50.0, 60.0, 70.0]

PF_THRESHOLD        = 4.5
INITIAL_CAPITAL     = 5_000_000


# ---------------------------------------------------------------------------
# 워커 (top-level: ProcessPool pickle 필수)
# ---------------------------------------------------------------------------

def _gap_score_worker(args: tuple) -> dict:
    """갭업+스코어 그리드 워커."""
    config, candles_bytes, market_map_bytes, ticker_to_market, bt_config, params_dict = args

    from loguru import logger as _l
    _l.remove()
    _l.add(sys.stderr, level="WARNING")

    import asyncio as _a
    import pickle
    from backtest.backtester import Backtester as _BT
    from strategy.momentum_strategy import MomentumStrategy as _MS
    from utils.grid_runner import compute_stats as _cs

    candles_cache: dict = pickle.loads(candles_bytes)
    market_map: dict = pickle.loads(market_map_bytes)

    all_trades: list[dict] = []
    for tk, df in candles_cache.items():
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

    stats = _cs(all_trades)
    return {**params_dict, **stats}


# ---------------------------------------------------------------------------
# 조합 빌더
# ---------------------------------------------------------------------------

def _build_stage1_combos() -> list[dict]:
    combos = [{"tag": "BASE", "gap_breakout_adjust_enabled": False, "gap_threshold_pct": 0.0}]
    combos.append({"tag": "GAP-OFF", "gap_breakout_adjust_enabled": False, "gap_threshold_pct": 0.0})
    for thr in GAP_THRESHOLD_VALS:
        combos.append({
            "tag": f"GAP-{thr:.0%}",
            "gap_breakout_adjust_enabled": True,
            "gap_threshold_pct": thr,
        })
    return combos


def _build_stage2_combos() -> list[dict]:
    combos = [{"tag": "BASE", "signal_scoring_enabled": False, "signal_min_score": 0.0}]
    combos.append({"tag": "SCORE-OFF", "signal_scoring_enabled": False, "signal_min_score": 0.0})
    for mn in SCORE_MIN_VALS:
        combos.append({
            "tag": f"SC-{mn:.0f}",
            "signal_scoring_enabled": True,
            "signal_min_score": mn,
        })
    return combos


# ---------------------------------------------------------------------------
# config_factory
# ---------------------------------------------------------------------------

def _s1_factory(params: dict, base_config) -> object:
    if params.get("tag") == "BASE":
        return base_config
    return dataclasses.replace(
        base_config,
        gap_breakout_adjust_enabled=params.get("gap_breakout_adjust_enabled", False),
        gap_threshold_pct=params.get("gap_threshold_pct", 0.03),
    )


def _make_s2_factory(best_s1_config):
    def _factory(params: dict, base_config) -> object:
        if params.get("tag") == "BASE":
            return best_s1_config
        return dataclasses.replace(
            best_s1_config,
            signal_scoring_enabled=params.get("signal_scoring_enabled", False),
            signal_min_score=params.get("signal_min_score", 60.0),
        )
    return _factory


# ---------------------------------------------------------------------------
# 결과 출력 (DataFrame 행 기반)
# ---------------------------------------------------------------------------

def _print_table(df: pd.DataFrame, title: str) -> None:
    print(f"\n{title}")
    hdr = f"{'tag':>10} {'gap':>5} {'thr':>6} | {'trades':>7} {'PF':>6} {'PnL':>11} {'win%':>6}"
    sep = "=" * len(hdr)
    print(sep)
    print(hdr)
    print("-" * len(hdr))
    for _, r in df.iterrows():
        tag = str(r.get("tag", ""))
        gap = "Y" if r.get("gap_breakout_adjust_enabled") else "N"
        thr_val = r.get("gap_threshold_pct")
        thr = f"{thr_val:.0%}" if r.get("gap_breakout_adjust_enabled") and thr_val else "-"
        if "signal_min_score" in r.index:
            gap = "Y" if r.get("signal_scoring_enabled") else "N"
            sc_val = r.get("signal_min_score", 0)
            thr = f"{sc_val:.0f}" if r.get("signal_scoring_enabled") else "-"
        pf = r.get("pf", 0)
        pf_mark = "*" if pf >= PF_THRESHOLD else ""
        print(
            f"{tag:>10} {gap:>5} {thr:>6} | "
            f"{int(r.get('trades', 0)):>7} {pf:>5.3f}{pf_mark} "
            f"{int(r.get('pnl', 0)):>+11,} {r.get('win_rate', 0):>6.1%}"
        )
    print(sep)


def _select_best(df: pd.DataFrame, pf_thr: float) -> pd.Series | None:
    """PF >= thr 조합 중 PnL 최대. BASE/SCORE-OFF/GAP-OFF 제외."""
    mask = ~df["tag"].isin(["BASE", "GAP-OFF", "SCORE-OFF"]) & (df["pf"] >= pf_thr)
    candidates = df[mask]
    if candidates.empty:
        return None
    return candidates.loc[candidates["pnl"].idxmax()]


# ---------------------------------------------------------------------------
# 보고서
# ---------------------------------------------------------------------------

def _write_report(
    s1_old: pd.DataFrame, s1_new: pd.DataFrame,
    s2_old: pd.DataFrame, s2_new: pd.DataFrame,
    best_s1: pd.Series | None, best_s2: pd.Series | None,
) -> None:
    out = Path("reports/gap_score_grid.md")
    out.parent.mkdir(exist_ok=True)

    def _md_rows(df: pd.DataFrame, cols: list[str]) -> list[str]:
        header = "| " + " | ".join(cols) + " |"
        sep    = "| " + " | ".join("---" for _ in cols) + " |"
        lines  = [header, sep]
        for _, r in df.iterrows():
            vals = []
            for c in cols:
                v = r.get(c, "-")
                if isinstance(v, float):
                    vals.append(f"{v:.3f}")
                elif isinstance(v, bool):
                    vals.append("Y" if v else "N")
                else:
                    vals.append(str(v))
            lines.append("| " + " | ".join(vals) + " |")
        return lines

    s1_cols = ["tag", "gap_breakout_adjust_enabled", "gap_threshold_pct", "trades", "pf", "pnl", "win_rate"]
    s2_cols = ["tag", "signal_scoring_enabled", "signal_min_score", "trades", "pf", "pnl", "win_rate"]

    lines = [
        "# 갭업 기준가 조정 + 시그널 스코어링 그리드",
        f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> OLD 구간: {OLD_START} ~ {OLD_END}  NEW 구간: {NEW_START} ~ {NEW_END}",
        f"> PF 선정 기준: {PF_THRESHOLD}",
        "",
    ]

    if not s1_old.empty:
        lines += [f"## Stage 1 -갭업 기준가 조정 (OLD, {len(s1_old)}조합)", ""]
        lines += _md_rows(s1_old, s1_cols) + [""]
    if not s1_new.empty:
        lines += [f"## Stage 1 -갭업 기준가 조정 (NEW, {len(s1_new)}조합)", ""]
        lines += _md_rows(s1_new, s1_cols) + [""]
    if not s2_old.empty:
        lines += [f"## Stage 2 -시그널 스코어링 (OLD, {len(s2_old)}조합)", ""]
        lines += _md_rows(s2_old, s2_cols) + [""]
    if not s2_new.empty:
        lines += [f"## Stage 2 -시그널 스코어링 (NEW, {len(s2_new)}조합)", ""]
        lines += _md_rows(s2_new, s2_cols) + [""]

    lines += ["## 최종 선정", ""]
    if best_s2 is not None and best_s2["pf"] >= PF_THRESHOLD:
        lines += [
            f"- Stage 2 최적: {best_s2.get('tag','-')} PF={best_s2['pf']:.3f} PnL={int(best_s2['pnl']):+,}",
            f"  - signal_scoring_enabled={best_s2.get('signal_scoring_enabled')} "
            f"min_score={best_s2.get('signal_min_score','-')}",
        ]
    elif best_s1 is not None and best_s1["pf"] >= PF_THRESHOLD:
        lines += [
            f"- Stage 1 최적: {best_s1.get('tag','-')} PF={best_s1['pf']:.3f} PnL={int(best_s1['pnl']):+,}",
            f"  - gap_breakout_adjust_enabled={best_s1.get('gap_breakout_adjust_enabled')} "
            f"gap_threshold={best_s1.get('gap_threshold_pct','-')}",
        ]
    else:
        lines += ["선정 기준 미달 -현재 파라미터 유지 (두 기능 비활성)"]

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[SAVED] {out}", flush=True)


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

async def run_verify(cache: GridCache) -> None:
    from backtest.backtester import Backtester
    from strategy.momentum_strategy import MomentumStrategy

    print("[VERIFY] baseline (gap=off, score=off) PF 재현", flush=True)
    old_cache = cache.filter_dates(OLD_START, OLD_END)
    all_trades: list[dict] = []
    for tk, df in old_cache.candles.items():
        market = old_cache.ticker_to_market.get(tk, "unknown")
        bt = Backtester(
            db=None, config=cache.base_config, backtest_config=old_cache.bt_config,
            ticker_market=market, market_strong_by_date=old_cache.market_map,
        )
        strat = MomentumStrategy(cache.base_config)
        result = await bt.run_multi_day_cached(tk, df, strat)
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)

    stats = compute_stats(all_trades)
    print(
        f"  PF={stats['pf']:.3f}  trades={stats['trades']}  PnL={stats['pnl']:+,}  "
        f"win={stats['win_rate']:.1%}",
        flush=True,
    )


# ---------------------------------------------------------------------------
# run_stage (sync)
# ---------------------------------------------------------------------------

def run_stage(
    cache: GridCache,
    combos: list[dict],
    config_factory,
    title: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """OLD + NEW 두 구간을 순서대로 실행하고 DataFrame 반환."""
    old_cache = cache.filter_dates(OLD_START, OLD_END)
    print(f"\n[{title}] OLD 구간 ({len(combos)}조합, {len(old_cache.candles)}종목)...", flush=True)
    old_df = run_parallel_grid(combos, config_factory, old_cache, worker_fn=_gap_score_worker)
    _print_table(old_df, f"{title} (OLD)")

    new_cache = cache.filter_dates(NEW_START, NEW_END)
    if new_cache.candles:
        print(f"\n[{title}] NEW 구간 ({len(combos)}조합, {len(new_cache.candles)}종목)...", flush=True)
        new_df = run_parallel_grid(combos, config_factory, new_cache, worker_fn=_gap_score_worker)
        _print_table(new_df, f"{title} (NEW)")
    else:
        new_df = pd.DataFrame()
        print(f"[{title}] NEW 구간 캔들 없음 -생략", flush=True)

    return old_df, new_df


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--stage", type=int, choices=[1, 2], default=0)
    args = parser.parse_args()

    print("캔들 캐시 로드 중...", flush=True)
    cache = await load_candle_cache(OLD_START, NEW_END)
    print(f"  {len(cache.candles)}종목 로드 완료", flush=True)

    if args.verify:
        await run_verify(cache)
        return

    s1_old = s1_new = s2_old = s2_new = pd.DataFrame()
    best_s1: pd.Series | None = None
    best_s2: pd.Series | None = None

    if args.stage in (0, 1):
        s1_old, s1_new = run_stage(cache, _build_stage1_combos(), _s1_factory, "Stage 1 갭업 조정")
        best_s1 = _select_best(s1_old, PF_THRESHOLD)
        if best_s1 is not None:
            print(
                f"\n[Stage 1 최적] {best_s1.get('tag')} -"
                f"gap={best_s1.get('gap_breakout_adjust_enabled')} "
                f"thr={best_s1.get('gap_threshold_pct','N/A')} "
                f"PF={best_s1['pf']:.3f} PnL={int(best_s1['pnl']):+,}",
                flush=True,
            )
        else:
            print("\n[Stage 1] PF 선정 기준 미달 -baseline(gap=off) 유지", flush=True)
            base_rows = s1_old[s1_old["tag"] == "BASE"]
            best_s1 = base_rows.iloc[0] if not base_rows.empty else None

    if args.stage in (0, 2):
        # Stage 1 최적 config 구성
        if best_s1 is not None and best_s1.get("tag") != "BASE":
            best_s1_cfg = dataclasses.replace(
                cache.base_config,
                gap_breakout_adjust_enabled=bool(best_s1.get("gap_breakout_adjust_enabled", False)),
                gap_threshold_pct=float(best_s1.get("gap_threshold_pct", 0.03)),
            )
        else:
            best_s1_cfg = cache.base_config

        s2_old, s2_new = run_stage(
            cache, _build_stage2_combos(), _make_s2_factory(best_s1_cfg), "Stage 2 스코어링",
        )
        best_s2 = _select_best(s2_old, PF_THRESHOLD)
        if best_s2 is not None:
            print(
                f"\n[Stage 2 최적] {best_s2.get('tag')} -"
                f"score_enabled={best_s2.get('signal_scoring_enabled')} "
                f"min_score={best_s2.get('signal_min_score','N/A')} "
                f"PF={best_s2['pf']:.3f} PnL={int(best_s2['pnl']):+,}",
                flush=True,
            )
        else:
            print("\n[Stage 2] PF 선정 기준 미달 -스코어링 비활성 유지", flush=True)

    _write_report(s1_old, s1_new, s2_old, s2_new, best_s1, best_s2)


if __name__ == "__main__":
    asyncio.run(main())
