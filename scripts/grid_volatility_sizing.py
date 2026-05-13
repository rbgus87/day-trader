"""scripts/grid_volatility_sizing.py -- 변동성 기반 포지션 사이징 파라미터 그리드.

조합: risk_per_trade_pct × sizing_min_pct (sizing_atr_multiplier=1.0, sizing_max_pct=0.50 고정)
baseline: volatility_sizing_enabled=false (균등 분배)

각 조합 측정:
  PF, PnL(₩), 거래수, 승률, MDD(%), 평균 포지션 크기(₩)

기존 구간(~04-10) + 확장 구간(04-11~05-12) 모두 측정.
결과 → reports/volatility_sizing_grid.md

사용:
    python -u scripts/grid_volatility_sizing.py
    python -u scripts/grid_volatility_sizing.py --verify
"""
from __future__ import annotations

import asyncio
import dataclasses
import sys
from datetime import date
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")

from utils.grid_runner import GridCache, compute_stats, load_candle_cache, run_parallel_grid

OLD_START = "2025-04-01"
OLD_END   = "2026-04-10"
NEW_START = "2026-04-11"
NEW_END   = "2026-05-12"

RISK_VALS    = [0.005, 0.01, 0.015, 0.02]
MIN_PCT_VALS = [0.10, 0.15, 0.20]

INITIAL_CAPITAL = 5_000_000


# ---------------------------------------------------------------------------
# 커스텀 워커 — position_value / MDD 계산 포함
# ---------------------------------------------------------------------------

def _vol_sizing_worker(args: tuple) -> dict:
    """변동성 사이징 워커: pnl_pct 기반 ₩ PnL + 포지션 크기 + MDD 산출."""
    config, candles_bytes, market_map_bytes, ticker_to_market, bt_config, params_dict = args

    from loguru import logger as _l
    _l.remove()
    _l.add(sys.stderr, level="WARNING")

    import asyncio as _a
    import pickle
    from backtest.backtester import (
        Backtester as _BT,
        calc_sizing_position_value,
        compute_atr_pct_from_candles,
    )
    from strategy.momentum_strategy import MomentumStrategy as _MS
    from utils.grid_runner import compute_stats as _stats

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

    stats = _stats(all_trades)
    mdd = _compute_mdd(all_trades, INITIAL_CAPITAL)
    avg_pos = _avg_position_value(all_trades)

    return {
        **params_dict,
        **stats,
        "mdd_pct": round(mdd * 100, 2),
        "avg_pos_val": int(avg_pos),
    }


def _compute_mdd(trades: list[dict], initial_capital: float) -> float:
    """거래 시계열 순차 합산 기반 MDD (비율)."""
    if not trades:
        return 0.0
    sorted_trades = sorted(
        trades,
        key=lambda t: str(t.get("exit_ts", "") or ""),
    )
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


def _avg_position_value(trades: list[dict]) -> float:
    """trades 중 position_value 필드 평균 (없으면 0)."""
    vals = [t["position_value"] for t in trades if "position_value" in t]
    return sum(vals) / len(vals) if vals else 0.0


# ---------------------------------------------------------------------------
# 그리드 빌드
# ---------------------------------------------------------------------------

def build_combos() -> list[dict]:
    combos = [{"vol": False}]  # baseline
    for risk, min_pct in product(RISK_VALS, MIN_PCT_VALS):
        combos.append({
            "vol": True,
            "risk_per_trade_pct": risk,
            "sizing_min_pct": min_pct,
        })
    return combos


def config_factory(params: dict, base_config) -> object:
    if not params.get("vol"):
        return dataclasses.replace(base_config, volatility_sizing_enabled=False)
    return dataclasses.replace(
        base_config,
        volatility_sizing_enabled=True,
        risk_per_trade_pct=params["risk_per_trade_pct"],
        sizing_min_pct=params["sizing_min_pct"],
        sizing_atr_multiplier=1.0,
        sizing_max_pct=0.50,
        initial_capital=INITIAL_CAPITAL,
    )


# ---------------------------------------------------------------------------
# 결과 출력 + 보고서
# ---------------------------------------------------------------------------

def _print_results(rows: list[dict], title: str) -> None:
    print(f"\n{title}")
    hdr = (
        f"{'risk':>6} {'min':>5} | {'trades':>7} {'PF':>6} "
        f"{'PnL':>11} {'win%':>6} {'MDD%':>6} {'avgPos':>10}"
    )
    print("=" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        if not r.get("vol"):
            tag = "BASE"
            risk_s = "-"
            min_s = "-"
        else:
            tag = ""
            risk_s = f"{r['risk_per_trade_pct']:.1%}"
            min_s = f"{r['sizing_min_pct']:.0%}"
        print(
            f"{risk_s:>6} {min_s:>5} | "
            f"{r['trades']:>7} {r['pf']:>6.3f} "
            f"{r['pnl']:>+11,} {r['win_rate']:>6.1%} "
            f"{r['mdd_pct']:>6.2f} {r['avg_pos_val']:>10,}  {tag}"
        )
    print("=" * len(hdr))


def _select_best(rows: list[dict], base: dict) -> dict | None:
    """MDD < baseline AND PF >= baseline×0.90 조합 중 PnL 최대."""
    base_mdd = base.get("mdd_pct", 999.0)
    base_pf  = base.get("pf", 0.0)
    candidates = [
        r for r in rows
        if r.get("vol")
        and r["mdd_pct"] < base_mdd
        and r["pf"] >= base_pf * 0.90
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda x: x["pnl"])


def _write_report(old_rows: list[dict], new_rows: list[dict]) -> None:
    from datetime import datetime
    out = Path("reports/volatility_sizing_grid.md")
    out.parent.mkdir(exist_ok=True)

    def _table(rows: list[dict]) -> list[str]:
        lines = [
            "| risk | min_pct | trades | PF | PnL | win% | MDD% | avg_pos |",
            "|------|---------|--------|-----|-----|------|------|---------|",
        ]
        for r in rows:
            tag = "**BASE**" if not r.get("vol") else ""
            lines.append(
                f"| {r.get('risk_per_trade_pct', '-')} "
                f"| {r.get('sizing_min_pct', '-')} "
                f"| {r['trades']} | {r['pf']:.3f} | {r['pnl']:+,} "
                f"| {r['win_rate']:.1%} | {r['mdd_pct']:.2f}% "
                f"| {r['avg_pos_val']:,} | {tag} |"
            )
        return lines

    base_old = next((r for r in old_rows if not r.get("vol")), {})
    best_old = _select_best(old_rows, base_old)

    content = [
        "# 변동성 기반 포지션 사이징 그리드",
        f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> initial_capital={INITIAL_CAPITAL:,}  sizing_atr_multiplier=1.0  sizing_max_pct=50%",
        "",
        f"## 기존 구간 ({OLD_START} ~ {OLD_END})",
        "",
    ] + _table(old_rows) + [
        "",
        f"## 확장 구간 ({NEW_START} ~ {NEW_END})",
        "",
    ] + _table(new_rows)

    if best_old:
        content += [
            "",
            "## 최적 조합 (기존 구간 기준)",
            f"- risk_per_trade_pct: **{best_old['risk_per_trade_pct']:.1%}**",
            f"- sizing_min_pct: **{best_old['sizing_min_pct']:.0%}**",
            f"- PF: {best_old['pf']:.3f}  PnL: {best_old['pnl']:+,}  MDD: {best_old['mdd_pct']:.2f}%",
            f"- baseline MDD: {base_old.get('mdd_pct', 0):.2f}%  PF: {base_old.get('pf', 0):.3f}",
        ]
    else:
        content += [
            "",
            "## 판정",
            "선정 기준(MDD < baseline AND PF >= baseline×90%) 만족 조합 없음 -- 균등 분배 유지",
        ]

    out.write_text("\n".join(content), encoding="utf-8")
    print(f"\n[SAVED] {out}", flush=True)


# ---------------------------------------------------------------------------
# 검증 모드
# ---------------------------------------------------------------------------

async def run_verify(cache: GridCache) -> None:
    """baseline(균등 분배)으로 PF 4.817 재현 확인."""
    from backtest.backtester import Backtester
    from strategy.momentum_strategy import MomentumStrategy

    print("[VERIFY] volatility_sizing=false baseline 검증", flush=True)
    cfg = dataclasses.replace(cache.base_config, volatility_sizing_enabled=False)
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
    expected_pf = 4.817
    ok = abs(stats["pf"] - expected_pf) <= 0.05
    print(f"  PF={stats['pf']:.3f} trades={stats['trades']} pnl={stats['pnl']:+,}", flush=True)
    print(f"  결과: {'PASS' if ok else 'FAIL'} (expected {expected_pf}+-0.05)", flush=True)


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

async def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    full_cache = await load_candle_cache("2025-04-01", "2026-05-12")

    if args.verify:
        await run_verify(full_cache)
        return

    combos = build_combos()
    print(f"\n총 {len(combos)}조합 (baseline 1 + vol_sizing {len(combos)-1})", flush=True)

    # 기존 구간
    print(f"\n=== 기존 구간 ({OLD_START} ~ {OLD_END}) ===", flush=True)
    old_cache = full_cache.filter_dates(OLD_START, OLD_END)
    old_df = run_parallel_grid(
        combos, config_factory, old_cache, worker_fn=_vol_sizing_worker
    )
    old_rows = old_df.to_dict("records")
    _print_results(old_rows, f"기존 구간 ({OLD_START} ~ {OLD_END})")

    # 확장 구간
    print(f"\n=== 확장 구간 ({NEW_START} ~ {NEW_END}) ===", flush=True)
    new_cache = full_cache.filter_dates(NEW_START, NEW_END)
    new_df = run_parallel_grid(
        combos, config_factory, new_cache, worker_fn=_vol_sizing_worker
    )
    new_rows = new_df.to_dict("records")
    _print_results(new_rows, f"확장 구간 ({NEW_START} ~ {NEW_END})")

    # 최적 선정
    base_old = next((r for r in old_rows if not r.get("vol")), {})
    best = _select_best(old_rows, base_old)
    if best:
        print(f"\n[최적] risk={best['risk_per_trade_pct']:.1%}  min_pct={best['sizing_min_pct']:.0%}", flush=True)
        print(f"  PF {best['pf']:.3f} / PnL {best['pnl']:+,} / MDD {best['mdd_pct']:.2f}%", flush=True)
        print(f"  baseline: PF {base_old.get('pf',0):.3f} / MDD {base_old.get('mdd_pct',0):.2f}%", flush=True)
    else:
        print("\n[결과] 선정 기준 미달 -- volatility_sizing 비활성 유지", flush=True)

    _write_report(old_rows, new_rows)


if __name__ == "__main__":
    asyncio.run(main())
