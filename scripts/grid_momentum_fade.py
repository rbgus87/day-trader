"""scripts/grid_momentum_fade.py — momentum_fade 파라미터 그리드 서치.

3개 파라미터를 격자 탐색하여 PF >= 4.1 + forced_close <= 40% + PnL >= 260K
조건을 만족하는 최적 조합을 찾는다.

최적화:
- 캔들 데이터를 첫 회만 로드하여 dict에 캐시 (DB 반복 조회 제거).
- ProcessPoolExecutor 병렬화 — 종목별 백테스트를 워커들에 분산.
- spawn context 명시 (Windows BrokenProcessPool 회피).
- --verify: 1조합(default 파라미터) 실행 후 기존 baseline PF와 비교.

사용:
    python scripts/grid_momentum_fade.py --verify    # 1조합 baseline 검증
    python scripts/grid_momentum_fade.py             # 36조합 그리드
"""

import argparse
import asyncio
import dataclasses
import multiprocessing as mp
import os
import pickle
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# loguru DEBUG/INFO 출력 억제 — 백테스터의 자체 로그가 파일 I/O로 지연 유발
from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")

from backtest.backtester import Backtester
from strategy.momentum_strategy import MomentumStrategy


# 그리드 파라미터 (사용자 spec: 4 × 3 × 3 = 36 조합)
THRESHOLDS = [-0.005, -0.008, -0.010, -0.015]
MIN_PROFITS = [0.01, 0.02, 0.03]
MIN_HOLDS = [15, 20, 30]

# baseline 검증 임계 (PF 3.80 ± 0.01 같은 결정성 — 동일 코드/데이터/파라미터면 정확히 동일)
BASELINE_EXPECTED_PF = 3.80
BASELINE_EXPECTED_TRADES = 250
BASELINE_EXPECTED_PNL = 225_523
BASELINE_TOLERANCE_PF = 0.02
BASELINE_TOLERANCE_PNL = 100  # 정수 반올림 차이 흡수


# ────────────────────────────────────────────────────────────────────────
# Worker — ProcessPoolExecutor에서 실행되는 함수 (모듈 top-level에 정의 필수)
# ────────────────────────────────────────────────────────────────────────

def _worker_run_combo(args: tuple) -> dict:
    """워커: 한 조합 (threshold, min_profit, min_hold)을 모든 종목에 대해 실행.

    args:
      - (threshold, min_profit, min_hold)
      - candles_bytes: pickle된 dict[str, DataFrame]
      - market_map_bytes: pickle된 dict[str, dict[date, bool]]
      - ticker_to_market: dict[str, str]
      - base_config: TradingConfig (frozen dataclass — pickle 가능)
      - bt_config: BacktestConfig
    """
    (thr, mp_, mh), candles_bytes, market_map_bytes, ticker_to_market, base_config, bt_config = args

    # 워커 측 loguru 억제 (process 격리)
    from loguru import logger as _logger
    _logger.remove()
    _logger.add(sys.stderr, level="WARNING")

    candles_cache = pickle.loads(candles_bytes)
    market_map = pickle.loads(market_map_bytes)

    cfg = dataclasses.replace(
        base_config,
        momentum_fade_threshold=thr,
        momentum_fade_min_profit=mp_,
        momentum_fade_min_hold_min=mh,
    )

    import asyncio as _asyncio
    from backtest.backtester import Backtester as _BT
    from strategy.momentum_strategy import MomentumStrategy as _MS

    all_trades: list[dict] = []
    for tk, candles in candles_cache.items():
        market = ticker_to_market.get(tk, "unknown")
        bt = _BT(
            db=None, config=cfg, backtest_config=bt_config,
            ticker_market=market, market_strong_by_date=market_map,
        )
        strategy = _MS(cfg)
        result = _asyncio.run(bt.run_multi_day_cached(tk, candles, strategy))
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)

    total = len(all_trades)
    if total == 0:
        return {
            "threshold": thr, "min_profit": mp_, "min_hold": mh,
            "pf": 0.0, "total_pnl": 0, "trades": 0,
            "exits": {}, "forced_close_pct": 0.0, "fade_count": 0,
        }
    gp = sum(t["pnl"] for t in all_trades if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in all_trades if t["pnl"] < 0))
    pf = gp / gl if gl > 0 else float("inf")
    pnl = sum(t["pnl"] for t in all_trades)
    exits = Counter(t.get("exit_reason", "?") for t in all_trades)
    return {
        "threshold": thr,
        "min_profit": mp_,
        "min_hold": mh,
        "pf": pf,
        "total_pnl": pnl,
        "trades": total,
        "exits": dict(exits),
        "forced_close_pct": exits.get("forced_close", 0) / total * 100,
        "fade_count": exits.get("momentum_fade", 0),
    }


# ────────────────────────────────────────────────────────────────────────
# Loading
# ────────────────────────────────────────────────────────────────────────

async def load_candles_and_market(start: str, end: str):
    """캔들과 market_map을 한 번만 로드 — 모든 조합에서 재사용."""
    from utils.grid_runner import load_candle_cache
    cache = await load_candle_cache(start, end)
    return (
        cache.base_config,
        cache.bt_config,
        cache.candles,
        cache.ticker_to_market,
        cache.market_map,
    )


# ────────────────────────────────────────────────────────────────────────
# Selection + Report
# ────────────────────────────────────────────────────────────────────────

def select_best(results: list[dict]) -> dict | None:
    """선정 기준 (우선순위):
      1. PF >= 4.1
      2. forced_close <= 40%
      3. total PnL >= 260_000
      4. 위 모두 만족 중 momentum_fade 건수가 가장 적은 것
    """
    qualified = [
        r for r in results
        if r["pf"] >= 4.1
        and r["forced_close_pct"] <= 40.0
        and r["total_pnl"] >= 260_000
    ]
    if not qualified:
        return None
    return min(qualified, key=lambda r: r["fade_count"])


def write_report(results: list[dict], best: dict | None, out_path: Path):
    """reports/momentum_fade_grid.md 작성."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    lines.append("# Momentum Fade Grid Search 결과\n")
    lines.append(f"> 생성: {time.strftime('%Y-%m-%d %H:%M')}\n")
    lines.append("## 선정 기준 (우선순위)\n")
    lines.append("1. PF >= 4.1")
    lines.append("2. forced_close <= 40%")
    lines.append("3. 총 PnL >= 260,000")
    lines.append("4. 위 모두 만족 중 momentum_fade 건수 최소\n")
    lines.append(f"## 전체 {len(results)}개 조합\n")
    lines.append(
        "| threshold | min_profit | min_hold | PF | trades | total PnL | "
        "forced_close% | momentum_fade | 통과 |"
    )
    lines.append("|-----------|-----------|----------|-----|--------|----------|--------------|--------------|------|")
    sorted_r = sorted(results, key=lambda r: r["pf"], reverse=True)
    for r in sorted_r:
        passed = (
            r["pf"] >= 4.1
            and r["forced_close_pct"] <= 40.0
            and r["total_pnl"] >= 260_000
        )
        marker = "PASS" if passed else ""
        lines.append(
            f"| {r['threshold']:+.3f} | {r['min_profit']:.2f} | {r['min_hold']} | "
            f"{r['pf']:.2f} | {r['trades']} | {r['total_pnl']:+,.0f} | "
            f"{r['forced_close_pct']:.1f}% | {r['fade_count']} | {marker} |"
        )
    lines.append("")
    if best is not None:
        lines.append("## 선정 최적 조합\n")
        lines.append(
            f"- threshold: **{best['threshold']:+.3f}**\n"
            f"- min_profit: **{best['min_profit']:.2f}**\n"
            f"- min_hold: **{best['min_hold']}**\n"
            f"- PF: **{best['pf']:.2f}**\n"
            f"- trades: {best['trades']}\n"
            f"- total PnL: {best['total_pnl']:+,.0f}\n"
            f"- forced_close: {best['forced_close_pct']:.1f}%\n"
            f"- momentum_fade: {best['fade_count']}건"
        )
        lines.append("")
        lines.append("### 청산 분포\n")
        for reason, cnt in sorted(best["exits"].items(), key=lambda x: -x[1]):
            pct = cnt / best["trades"] * 100
            lines.append(f"- {reason}: {cnt} ({pct:.1f}%)")
    else:
        lines.append("## 선정 결과: 모든 조합이 기준 미달\n")
        lines.append("기준을 완화하거나 time_decay 등 다른 파라미터 조정 필요.\n")
        lines.append("### PF 상위 5개 (참고용)\n")
        for r in sorted_r[:5]:
            lines.append(
                f"- threshold={r['threshold']:+.3f}, min_profit={r['min_profit']:.2f}, "
                f"min_hold={r['min_hold']} -> PF {r['pf']:.2f}, "
                f"forced_close {r['forced_close_pct']:.1f}%, "
                f"PnL {r['total_pnl']:+,.0f}, fade {r['fade_count']}"
            )
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[REPORT] {out_path} written", flush=True)


# ────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-04-01")
    parser.add_argument("--end", default="2026-04-10")
    parser.add_argument("--verify", action="store_true",
                        help="default 파라미터 1조합만 실행 후 baseline 비교")
    parser.add_argument("--workers", type=int, default=None,
                        help="병렬 워커 수 (기본: min(4, cpu_count-1))")
    args = parser.parse_args()

    print("=" * 70, flush=True)
    if args.verify:
        print(" Baseline Verification (1 combo, default params)", flush=True)
    else:
        n = len(THRESHOLDS) * len(MIN_PROFITS) * len(MIN_HOLDS)
        print(f" Momentum Fade Grid Search "
              f"({len(THRESHOLDS)} x {len(MIN_PROFITS)} x {len(MIN_HOLDS)} = {n} combos)",
              flush=True)
    print(f" Period: {args.start} ~ {args.end}", flush=True)
    print("=" * 70, flush=True)

    base_config, bt_cfg, candles_cache, ticker_market, market_map = (
        await load_candles_and_market(args.start, args.end)
    )

    # 워커에 전달할 직렬화 (한 번만 pickle, 모든 워커가 재사용)
    print("\n[SERIALIZE] pickling candles + market_map for workers...", flush=True)
    candles_bytes = pickle.dumps(candles_cache)
    market_map_bytes = pickle.dumps(market_map)
    print(f"  candles: {len(candles_bytes) / 1024 / 1024:.1f} MB", flush=True)
    print(f"  market_map: {len(market_map_bytes) / 1024:.1f} KB", flush=True)

    # 조합 리스트
    if args.verify:
        combos = [(-0.005, 0.01, 15)]  # 현재 default와 일치 (config.yaml)
    else:
        combos = list(product(THRESHOLDS, MIN_PROFITS, MIN_HOLDS))

    worker_count = args.workers or max(2, min(4, (os.cpu_count() or 4) - 1))
    print(f"\n[GRID] {len(combos)} combinations x {len(candles_cache)} tickers", flush=True)
    print(f"[GRID] workers: {worker_count} (ProcessPoolExecutor, spawn context)", flush=True)

    # 워커 인자 준비
    worker_args = [
        (combo, candles_bytes, market_map_bytes, ticker_market, base_config, bt_cfg)
        for combo in combos
    ]

    results: list[dict] = []
    t_start = time.time()

    # Windows BrokenProcessPool 회피: spawn context 명시
    ctx = mp.get_context("spawn")
    try:
        with ProcessPoolExecutor(max_workers=worker_count, mp_context=ctx) as ex:
            for i, r in enumerate(ex.map(_worker_run_combo, worker_args), 1):
                results.append(r)
                elapsed = time.time() - t_start
                eta = elapsed / i * (len(combos) - i) if i < len(combos) else 0
                print(
                    f"[{i:>2}/{len(combos)}] thr={r['threshold']:+.3f} "
                    f"mp={r['min_profit']:.2f} mh={r['min_hold']} -> "
                    f"PF={r['pf']:.2f} PnL={r['total_pnl']:+,.0f} "
                    f"fc%={r['forced_close_pct']:.1f} fade={r['fade_count']} "
                    f"(elapsed {elapsed:.0f}s, ETA {eta:.0f}s)",
                    flush=True,
                )
    except Exception as e:
        print(f"\n[ERROR] ProcessPoolExecutor 실패: {e}", flush=True)
        print("[FALLBACK] 순차 실행으로 전환", flush=True)
        results = []
        t_start = time.time()
        for i, wargs in enumerate(worker_args, 1):
            r = _worker_run_combo(wargs)
            results.append(r)
            elapsed = time.time() - t_start
            print(
                f"[{i:>2}/{len(combos)}] (seq) PF={r['pf']:.2f} "
                f"PnL={r['total_pnl']:+,.0f} fc%={r['forced_close_pct']:.1f}",
                flush=True,
            )

    # ─── verify 모드 ───
    if args.verify:
        r = results[0]
        print("\n" + "=" * 70, flush=True)
        print(" VERIFY RESULT", flush=True)
        print("=" * 70, flush=True)
        print(f"  PF:           {r['pf']:.2f}    (expected ~{BASELINE_EXPECTED_PF:.2f})", flush=True)
        print(f"  Total trades: {r['trades']}    (expected {BASELINE_EXPECTED_TRADES})", flush=True)
        print(f"  Total PnL:    {r['total_pnl']:+,.0f}    (expected ~{BASELINE_EXPECTED_PNL:+,.0f})", flush=True)
        print(f"  forced_close: {r['forced_close_pct']:.1f}%", flush=True)
        print(f"  fade count:   {r['fade_count']}", flush=True)
        pf_match = abs(r["pf"] - BASELINE_EXPECTED_PF) <= BASELINE_TOLERANCE_PF
        trades_match = r["trades"] == BASELINE_EXPECTED_TRADES
        pnl_match = abs(r["total_pnl"] - BASELINE_EXPECTED_PNL) <= BASELINE_TOLERANCE_PNL
        print(f"\n  PF match:     {'OK' if pf_match else 'FAIL'}", flush=True)
        print(f"  Trades match: {'OK' if trades_match else 'FAIL'}", flush=True)
        print(f"  PnL match:    {'OK' if pnl_match else 'FAIL'}", flush=True)
        if pf_match and trades_match and pnl_match:
            print("\n[VERIFY] PASS - baseline 정확 재현, 그리드 실행 가능", flush=True)
            return 0
        else:
            print("\n[VERIFY] FAIL - baseline 일치 안 함, 그리드 실행 보류 권장", flush=True)
            return 1

    # ─── grid 모드 ───
    best = select_best(results)
    write_report(results, best, Path("reports/momentum_fade_grid.md"))

    if best:
        print("\n" + "=" * 70, flush=True)
        print(" BEST", flush=True)
        print("=" * 70, flush=True)
        print(f"  threshold:  {best['threshold']:+.3f}", flush=True)
        print(f"  min_profit: {best['min_profit']:.2f}", flush=True)
        print(f"  min_hold:   {best['min_hold']}", flush=True)
        print(f"  PF:         {best['pf']:.2f}", flush=True)
        print(f"  PnL:        {best['total_pnl']:+,.0f}", flush=True)
        print(f"  forced_close: {best['forced_close_pct']:.1f}%", flush=True)
        print(f"  fade count: {best['fade_count']}", flush=True)
    else:
        print("\n[WARN] 기준 만족 조합 없음 -- 보고서 PF 상위 참고", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
