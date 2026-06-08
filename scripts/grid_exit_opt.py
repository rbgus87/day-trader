"""scripts/grid_exit_opt.py — 모멘텀 청산 최적화 그리드.

atr_trail_multiplier 3조합으로 trailing_stop 수익 극대화 검증.

현행 baseline:
  atr_trail_multiplier=1.0
  → OLD PF=4.881 / PnL=+295,690 / #228

그리드:
  atr_trail_multiplier: [0.8, 1.0, 1.2]
  → 3조합

추가 측정:
  - trailing_stop 건수 + 평균 수익률
  - breakeven_stop 건수
  - "남긴 수익" (trailing_stop 청산가 vs 당일 고가 차이, median)

구간:
  OLD: 2025-04-01 ~ 2026-04-10
  NEW: 2026-04-11 ~ 2026-05-19

사용:
    python -u scripts/grid_exit_opt.py
    python -u scripts/grid_exit_opt.py --verify  # baseline만 검증
"""
from __future__ import annotations

import asyncio
import dataclasses
import sys
import time as _time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")

from utils.grid_runner import GridCache, load_candle_cache, run_parallel_grid

# ---------------------------------------------------------------------------
# 날짜 구간
# ---------------------------------------------------------------------------

OLD_START = "2025-04-01"
OLD_END   = "2026-04-10"
NEW_START = "2026-04-11"
NEW_END   = "2026-05-19"

BASELINE = {
    "pf": 4.881, "pnl": 295_690, "trades": 228,
}

VERIFY_TOLERANCE = {"pf": 0.05, "pnl": 5000}

# ---------------------------------------------------------------------------
# 그리드 파라미터
# ---------------------------------------------------------------------------

TRAIL_MULTS  = [0.8, 1.0, 1.2]


# ---------------------------------------------------------------------------
# 확장 통계 (trailing_stop 분석 포함)
# ---------------------------------------------------------------------------

def _compute_extended(trades: list[dict]) -> dict:
    from collections import Counter

    n = len(trades)
    if n == 0:
        return {
            "pf": 0.0, "pnl": 0, "trades": 0,
            "win_rate": 0.0, "fc_pct": 0.0,
            "trail_cnt": 0, "trail_avg_ret": 0.0, "trail_left_pct": 0.0,
            "be_cnt": 0, "be_avg_ret": 0.0,
            "sl_cnt": 0, "sl_avg_ret": 0.0,
        }

    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    pnl = sum(t["pnl"] for t in trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    exits = Counter(t.get("exit_reason", "?") for t in trades)

    def _avg_ret(reason: str) -> float:
        sub = [t["pnl_pct"] for t in trades if t.get("exit_reason") == reason]
        return round(sum(sub) / len(sub) * 100, 2) if sub else 0.0

    # "남긴 수익" — trailing_stop 청산가 vs 당일 고가(highest_price) 차이
    trail_left = []
    for t in trades:
        if t.get("exit_reason") == "trailing_stop":
            peak = t.get("highest_price", 0.0)
            ep   = t.get("entry_price", 0.0)
            xp   = t.get("exit_price", 0.0)
            if peak > 0 and ep > 0 and peak > xp:
                left_pct = (peak - xp) / ep * 100.0
                trail_left.append(left_pct)
    trail_left_median = sorted(trail_left)[len(trail_left) // 2] if trail_left else 0.0

    return {
        "pf":             round(gp / gl, 4) if gl > 0 else float("inf"),
        "pnl":            int(pnl),
        "trades":         n,
        "win_rate":       round(wins / n, 4),
        "fc_pct":         round(exits.get("forced_close", 0) / n * 100, 2),
        "trail_cnt":      exits.get("trailing_stop", 0),
        "trail_avg_ret":  _avg_ret("trailing_stop"),
        "trail_left_pct": round(trail_left_median, 2),
        "be_cnt":         exits.get("breakeven_stop", 0),
        "be_avg_ret":     _avg_ret("breakeven_stop"),
        "sl_cnt":         exits.get("stop_loss", 0),
        "sl_avg_ret":     _avg_ret("stop_loss"),
    }


# ---------------------------------------------------------------------------
# 워커 (ProcessPool에서 호출)
# ---------------------------------------------------------------------------

def _exit_opt_worker(args: tuple) -> dict:
    config, candles_bytes, market_map_bytes, ticker_to_market, bt_config, params_dict = args

    import asyncio as _asyncio
    import pickle
    import sys
    from loguru import logger as _l
    _l.remove()
    _l.add(sys.stderr, level="WARNING")

    from backtest.backtester_fast import FastBacktester as _FBT
    from strategy.momentum_strategy import MomentumStrategy as _MS

    candles_cache = pickle.loads(candles_bytes)
    market_map    = pickle.loads(market_map_bytes)

    all_trades = []
    for tk, df in candles_cache.items():
        market = ticker_to_market.get(tk, "unknown")
        bt = _FBT(
            db=None, config=config, backtest_config=bt_config,
            ticker_market=market, market_strong_by_date=market_map,
        )
        strategy = _MS(config)
        result = _asyncio.run(bt.run_multi_day_cached(tk, df, strategy))
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)

    stats = _compute_extended(all_trades)
    return {**params_dict, **stats}


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

async def main(verify_only: bool = False):
    import dataclasses as _dc
    from concurrent.futures import ProcessPoolExecutor
    import multiprocessing as _mp
    import os
    import pickle

    print("=" * 60)
    print("청산 최적화 그리드 — trail_mult × be3_ratio")
    print("=" * 60)

    t0 = _time.time()

    print("[LOAD] OLD 구간 캔들 로드…")
    cache_old = await load_candle_cache(OLD_START, OLD_END)
    print("[LOAD] NEW 구간 캔들 로드…")
    cache_new = await load_candle_cache(NEW_START, NEW_END)

    # 조합 생성
    if verify_only:
        combos = [{"atr_trail_multiplier": 1.0}]
    else:
        combos = [{"atr_trail_multiplier": tm} for tm in TRAIL_MULTS]

    def _config_factory(params: dict, base_cfg) -> object:
        return _dc.replace(base_cfg, **params)

    old_combos_cfg = [_config_factory(p, cache_old.base_config) for p in combos]
    new_combos_cfg = [_config_factory(p, cache_new.base_config) for p in combos]

    ncpu = min(max(1, os.cpu_count() - 1), len(combos))
    print(f"[RUN] OLD {len(combos)}조합 × 병렬 {ncpu}프로세스")

    def _build_args(combos_cfg, cache, combo_params_list):
        return [
            (cfg,
             cache.candles_bytes,
             cache.market_map_bytes,
             cache.ticker_to_market,
             cache.bt_config,
             combo_params_list[i])
            for i, cfg in enumerate(combos_cfg)
        ]

    old_args = _build_args(old_combos_cfg, cache_old, combos)
    new_args = _build_args(new_combos_cfg, cache_new, combos)

    print("[RUN] OLD 구간…")
    t1 = _time.time()
    with ProcessPoolExecutor(max_workers=ncpu) as pool:
        old_results = list(pool.map(_exit_opt_worker, old_args))
    print(f"  완료 ({_time.time() - t1:.1f}초)")

    print("[RUN] NEW 구간…")
    t2 = _time.time()
    with ProcessPoolExecutor(max_workers=ncpu) as pool:
        new_results = list(pool.map(_exit_opt_worker, new_args))
    print(f"  완료 ({_time.time() - t2:.1f}초)")

    total = _time.time() - t0
    print(f"\n[완료] 총 {total:.1f}초")

    # 결과 출력
    print("\n" + "=" * 70)
    print("결과 요약 (OLD 구간)")
    print("=" * 70)
    hdr = f"{'trail_mult':>10} | {'PF':>6} {'PnL':>10} {'#':>5} {'trail#':>7} {'trail_avg%':>10} {'left%':>7} | {'NEW_PF':>6}"
    print(hdr)
    print("-" * len(hdr))

    rows = []
    for i, (old_r, new_r, p) in enumerate(zip(old_results, new_results, combos)):
        is_baseline = (p["atr_trail_multiplier"] == 1.0)
        mark = " ←" if is_baseline else ""
        print(
            f"{p['atr_trail_multiplier']:>10.1f} | "
            f"{old_r['pf']:>6.3f} {old_r['pnl']:>+10,} {old_r['trades']:>5} "
            f"{old_r['trail_cnt']:>7} {old_r['trail_avg_ret']:>+9.1f}% "
            f"{old_r['trail_left_pct']:>6.1f}% | {new_r['pf']:>6.3f}{mark}"
        )
        rows.append({**p, **{f"old_{k}": v for k, v in old_r.items()},
                              **{f"new_{k}": v for k, v in new_r.items()}})

    # baseline 검증 (verify_only)
    if verify_only:
        r = old_results[0]
        pf_ok  = abs(r["pf"] - BASELINE["pf"]) <= VERIFY_TOLERANCE["pf"]
        pnl_ok = abs(r["pnl"] - BASELINE["pnl"]) <= VERIFY_TOLERANCE["pnl"]
        status = "OK" if pf_ok and pnl_ok else "MISMATCH"
        print(f"\n[VERIFY] PF={r['pf']:.3f} (expect {BASELINE['pf']}) → {'OK' if pf_ok else 'FAIL'}")
        print(f"[VERIFY] PnL={r['pnl']:+,} (expect {BASELINE['pnl']:+,}) → {'OK' if pnl_ok else 'FAIL'}")
        print(f"[VERIFY] 종합: {status}")

    # 선정 기준
    print("\n[선정기준] PnL >= baseline PnL인 조합:")
    passed = [r for r in rows if r["old_pnl"] >= BASELINE["pnl"]]
    if passed:
        for r in passed:
            print(f"  → trail_mult={r['atr_trail_multiplier']:.1f} "
                  f"PF={r['old_pf']:.3f} PnL={r['old_pnl']:+,}")
    else:
        print("  → 없음 (baseline PnL 이상 조합 없음)")

    if not verify_only:
        _save_report(rows)


def _save_report(rows: list[dict]):
    from datetime import datetime

    lines = [
        "# 모멘텀 청산 최적화 그리드 결과",
        "",
        f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> baseline trail_mult=1.0 → OLD PF={BASELINE['pf']} / PnL={BASELINE['pnl']:+,}",
        "",
        "## 결과 테이블 (OLD 구간 기준)",
        "",
        "| trail_mult | OLD_PF | OLD_PnL | #거래 | trail# | trail_avg% | left% | NEW_PF | NEW_PnL |",
        "|-----------|--------|---------|-------|--------|-----------|-------|--------|---------|",
    ]
    for r in rows:
        is_bl = r["atr_trail_multiplier"] == 1.0
        mark = " ←" if is_bl else ""
        lines.append(
            f"| {r['atr_trail_multiplier']:.1f} | "
            f"{r['old_pf']:.3f} | {r['old_pnl']:+,} | {r['old_trades']} | "
            f"{r['old_trail_cnt']} | {r['old_trail_avg_ret']:+.1f}% | "
            f"{r['old_trail_left_pct']:.1f}% | {r['new_pf']:.3f} | {r['new_pnl']:+,} |{mark}"
        )

    lines += [
        "",
        "## 지표 설명",
        "",
        "- **trail#**: trailing_stop으로 청산된 건수",
        "- **trail_avg%**: trailing_stop 청산 거래 평균 수익률",
        "- **left%**: trailing_stop 청산 시 '남긴 수익' (당일고가 vs 청산가 차이, median, 진입가 대비 %)",
        "  - 높을수록 trail이 너무 빡빡 → multiplier 키우면 개선 가능",
        "  - 낮을수록 trail이 충분히 느슨",
        "",
        "## 선정 기준",
        "",
        "- PnL >= baseline PnL ({:+,})".format(BASELINE["pnl"]),
        "- NEW 기간 방향성 일치 (PF 개선 또는 유지)",
    ]

    out = Path("reports/momentum_exit_grid_result.md")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[저장] {out}")


if __name__ == "__main__":
    verify = "--verify" in sys.argv
    asyncio.run(main(verify_only=verify))
