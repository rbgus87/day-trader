"""scripts/grid_atr_stop.py -- ATR 비례 손절 + trail 범위 + breakeven 3단계 그리드.

Stage 1: atr_stop_multiplier × atr_stop_min_pct × atr_stop_max_pct (36조합 + baseline)
Stage 2: atr_trail_min_pct × atr_trail_max_pct (9조합, Stage 1 최적 위에서)
Stage 3: breakeven_trigger_pct × breakeven_offset_pct (9조합, Stage 2 최적 위에서)

선정 기준: PF >= 4.5 (baseline 4.817의 93%)
각 조합: PF, PnL, 거래수, 승률, MDD%, stop_loss 건수
기존 구간(~04-10) 기준.

사용:
    python -u scripts/grid_atr_stop.py            -- 전체 3단계
    python -u scripts/grid_atr_stop.py --verify   -- baseline PF 4.817 재현
    python -u scripts/grid_atr_stop.py --stage 1  -- Stage 1만
    python -u scripts/grid_atr_stop.py --stage 2 --s1-mult 1.5 --s1-min 0.05 --s1-max 0.20
    python -u scripts/grid_atr_stop.py --stage 3 \\
        --s1-mult 1.5 --s1-min 0.05 --s1-max 0.20 \\
        --s2-trail-min 0.02 --s2-trail-max 0.10
"""
from __future__ import annotations

import asyncio
import dataclasses
import sys
from datetime import datetime
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")

from utils.grid_runner import GridCache, compute_stats, load_candle_cache, run_parallel_grid

OLD_START = "2025-04-01"
OLD_END   = "2026-04-10"

# Stage 1: ATR 손절
MULTIPLIER_VALS  = [1.0, 1.5, 2.0, 2.5]
STOP_MIN_VALS    = [0.04, 0.05, 0.06]   # 양수 (4%, 5%, 6%)
STOP_MAX_VALS    = [0.15, 0.20, 0.25]   # 양수 (15%, 20%, 25%)

# Stage 2: trail 범위
TRAIL_MIN_VALS   = [0.015, 0.02, 0.025]
TRAIL_MAX_VALS   = [0.08, 0.10, 0.12]

# Stage 3: breakeven
BE_TRIGGER_VALS  = [0.02, 0.03, 0.04]
BE_OFFSET_VALS   = [0.005, 0.01, 0.015]

PF_THRESHOLD     = 4.5   # 선정 기준
INITIAL_CAPITAL  = 5_000_000


# ---------------------------------------------------------------------------
# 공용 헬퍼
# ---------------------------------------------------------------------------

def _compute_mdd(trades: list[dict], initial_capital: float) -> float:
    """exit_ts 순 누적 자본 시뮬 기반 MDD."""
    if not trades:
        return 0.0
    sorted_trades = sorted(trades, key=lambda t: str(t.get("exit_ts", "") or ""))
    capital = initial_capital
    peak = capital
    max_dd = 0.0
    for t in sorted_trades:
        capital += t.get("pnl", 0.0)
        if capital > peak:
            peak = capital
        dd = (peak - capital) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


# ---------------------------------------------------------------------------
# 워커 (top-level: ProcessPool pickle 필수)
# ---------------------------------------------------------------------------

def _atr_stop_worker(args: tuple) -> dict:
    """ATR 손절 그리드 워커: stop_loss 건수 + MDD 포함."""
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
    sl_n = stats["exit_counts"].get("stop_loss", 0)
    mdd = _compute_mdd(all_trades, INITIAL_CAPITAL)

    return {
        **params_dict,
        **stats,
        "stop_loss_n": sl_n,
        "mdd_pct": round(mdd * 100, 2),
    }


# ---------------------------------------------------------------------------
# 조합 빌더
# ---------------------------------------------------------------------------

def _build_stage1_combos() -> list[dict]:
    combos = [{"tag": "BASE"}]
    for mult, mn, mx in product(MULTIPLIER_VALS, STOP_MIN_VALS, STOP_MAX_VALS):
        if mn >= mx:
            continue
        combos.append({
            "tag": "ATR",
            "atr_stop_multiplier": mult,
            "atr_stop_min_pct": mn,
            "atr_stop_max_pct": mx,
        })
    return combos


def _build_stage2_combos() -> list[dict]:
    combos = [{"tag": "BASE"}]
    for mn, mx in product(TRAIL_MIN_VALS, TRAIL_MAX_VALS):
        if mn >= mx:
            continue
        combos.append({
            "tag": "TRAIL",
            "atr_trail_min_pct": mn,
            "atr_trail_max_pct": mx,
        })
    return combos


def _build_stage3_combos() -> list[dict]:
    combos = [{"tag": "BASE"}]
    for trig, off in product(BE_TRIGGER_VALS, BE_OFFSET_VALS):
        combos.append({
            "tag": "BE",
            "breakeven_trigger_pct": trig,
            "breakeven_offset_pct": off,
        })
    return combos


# ---------------------------------------------------------------------------
# config_factory (단계별)
# ---------------------------------------------------------------------------

def _s1_factory(params: dict, base_config) -> object:
    if params.get("tag") == "BASE":
        return dataclasses.replace(base_config, atr_stop_enabled=False)
    return dataclasses.replace(
        base_config,
        atr_stop_enabled=True,
        atr_stop_multiplier=params["atr_stop_multiplier"],
        atr_stop_min_pct=params["atr_stop_min_pct"],
        atr_stop_max_pct=params["atr_stop_max_pct"],
    )


def _make_s2_factory(best_s1_config):
    def _factory(params: dict, base_config) -> object:
        if params.get("tag") == "BASE":
            return best_s1_config
        return dataclasses.replace(
            best_s1_config,
            atr_trail_min_pct=params["atr_trail_min_pct"],
            atr_trail_max_pct=params["atr_trail_max_pct"],
        )
    return _factory


def _make_s3_factory(best_s2_config):
    def _factory(params: dict, base_config) -> object:
        if params.get("tag") == "BASE":
            return best_s2_config
        return dataclasses.replace(
            best_s2_config,
            breakeven_trigger_pct=params["breakeven_trigger_pct"],
            breakeven_offset_pct=params["breakeven_offset_pct"],
        )
    return _factory


# ---------------------------------------------------------------------------
# 결과 출력
# ---------------------------------------------------------------------------

def _print_table(rows: list[dict], title: str, baseline_sl: int = 0) -> None:
    print(f"\n{title}")
    hdr = f"{'tag':>5} {'mult':>5} {'min':>5} {'max':>5} | {'trades':>7} {'PF':>6} {'PnL':>11} {'win%':>6} {'MDD%':>6} {'SL#':>5}"
    sep = "=" * len(hdr)
    print(sep)
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        tag = r.get("tag", "")
        mult = f"{r['atr_stop_multiplier']:.1f}" if "atr_stop_multiplier" in r else "-"
        mn   = f"{r['atr_stop_min_pct']:.0%}" if "atr_stop_min_pct" in r else "-"
        mx   = f"{r['atr_stop_max_pct']:.0%}" if "atr_stop_max_pct" in r else "-"
        # Stage 2/3 columns
        if "atr_trail_min_pct" in r:
            mult = f"{r['atr_trail_min_pct']:.3f}"
            mn   = f"{r['atr_trail_max_pct']:.2f}"
            mx   = "-"
        if "breakeven_trigger_pct" in r:
            mult = f"{r['breakeven_trigger_pct']:.0%}"
            mn   = f"{r['breakeven_offset_pct']:.1%}"
            mx   = "-"
        sl_mark = "*" if r.get("stop_loss_n", 0) < baseline_sl else ""
        print(
            f"{tag:>5} {mult:>5} {mn:>5} {mx:>5} | "
            f"{r['trades']:>7} {r['pf']:>6.3f} "
            f"{r['pnl']:>+11,} {r['win_rate']:>6.1%} "
            f"{r['mdd_pct']:>6.2f} {r['stop_loss_n']:>4}{sl_mark}"
        )
    print(sep)


def _select_best(rows: list[dict], pf_thr: float) -> dict | None:
    """PF >= thr 조합 중 PnL 최대. BASE 제외."""
    candidates = [r for r in rows if r.get("tag") != "BASE" and r["pf"] >= pf_thr]
    if not candidates:
        return None
    return max(candidates, key=lambda x: x["pnl"])


# ---------------------------------------------------------------------------
# 보고서
# ---------------------------------------------------------------------------

def _write_report(
    stage1_rows: list[dict],
    stage2_rows: list[dict],
    stage3_rows: list[dict],
    best_s1: dict | None,
    best_s2: dict | None,
    best_s3: dict | None,
) -> None:
    out = Path("reports/atr_stop_grid.md")
    out.parent.mkdir(exist_ok=True)

    def _md_table(rows: list[dict], cols: list[tuple[str, str]]) -> list[str]:
        header = "| " + " | ".join(label for _, label in cols) + " |"
        sep    = "| " + " | ".join("---" for _ in cols) + " |"
        lines  = [header, sep]
        for r in rows:
            def _v(k):
                v = r.get(k, "-")
                if isinstance(v, float):
                    return f"{v:.3f}"
                return str(v)
            lines.append("| " + " | ".join(_v(k) for k, _ in cols) + " |")
        return lines

    s1_cols = [
        ("tag","tag"),("atr_stop_multiplier","mult"),("atr_stop_min_pct","min"),
        ("atr_stop_max_pct","max"),("trades","trades"),("pf","PF"),("pnl","PnL"),
        ("win_rate","win%"),("mdd_pct","MDD%"),("stop_loss_n","SL#"),
    ]
    s2_cols = [
        ("tag","tag"),("atr_trail_min_pct","trail_min"),("atr_trail_max_pct","trail_max"),
        ("trades","trades"),("pf","PF"),("pnl","PnL"),("win_rate","win%"),
        ("mdd_pct","MDD%"),("stop_loss_n","SL#"),
    ]
    s3_cols = [
        ("tag","tag"),("breakeven_trigger_pct","be_trig"),("breakeven_offset_pct","be_off"),
        ("trades","trades"),("pf","PF"),("pnl","PnL"),("win_rate","win%"),
        ("mdd_pct","MDD%"),("stop_loss_n","SL#"),
    ]

    lines = [
        "# ATR 비례 손절 그리드",
        f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> 구간: {OLD_START} ~ {OLD_END}  PF 선정 기준: {PF_THRESHOLD}",
        "",
        f"## Stage 1 — ATR 손절 ({len(stage1_rows)}조합)",
        "",
    ] + _md_table(stage1_rows, s1_cols) + [""]

    if stage2_rows:
        lines += [f"## Stage 2 — Trail 범위 ({len(stage2_rows)}조합)", ""]
        lines += _md_table(stage2_rows, s2_cols) + [""]

    if stage3_rows:
        lines += [f"## Stage 3 — Breakeven ({len(stage3_rows)}조합)", ""]
        lines += _md_table(stage3_rows, s3_cols) + [""]

    lines += ["## 최종 선정"]
    if best_s3 and best_s3["pf"] >= PF_THRESHOLD:
        lines += [
            f"- Stage 1: mult={best_s1.get('atr_stop_multiplier','-')} min={best_s1.get('atr_stop_min_pct','-')} max={best_s1.get('atr_stop_max_pct','-')}",
            f"- Stage 2: trail_min={best_s2.get('atr_trail_min_pct','-')} trail_max={best_s2.get('atr_trail_max_pct','-')}",
            f"- Stage 3: be_trig={best_s3.get('breakeven_trigger_pct','-')} be_off={best_s3.get('breakeven_offset_pct','-')}",
            f"- **최종 PF {best_s3['pf']:.3f} / PnL {best_s3['pnl']:+,} / MDD {best_s3['mdd_pct']:.2f}% / SL# {best_s3['stop_loss_n']}**",
        ]
    elif best_s1 and best_s1.get("pf", 0) >= PF_THRESHOLD:
        lines += [
            f"- Stage 1 최적: mult={best_s1.get('atr_stop_multiplier','-')} min={best_s1.get('atr_stop_min_pct','-')} max={best_s1.get('atr_stop_max_pct','-')}",
            f"- PF {best_s1.get('pf','-')} / PnL {best_s1.get('pnl','-')} / SL# {best_s1.get('stop_loss_n','-')}",
        ]
    else:
        lines += ["선정 기준 미달 -- 현재 파라미터 유지"]

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[SAVED] {out}", flush=True)


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

async def run_verify(cache: GridCache) -> None:
    from backtest.backtester import Backtester
    from strategy.momentum_strategy import MomentumStrategy

    print("[VERIFY] atr_stop=false baseline PF 4.817 재현", flush=True)
    cfg = dataclasses.replace(cache.base_config, atr_stop_enabled=False)
    old_cache = cache.filter_dates(OLD_START, OLD_END)
    all_trades: list[dict] = []
    for tk, df in old_cache.candles.items():
        market = old_cache.ticker_to_market.get(tk, "unknown")
        bt = Backtester(
            db=None, config=cfg, backtest_config=old_cache.bt_config,
            ticker_market=market, market_strong_by_date=old_cache.market_map,
        )
        strat = MomentumStrategy(cfg)
        result = await bt.run_multi_day_cached(tk, df, strat)
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)

    stats = compute_stats(all_trades)
    sl_n  = stats["exit_counts"].get("stop_loss", 0)
    expected_pf = 4.817
    ok = abs(stats["pf"] - expected_pf) <= 0.05
    print(f"  PF={stats['pf']:.3f} trades={stats['trades']} pnl={stats['pnl']:+,} SL#={sl_n}", flush=True)
    print(f"  {'PASS' if ok else 'FAIL'} (expected {expected_pf}+-0.05)", flush=True)


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

async def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify",    action="store_true")
    ap.add_argument("--stage",     type=int, default=0, help="0=전체, 1/2/3=개별")
    # Stage 2 단독 실행 시 Stage 1 최적 파라미터 주입
    ap.add_argument("--s1-mult",   type=float, default=None)
    ap.add_argument("--s1-min",    type=float, default=None)
    ap.add_argument("--s1-max",    type=float, default=None)
    # Stage 3 단독 실행 시 Stage 2 최적 파라미터 주입
    ap.add_argument("--s2-trail-min", type=float, default=None)
    ap.add_argument("--s2-trail-max", type=float, default=None)
    args = ap.parse_args()

    full_cache = await load_candle_cache("2025-04-01", "2026-04-10")

    if args.verify:
        await run_verify(full_cache)
        return

    stage1_rows: list[dict] = []
    stage2_rows: list[dict] = []
    stage3_rows: list[dict] = []
    best_s1 = best_s2 = best_s3 = None
    best_s1_cfg = best_s2_cfg = None

    # -----------------------------------------------------------------------
    # Stage 1
    # -----------------------------------------------------------------------
    if args.stage in (0, 1):
        combos = _build_stage1_combos()
        print(f"\n[Stage 1] ATR 손절 {len(combos)}조합 (baseline 1 + ATR {len(combos)-1})", flush=True)
        df1 = run_parallel_grid(combos, _s1_factory, full_cache, worker_fn=_atr_stop_worker)
        stage1_rows = df1.to_dict("records")
        base_row = next((r for r in stage1_rows if r.get("tag") == "BASE"), {})
        baseline_sl = base_row.get("stop_loss_n", 0)
        _print_table(stage1_rows, f"Stage 1 ({OLD_START} ~ {OLD_END})", baseline_sl)

        best_s1 = _select_best(stage1_rows, PF_THRESHOLD)
        if best_s1:
            print(
                f"\n[S1 최적] mult={best_s1['atr_stop_multiplier']:.1f} "
                f"min={best_s1['atr_stop_min_pct']:.0%} max={best_s1['atr_stop_max_pct']:.0%} "
                f"| PF {best_s1['pf']:.3f} PnL {best_s1['pnl']:+,} "
                f"SL# {best_s1['stop_loss_n']} (baseline {baseline_sl})",
                flush=True,
            )
            best_s1_cfg = dataclasses.replace(
                full_cache.base_config,
                atr_stop_enabled=True,
                atr_stop_multiplier=best_s1["atr_stop_multiplier"],
                atr_stop_min_pct=best_s1["atr_stop_min_pct"],
                atr_stop_max_pct=best_s1["atr_stop_max_pct"],
            )
        else:
            print(f"\n[S1 결과] PF>={PF_THRESHOLD} 조합 없음 -- ATR 손절 비활성 유지", flush=True)
            if args.stage == 1:
                _write_report(stage1_rows, [], [], best_s1, None, None)
                return

        if args.stage == 1:
            _write_report(stage1_rows, [], [], best_s1, None, None)
            return

    # Stage 2/3 단독 실행 시 Stage 1 best를 CLI 인자에서 복원
    if args.stage in (2, 3) and args.s1_mult is not None:
        best_s1_cfg = dataclasses.replace(
            full_cache.base_config,
            atr_stop_enabled=True,
            atr_stop_multiplier=args.s1_mult,
            atr_stop_min_pct=args.s1_min,
            atr_stop_max_pct=args.s1_max,
        )
        best_s1 = {
            "atr_stop_multiplier": args.s1_mult,
            "atr_stop_min_pct": args.s1_min,
            "atr_stop_max_pct": args.s1_max,
        }

    if best_s1_cfg is None:
        print("[SKIP] Stage 2/3 생략 (Stage 1 미통과)", flush=True)
        _write_report(stage1_rows, [], [], None, None, None)
        return

    # -----------------------------------------------------------------------
    # Stage 2
    # -----------------------------------------------------------------------
    if args.stage in (0, 2):
        combos2 = _build_stage2_combos()
        print(f"\n[Stage 2] trail 범위 {len(combos2)}조합 (Stage 1 최적 위에서)", flush=True)
        s2_factory = _make_s2_factory(best_s1_cfg)
        df2 = run_parallel_grid(combos2, s2_factory, full_cache, worker_fn=_atr_stop_worker)
        stage2_rows = df2.to_dict("records")
        base2_row = next((r for r in stage2_rows if r.get("tag") == "BASE"), {})
        _print_table(stage2_rows, "Stage 2 (trail 범위)", base2_row.get("stop_loss_n", 0))

        best_s2 = _select_best(stage2_rows, PF_THRESHOLD)
        if best_s2:
            print(
                f"\n[S2 최적] trail_min={best_s2['atr_trail_min_pct']:.3f} "
                f"trail_max={best_s2['atr_trail_max_pct']:.2f} "
                f"| PF {best_s2['pf']:.3f} PnL {best_s2['pnl']:+,}",
                flush=True,
            )
            best_s2_cfg = dataclasses.replace(
                best_s1_cfg,
                atr_trail_min_pct=best_s2["atr_trail_min_pct"],
                atr_trail_max_pct=best_s2["atr_trail_max_pct"],
            )
        else:
            print(f"\n[S2 결과] 개선 없음 -- Stage 1 최적 trail 파라미터 유지", flush=True)
            best_s2_cfg = best_s1_cfg
            best_s2 = {"atr_trail_min_pct": best_s1_cfg.atr_trail_min_pct,
                       "atr_trail_max_pct": best_s1_cfg.atr_trail_max_pct}

        if args.stage == 2:
            _write_report(stage1_rows, stage2_rows, [], best_s1, best_s2, None)
            return

    # Stage 3 단독 실행 시 Stage 2 best를 CLI 인자에서 복원
    if args.stage == 3 and args.s2_trail_min is not None:
        if best_s1_cfg is None:
            print("[ERR] Stage 3 단독 실행 시 --s1-* 인자 필요", flush=True)
            return
        best_s2_cfg = dataclasses.replace(
            best_s1_cfg,
            atr_trail_min_pct=args.s2_trail_min,
            atr_trail_max_pct=args.s2_trail_max,
        )
        best_s2 = {"atr_trail_min_pct": args.s2_trail_min, "atr_trail_max_pct": args.s2_trail_max}

    if best_s2_cfg is None:
        best_s2_cfg = best_s1_cfg

    # -----------------------------------------------------------------------
    # Stage 3
    # -----------------------------------------------------------------------
    combos3 = _build_stage3_combos()
    print(f"\n[Stage 3] breakeven {len(combos3)}조합 (Stage 2 최적 위에서)", flush=True)
    s3_factory = _make_s3_factory(best_s2_cfg)
    df3 = run_parallel_grid(combos3, s3_factory, full_cache, worker_fn=_atr_stop_worker)
    stage3_rows = df3.to_dict("records")
    base3_row = next((r for r in stage3_rows if r.get("tag") == "BASE"), {})
    _print_table(stage3_rows, "Stage 3 (breakeven)", base3_row.get("stop_loss_n", 0))

    best_s3 = _select_best(stage3_rows, PF_THRESHOLD)
    if best_s3:
        print(
            f"\n[S3 최적] be_trig={best_s3['breakeven_trigger_pct']:.0%} "
            f"be_off={best_s3['breakeven_offset_pct']:.1%} "
            f"| PF {best_s3['pf']:.3f} PnL {best_s3['pnl']:+,}",
            flush=True,
        )
    else:
        print(f"\n[S3 결과] PF>={PF_THRESHOLD} 조합 없음 -- Stage 2 breakeven 파라미터 유지", flush=True)
        best_s3 = {"breakeven_trigger_pct": best_s2_cfg.breakeven_trigger_pct,
                   "breakeven_offset_pct": best_s2_cfg.breakeven_offset_pct,
                   "pf": base3_row.get("pf", 0),
                   "pnl": base3_row.get("pnl", 0),
                   "stop_loss_n": base3_row.get("stop_loss_n", 0),
                   "mdd_pct": base3_row.get("mdd_pct", 0)}

    _write_report(stage1_rows, stage2_rows, stage3_rows, best_s1, best_s2, best_s3)


if __name__ == "__main__":
    asyncio.run(main())
