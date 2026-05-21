"""scripts/grid_momentum_tight_sl.py — 모멘텀 고정 SL/TP 96조합 그리드 서치.

기존 모멘텀 전략(전일고가 돌파 + 거래량 2.0)의 진입 조건을 그대로 유지하면서
ATR 기반 복잡한 청산 로직 대신 고정 SL/TP 또는 단순 트레일링으로 교체했을 때의
성능을 검증한다.

파라미터 격자:
  sl_pct      : [0.5%, 1.0%, 1.5%, 2.0%]
  tp_pct      : [1.5%, 2.0%, 3.0%, 5.0%]
  trail_mode  : ["off", "trail_1pct", "trail_1.5pct"]
  entry_deadline: ["11:00", "14:00"]

총 조합: 4 × 4 × 3 × 2 = 96

청산 모드:
  off          : 고정 SL + 고정 TP (TP 도달 시 전량 청산, 미달 시 강제청산)
  trail_1pct   : 고정 초기 SL + 고점 대비 1% 트레일링 (TP 없음)
  trail_1.5pct : 고정 초기 SL + 고점 대비 1.5% 트레일링 (TP 없음)

비활성 (이 그리드에서 OFF):
  ATR 비례 손절, Chandelier 트레일링, time_decay, BE3, momentum_fade,
  시장 필터, 장중 필터, blacklist

구간:
  OLD: 2025-04-01 ~ 2026-04-10  (baseline 검증 구간)
  NEW: 2026-04-11 ~ 2026-05-19  (확장 검증 구간)

ATR 모드 baseline (현행):
  OLD PF=4.881 / PnL=+295,690 / 거래=228

선정 기준 (OLD 구간 기준):
  PF >= 1.5  AND  거래수 >= 30  AND  연속 손실 <= 8
  + NEW 구간 PF > 1.0

사용:
    python -u scripts/grid_momentum_tight_sl.py           # 전체 실행
    python -u scripts/grid_momentum_tight_sl.py --verify  # 단일 파라미터 검증만
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
NEW_END   = "2026-05-19"

# ATR 모드 baseline (현행 참고값)
ATR_BASELINE = {
    "pf": 4.881, "pnl": 295_690, "trades": 228,
    "win_rate": 0.557, "fc_pct": 40.4,
}

# ---------------------------------------------------------------------------
# 그리드 파라미터
# ---------------------------------------------------------------------------

SL_PCTS      = [0.005, 0.010, 0.015, 0.020]   # 0.5%, 1%, 1.5%, 2%
TP_PCTS      = [0.015, 0.020, 0.030, 0.050]   # 1.5%, 2%, 3%, 5%
TRAIL_MODES  = ["off", "trail_1pct", "trail_1.5pct"]
ENTRY_ENDS   = ["11:00", "14:00"]

# ---------------------------------------------------------------------------
# 선정 기준
# ---------------------------------------------------------------------------

MIN_PF          = 1.5
MIN_TRADES      = 30
MAX_CONSEC_LOSS = 8
MIN_NEW_PF      = 1.0

# ---------------------------------------------------------------------------
# KPI 계산
# ---------------------------------------------------------------------------

def _compute_stats(trades: list[dict]) -> dict:
    """거래 목록 → KPI dict.

    키 네이밍: sl_pct / tp_pct는 params_dict 키와 충돌하므로
    청산 비율은 sl_exit_pct / tp_exit_pct / trail_exit_pct / fc_pct 로 구분.
    """
    n = len(trades)
    if n == 0:
        return {
            "pf": 0.0, "pnl": 0, "trades": 0,
            "win_rate": 0.0, "avg_win": 0, "avg_loss": 0,
            "tp_cnt": 0, "sl_cnt": 0, "trail_cnt": 0, "fc_cnt": 0,
            "fc_pct": 0.0, "tp_exit_pct": 0.0, "sl_exit_pct": 0.0,
            "trail_exit_pct": 0.0,
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

    tp_cnt    = exits.get("tp_exit",      0)
    sl_cnt    = exits.get("stop_loss",    0)
    trail_cnt = exits.get("trailing_stop", 0)
    fc_cnt    = exits.get("forced_close", 0)

    return {
        "pf":              round(gp / gl, 4) if gl > 0 else float("inf"),
        "pnl":             int(pnl),
        "trades":          n,
        "win_rate":        round(len(wins) / n, 4),
        "avg_win":         int(sum(wins) / len(wins)) if wins else 0,
        "avg_loss":        int(sum(loss) / len(loss)) if loss else 0,
        "tp_cnt":          tp_cnt,
        "sl_cnt":          sl_cnt,
        "trail_cnt":       trail_cnt,
        "fc_cnt":          fc_cnt,
        "fc_pct":          round(fc_cnt    / n * 100, 1),
        "tp_exit_pct":     round(tp_cnt   / n * 100, 1),
        "sl_exit_pct":     round(sl_cnt   / n * 100, 1),
        "trail_exit_pct":  round(trail_cnt / n * 100, 1),
        "max_consec_loss": max_cl,
        "avg_hold_min":    round(avg_hold, 1),
        "exit_counts":     dict(exits),
    }


# ---------------------------------------------------------------------------
# config factory
# ---------------------------------------------------------------------------

def _tight_sl_config_factory(params: dict, base_config: object) -> object:
    """params → TradingConfig (ATR/BE3/fade 비활성, 고정 SL/TP 활성)."""
    return dataclasses.replace(
        base_config,
        # 고정 SL/TP 파라미터
        tight_sl_pct=params["sl_pct"],
        tight_tp_pct=params["tp_pct"],
        tight_trail_mode=params["trail_mode"],
        # 진입 시간 제한
        buy_time_limit_enabled=True,
        buy_time_end=params["entry_deadline"],
        # 복잡한 청산 로직 비활성 (MomentumTightSLFastBacktester가 직접 처리)
        atr_stop_enabled=False,
        atr_trail_enabled=False,
        breakeven_enabled=False,
        time_decay_trailing_enabled=False,
        momentum_fade_exit_enabled=False,
        stale_position_exit_enabled=False,
        # 포트폴리오 레벨 필터 비활성 (단일 전략 성능 측정)
        market_filter_enabled=False,
        intraday_market_filter_enabled=False,
        blacklist_enabled=False,
        consecutive_loss_rest_enabled=False,
        volatility_sizing_enabled=False,
        # 1일 1거래 (종목별)
        max_trades_per_day=1,
        cooldown_minutes=0,
        # 진입 추격 상한 완화 (진입 조건 간소화)
        max_entry_above_close_pct=999.0,
    )


# ---------------------------------------------------------------------------
# 조합 빌더
# ---------------------------------------------------------------------------

def _build_combos() -> list[dict]:
    combos = []
    for sl in SL_PCTS:
        for tp in TP_PCTS:
            for trail in TRAIL_MODES:
                for ee in ENTRY_ENDS:
                    # trail_mode 레이블
                    trail_label = {
                        "off":           "off",
                        "trail_1pct":    "tr1",
                        "trail_1.5pct":  "tr15",
                    }.get(trail, trail)
                    tag = (
                        f"sl{int(sl*1000):02d}_tp{int(tp*1000):03d}"
                        f"_{trail_label}"
                        f"_ee{ee.replace(':', '')}"
                    )
                    combos.append({
                        "tag":            tag,
                        "sl_pct":         sl,
                        "tp_pct":         tp,
                        "trail_mode":     trail,
                        "entry_deadline": ee,
                    })
    return combos


# ---------------------------------------------------------------------------
# 워커
# ---------------------------------------------------------------------------

def _tight_sl_worker(args: tuple) -> dict:
    """고정 SL/TP 단일 조합 백테스트 — subprocess 실행용."""
    config, candles_bytes, market_map_bytes, ticker_to_market, bt_config, params_dict = args

    import sys as _sys, pickle as _p, asyncio as _a
    from loguru import logger as _l
    _l.remove()
    _l.add(_sys.stderr, level="WARNING")

    try:
        from backtest.backtester_fast import MomentumTightSLFastBacktester as _MTSBT
        from strategy.momentum_strategy import MomentumStrategy as _MS

        candles_cache: dict = _p.loads(candles_bytes)
        market_map: dict    = _p.loads(market_map_bytes)

        all_trades: list[dict] = []
        for tk, df in candles_cache.items():
            market = ticker_to_market.get(tk, "unknown")
            bt = _MTSBT(
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
# 병렬 그리드 실행
# ---------------------------------------------------------------------------

def _run_grid(
    combos: list[dict], cache: GridCache, *, max_workers: int | None = None
) -> list[dict]:
    cache.prepare_bytes()
    n_workers = max_workers or max(2, min(4, (os.cpu_count() or 4) - 1))

    worker_args = [
        (
            _tight_sl_config_factory(p, cache.base_config),
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

    try:
        from tqdm import tqdm as _tqdm
        _use_tqdm = True
    except ImportError:
        _use_tqdm = False

    ctx = mp.get_context("spawn")
    try:
        from concurrent.futures import ProcessPoolExecutor as _PPE
        with _PPE(max_workers=n_workers, mp_context=ctx) as ex:
            it = ex.map(_tight_sl_worker, worker_args)
            if _use_tqdm:
                it = _tqdm(it, total=n, desc="tight_sl grid", unit="combo")
            for i, r in enumerate(it, 1):
                results.append(r)
                if not _use_tqdm:
                    elapsed = _time.time() - t0
                    eta = elapsed / i * (n - i) if i < n else 0
                    ok = _is_passing(r)
                    print(
                        f"  [{i:>3}/{n}] {r.get('tag',''):<38} "
                        f"pf={r.get('pf', 0):.3f} tr={r.get('trades', 0):>4} "
                        f"tp%={r.get('tp_exit_pct', 0):>4.1f} sl%={r.get('sl_exit_pct', 0):>4.1f} "
                        f"fc%={r.get('fc_pct', 0):>4.1f} cl={r.get('max_consec_loss', 0):>2} "
                        f"{'OK' if ok else '  '} (ETA {eta:.0f}s)",
                        flush=True,
                    )
    except Exception as exc:
        print(f"[WARN] Pool 실패 ({exc}), 순차 실행 전환", flush=True)
        results = []
        for i, wargs in enumerate(worker_args, 1):
            r = _tight_sl_worker(wargs)
            results.append(r)
            if not _use_tqdm:
                elapsed = _time.time() - t0
                print(
                    f"  [{i:>3}/{n}] {r.get('tag',''):<38} "
                    f"pf={r.get('pf', 0):.3f} ({elapsed:.0f}s)",
                    flush=True,
                )

    elapsed = _time.time() - t0
    print(f"[DONE] {n}조합 완료 ({elapsed:.1f}s)", flush=True)
    return results


# ---------------------------------------------------------------------------
# 선정 기준
# ---------------------------------------------------------------------------

def _is_passing(r: dict, *, new_pf: float | None = None) -> bool:
    ok = (
        r.get("pf", 0.0) >= MIN_PF
        and int(r.get("trades", 0)) >= MIN_TRADES
        and int(r.get("max_consec_loss", 999)) <= MAX_CONSEC_LOSS
    )
    if ok and new_pf is not None:
        ok = new_pf >= MIN_NEW_PF
    return ok


def _select_best(
    old_results: list[dict],
    new_map: dict[str, dict],
) -> list[dict]:
    passing = []
    for r in old_results:
        tag = r.get("tag", "")
        nr = new_map.get(tag)
        new_pf = nr.get("pf", 0.0) if nr else None
        if _is_passing(r, new_pf=new_pf):
            passing.append({**r, "new_pf": new_pf or 0.0, "new_pnl": nr.get("pnl", 0) if nr else 0})
    return sorted(passing, key=lambda x: x.get("pf", 0.0), reverse=True)


# ---------------------------------------------------------------------------
# 콘솔 출력
# ---------------------------------------------------------------------------

def _print_table(results: list[dict], title: str, *, top: int = 20) -> None:
    print(f"\n{title}", flush=True)
    hdr = (
        f"{'태그':>38} | "
        f"{'PF':>6} {'PnL':>10} {'거래#':>5} {'승률':>6} "
        f"{'TP%':>5} {'SL%':>5} {'FC%':>5} {'CL':>3} {'보유':>5} {'OK':>3}"
    )
    sep = "-" * len(hdr)
    print(sep, flush=True)
    print(hdr, flush=True)
    print(sep, flush=True)
    for r in sorted(results, key=lambda x: x.get("pf", 0.0), reverse=True)[:top]:
        ok = "Y" if _is_passing(r) else ""
        print(
            f"{r.get('tag', ''):<38} | "
            f"{r.get('pf', 0):>6.3f} {int(r.get('pnl', 0)):>+10,} "
            f"{int(r.get('trades', 0)):>5} {r.get('win_rate', 0):>6.1%} "
            f"{r.get('tp_exit_pct', 0):>5.1f} "
            f"{r.get('sl_exit_pct', 0):>5.1f} "
            f"{r.get('fc_pct', 0):>5.1f} "
            f"{r.get('max_consec_loss', 0):>3} {r.get('avg_hold_min', 0):>5.1f} "
            f"{ok:>3}",
            flush=True,
        )
    print(sep, flush=True)


# ---------------------------------------------------------------------------
# 보고서 생성
# ---------------------------------------------------------------------------

def _write_report(
    old_results: list[dict],
    new_results: list[dict],
    passing: list[dict],
    old_elapsed: float,
    new_elapsed: float,
) -> None:
    out = Path("reports/momentum_tight_sl_grid_result.md")
    out.parent.mkdir(exist_ok=True)

    def _md_table(results: list[dict]) -> list[str]:
        header = (
            "| 태그 | sl% | tp% | trail | deadline | PF | PnL | 거래# | 승률 | "
            "TP청산% | SL청산% | Trail청산% | FC% | CL | 보유(분) |"
        )
        sep = "| --- " * 15 + "|"
        lines = [header, sep]
        for r in sorted(results, key=lambda x: x.get("pf", 0.0), reverse=True):
            ok = " ✓" if _is_passing(r) else ""
            lines.append(
                f"| {r.get('tag','')} "
                f"| {r.get('sl_pct',0):.1%} "
                f"| {r.get('tp_pct',0):.1%} "
                f"| {r.get('trail_mode','')} "
                f"| {r.get('entry_deadline','')} "
                f"| {r.get('pf',0):.4f} "
                f"| {int(r.get('pnl',0)):+,} "
                f"| {r.get('trades',0)} "
                f"| {r.get('win_rate',0):.1%} "
                f"| {r.get('tp_exit_pct',0):.1f}% "
                f"| {r.get('sl_exit_pct',0):.1f}% "
                f"| {r.get('trail_exit_pct',0):.1f}% "
                f"| {r.get('fc_pct',0):.1f}% "
                f"| {r.get('max_consec_loss',0)} "
                f"| {r.get('avg_hold_min',0):.1f}{ok} |"
            )
        return lines

    lines: list[str] = [
        "# 모멘텀 고정 SL/TP 그리드 서치",
        f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> OLD 구간: {OLD_START} ~ {OLD_END}  ({old_elapsed:.0f}s)",
        f"> NEW 구간: {NEW_START} ~ {NEW_END}  ({new_elapsed:.0f}s)",
        f"> 선정 기준: PF≥{MIN_PF}  AND  거래≥{MIN_TRADES}  "
        f"AND  연속손실≤{MAX_CONSEC_LOSS}  AND  NEW PF>{MIN_NEW_PF}",
        "",
        "## ATR 모드 baseline (현행, 비교 참고)",
        "",
        "| 구분 | PF | PnL | 거래# | 승률 | FC% |",
        "| --- | --- | --- | --- | --- | --- |",
        f"| ATR 모드 (OLD) | {ATR_BASELINE['pf']:.3f} | "
        f"+{ATR_BASELINE['pnl']:,} | {ATR_BASELINE['trades']} | "
        f"{ATR_BASELINE['win_rate']:.1%} | {ATR_BASELINE['fc_pct']:.1f}% |",
        "",
        "**비고**: ATR 모드는 atr_stop(2.0×), Chandelier trail(min2.5%/max8%), "
        "time_decay, BE3, momentum_fade 모두 활성. 고정 SL/TP 모드와 직접 비교 주의.",
        "",
        f"## 그리드 결과 — OLD ({len(old_results)}조합)",
        "",
    ] + _md_table(old_results) + [""]

    if new_results:
        lines += [
            f"## 그리드 결과 — NEW ({len(new_results)}조합)",
            "",
        ] + _md_table(new_results) + [""]

    lines += [
        "## 선정 기준 통과 조합 (OLD 기준, NEW PF>1.0 교차 검증)",
        "",
    ]

    if passing:
        lines += [
            "| 태그 | sl% | tp% | trail | deadline | PF(OLD) | PnL(OLD) | 거래# | 승률 | "
            "TP청산% | FC% | CL | 보유(분) | PF(NEW) | PnL(NEW) |",
            "| --- " * 15 + "|",
        ]
        for r in passing[:10]:
            nr_pnl = int(r.get("new_pnl", 0))
            lines.append(
                f"| {r.get('tag','')} "
                f"| {r.get('sl_pct',0):.1%} "
                f"| {r.get('tp_pct',0):.1%} "
                f"| {r.get('trail_mode','')} "
                f"| {r.get('entry_deadline','')} "
                f"| {r.get('pf',0):.3f} "
                f"| {int(r.get('pnl',0)):+,} "
                f"| {r.get('trades',0)} "
                f"| {r.get('win_rate',0):.1%} "
                f"| {r.get('tp_exit_pct',0):.1f}% "
                f"| {r.get('fc_pct',0):.1f}% "
                f"| {r.get('max_consec_loss',0)} "
                f"| {r.get('avg_hold_min',0):.1f} "
                f"| {r.get('new_pf',0):.3f} "
                f"| {nr_pnl:+,} |"
            )
    else:
        lines += [
            f"선정 기준 미달 — 전 {len(old_results)}조합 비활성.",
            "",
            "**해석**: 고정 SL/TP 방식은 모멘텀 전략의 ATR 기반 청산보다 열세. "
            "`tight_sl.enabled: false` 유지.",
        ]

    lines += [
        "",
        "## 그리드 파라미터 요약",
        "",
        f"- sl_pct: {[f'{v:.1%}' for v in SL_PCTS]}",
        f"- tp_pct: {[f'{v:.1%}' for v in TP_PCTS]}",
        f"- trail_mode: {TRAIL_MODES}",
        f"- entry_deadline: {ENTRY_ENDS}",
        f"- 총 조합: {len(SL_PCTS)} × {len(TP_PCTS)} × {len(TRAIL_MODES)} "
        f"× {len(ENTRY_ENDS)} = {len(SL_PCTS)*len(TP_PCTS)*len(TRAIL_MODES)*len(ENTRY_ENDS)}",
    ]

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[SAVED] {out}", flush=True)


# ---------------------------------------------------------------------------
# --verify 단일 백테스트
# ---------------------------------------------------------------------------

async def _run_verify(cache: GridCache) -> None:
    from backtest.backtester_fast import MomentumTightSLFastBacktester as _MTSBT
    from strategy.momentum_strategy import MomentumStrategy as _MS

    params = {
        "sl_pct": 0.010, "tp_pct": 0.020,
        "trail_mode": "off", "entry_deadline": "12:00",
    }
    cfg = _tight_sl_config_factory(params, cache.base_config)

    all_trades: list[dict] = []
    for tk, df in cache.candles.items():
        market = cache.ticker_to_market.get(tk, "unknown")
        bt = _MTSBT(
            db=None, config=cfg, backtest_config=cache.bt_config,
            ticker_market=market, market_strong_by_date=cache.market_map,
        )
        strat = _MS(cfg)
        result = await bt.run_multi_day_cached(tk, df, strat)
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)

    stats = _compute_stats(all_trades)
    print("\n[VERIFY] 고정 SL/TP 단일 백테스트 결과")
    print(f"  (sl=1% / tp=2% / trail=off / entry_deadline=12:00)")
    print(
        f"  PF={stats['pf']:.3f}  PnL={stats['pnl']:+,}  "
        f"거래#{stats['trades']}  승률={stats['win_rate']:.1%}  "
        f"연속손실={stats['max_consec_loss']}  평균보유={stats['avg_hold_min']:.1f}분"
    )
    print(f"  청산분포: {stats['exit_counts']}")
    print(f"  TP청산={stats['tp_exit_pct']:.1f}%  SL청산={stats['sl_exit_pct']:.1f}%  FC={stats['fc_pct']:.1f}%")
    print(f"  평균수익={stats['avg_win']:+,}원  평균손실={stats['avg_loss']:+,}원")
    print(f"\n  ATR baseline: PF={ATR_BASELINE['pf']:.3f} / PnL=+{ATR_BASELINE['pnl']:,}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="모멘텀 고정 SL/TP 96조합 그리드")
    parser.add_argument("--verify", action="store_true", help="단일 파라미터 검증만")
    args = parser.parse_args()

    print("캔들 캐시 로드 중...", flush=True)
    cache_all = asyncio.run(load_candle_cache(OLD_START, NEW_END))
    print(f"  {len(cache_all.candles)}종목 로드 완료", flush=True)

    old_cache = cache_all.filter_dates(OLD_START, OLD_END)
    new_cache = cache_all.filter_dates(NEW_START, NEW_END)

    if args.verify:
        print("\n[VERIFY] OLD 구간 단일 파라미터 테스트...", flush=True)
        asyncio.run(_run_verify(old_cache))
        print("\n[VERIFY 완료]", flush=True)
        return

    combos = _build_combos()
    total = len(SL_PCTS) * len(TP_PCTS) * len(TRAIL_MODES) * len(ENTRY_ENDS)
    print(
        f"\n총 {len(combos)}조합 "
        f"(sl×tp×trail×deadline = "
        f"{len(SL_PCTS)}×{len(TP_PCTS)}×{len(TRAIL_MODES)}×{len(ENTRY_ENDS)} = {total})",
        flush=True,
    )
    print(f"  ATR baseline: PF={ATR_BASELINE['pf']:.3f} / PnL=+{ATR_BASELINE['pnl']:,}", flush=True)

    # ── OLD 구간 ──────────────────────────────────────────────────────────────
    print(f"\n[GRID OLD] {OLD_START} ~ {OLD_END}", flush=True)
    t0_old = _time.time()
    old_results = _run_grid(combos, old_cache)
    old_elapsed = _time.time() - t0_old
    _print_table(old_results, f"고정 SL/TP 그리드 (OLD, {len(old_results)}조합)")

    # ── NEW 구간 ──────────────────────────────────────────────────────────────
    print(f"\n[GRID NEW] {NEW_START} ~ {NEW_END}", flush=True)
    t0_new = _time.time()
    if new_cache.candles:
        new_results = _run_grid(combos, new_cache)
        new_elapsed = _time.time() - t0_new
        _print_table(new_results, f"고정 SL/TP 그리드 (NEW, {len(new_results)}조합)")
    else:
        new_results = []
        new_elapsed = 0.0
        print("  NEW 캔들 없음 — 생략", flush=True)

    # ── 교차 검증 선정 ──────────────────────────────────────────────────────
    new_map = {r["tag"]: r for r in new_results}
    passing = _select_best(old_results, new_map)

    print(f"\n[선정] 기준 통과 조합: {len(passing)}개", flush=True)
    for r in passing[:10]:
        print(
            f"  {r['tag']}  OLD PF={r['pf']:.3f}  NEW PF={r.get('new_pf',0):.3f}  "
            f"거래#{r['trades']}  CL={r['max_consec_loss']}  "
            f"TP청산={r.get('tp_exit_pct',0):.1f}%  FC={r.get('fc_pct',0):.1f}%  "
            f"보유={r.get('avg_hold_min',0):.1f}분",
            flush=True,
        )
    if not passing:
        print(
            f"  선정 기준 미달 (PF≥{MIN_PF} / 거래≥{MIN_TRADES} / "
            f"CL≤{MAX_CONSEC_LOSS} / NEW PF>{MIN_NEW_PF})",
            flush=True,
        )

    print(f"\n  [참고] ATR baseline: PF={ATR_BASELINE['pf']:.3f} / PnL=+{ATR_BASELINE['pnl']:,}", flush=True)

    _write_report(old_results, new_results, passing, old_elapsed, new_elapsed)


if __name__ == "__main__":
    main()
