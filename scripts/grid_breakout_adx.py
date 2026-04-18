"""scripts/grid_breakout_adx.py — 돌파 하한 × ADX 상한 5×4 그리드.

- min_breakout_pct: 0.03 / 0.04 / 0.05 / 0.06 / 0.08
- adx_max (상한): None(현재) / 50 / 40 / 35

각 조합에서 유니버스 41종목 × multi-day 백테스트 실행 후 PF/거래수/약세 PF 비교.
baseline (bp=0.03, adx_max=None) 대비 개선폭을 기준으로 sweet spot 권장.

사용:
    python scripts/grid_breakout_adx.py
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pickle
import sqlite3
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace as dc_replace
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig
from data.db_manager import DbManager
from strategy.momentum_strategy import MomentumStrategy

REPORT_PATH = Path("reports/breakout_adx_grid.md")
DB_PATH = "daytrader.db"

BREAKOUT_GRID = [0.03, 0.04, 0.05, 0.06, 0.08]
ADX_MAX_GRID: list[float | None] = [None, 50.0, 40.0, 35.0]


# ======================================================================
# ADX 상한까지 적용하는 MomentumStrategy 서브클래스
# ======================================================================

class AdxMaxMomentumStrategy(MomentumStrategy):
    """MomentumStrategy + ADX 상한. adx_max=None이면 부모와 동일."""

    def __init__(self, config, adx_max: float | None = None) -> None:
        super().__init__(config)
        self._adx_max = adx_max

    def _check_adx(self, candles):
        import pandas_ta as ta

        min_candles = self._config.adx_length + 20
        if len(candles) < min_candles:
            return False
        try:
            df = candles.tail(min_candles)
            adx_result = ta.adx(df["high"], df["low"], df["close"],
                                length=self._config.adx_length)
            if adx_result is None or adx_result.empty:
                return False
            adx_col = f"ADX_{self._config.adx_length}"
            if adx_col not in adx_result.columns:
                return False
            current_adx = adx_result[adx_col].iloc[-1]
            if pd.isna(current_adx):
                return False
            if current_adx < self._config.adx_min:
                return False
            if self._adx_max is not None and current_adx > self._adx_max:
                return False
            return True
        except Exception:
            return False


# ======================================================================
# ProcessPool worker
# ======================================================================

def _simulate_one(args: tuple) -> dict:
    (ticker, ticker_market, candles_pickle, trading_config, backtest_config,
     market_map, adx_max) = args
    import asyncio as _asyncio

    from backtest.backtester import Backtester as _Bt

    candles = pickle.loads(candles_pickle)
    strategy = AdxMaxMomentumStrategy(trading_config, adx_max=adx_max)
    bt = _Bt(
        db=None, config=trading_config, backtest_config=backtest_config,
        ticker_market=ticker_market, market_strong_by_date=market_map,
    )
    result = _asyncio.run(bt.run_multi_day_cached(ticker, candles, strategy))
    for t in result.get("trades", []):
        t["ticker"] = ticker
        t["ticker_market"] = ticker_market
    return result


# ======================================================================
# 지표 계산
# ======================================================================

def compute_pf(trades: list[dict]) -> float:
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    if gl == 0:
        return float("inf") if gp > 0 else 0.0
    return gp / gl


def load_bear_months(db_path: str, trades: list[dict]) -> set[str]:
    """월별 KOSPI+KOSDAQ 평균 수익률 ≤ -5%인 월. 거래 대상 월만 계산."""
    if not trades:
        return set()
    conn = sqlite3.connect(db_path)
    idx_data: dict[str, dict[str, float]] = {}
    for code, name in (("001", "kospi"), ("101", "kosdaq")):
        cur = conn.execute(
            "SELECT dt, close FROM index_candles WHERE index_code=? ORDER BY dt",
            (code,),
        )
        idx_data[name] = {r[0]: r[1] for r in cur.fetchall()}
    conn.close()

    months = {pd.to_datetime(t["entry_ts"]).strftime("%Y-%m") for t in trades}

    def month_ret(data: dict[str, float], ym: str) -> float | None:
        key = ym.replace("-", "")
        sorted_dates = sorted(d for d in data if d[:6] == key)
        if len(sorted_dates) < 2:
            return None
        first = data[sorted_dates[0]]
        last = data[sorted_dates[-1]]
        return (last - first) / first if first > 0 else None

    bear = set()
    for ym in months:
        k = month_ret(idx_data["kospi"], ym)
        q = month_ret(idx_data["kosdaq"], ym)
        if k is None or q is None:
            continue
        if (k + q) / 2 <= -0.05:
            bear.add(ym)
    return bear


def filter_bear(trades: list[dict], bear_months: set[str]) -> list[dict]:
    return [
        t for t in trades
        if pd.to_datetime(t["entry_ts"]).strftime("%Y-%m") in bear_months
    ]


# ======================================================================
# 그리드 실행
# ======================================================================

async def run_grid(args) -> list[dict]:
    app_config = AppConfig.from_yaml()
    base_config = app_config.trading
    bt_cfg = yaml.safe_load(open("config.yaml", encoding="utf-8")).get("backtest", {})
    backtest_config = BacktestConfig(
        commission=bt_cfg.get("commission", 0.00015),
        tax=bt_cfg.get("tax", 0.0015),
        slippage=bt_cfg.get("slippage", 0.0003),
    )
    uni = yaml.safe_load(open("config/universe.yaml", encoding="utf-8"))
    stocks = uni.get("stocks", [])
    ticker_to_market = {s["ticker"]: s.get("market", "unknown") for s in stocks}

    # 캔들 1회 로드
    print(f"[LOAD] 캔들 로딩 ({len(stocks)}종목, {args.start}~{args.end})...")
    db = DbManager(app_config.db_path)
    await db.init()
    bt_loader = Backtester(db=db, config=base_config, backtest_config=backtest_config)
    candles_cache: dict[str, bytes] = {}
    for s in stocks:
        tk = s["ticker"]
        candles = await bt_loader.load_candles(tk, args.start, f"{args.end} 23:59:59")
        if not candles.empty:
            candles_cache[tk] = pickle.dumps(candles)
    print(f"  로드 {len(candles_cache)}/{len(stocks)}")
    await db.close()

    market_map = build_market_strong_by_date(
        app_config.db_path, ma_length=base_config.market_ma_length
    )
    workers = max(2, (os.cpu_count() or 2) - 1)

    # 20 조합 실행
    total = len(BREAKOUT_GRID) * len(ADX_MAX_GRID)
    results: list[dict] = []
    idx = 0
    for bp in BREAKOUT_GRID:
        # TradingConfig는 frozen — replace로 새 인스턴스
        trading_config = dc_replace(base_config, min_breakout_pct=bp)
        for adx_max in ADX_MAX_GRID:
            idx += 1
            adx_label = str(int(adx_max)) if adx_max is not None else "none"
            print(f"\n[{idx:2d}/{total}] bp={bp:.2f} adx_max={adx_label}")
            tasks = [
                (tk, ticker_to_market.get(tk, "unknown"), candles_cache[tk],
                 trading_config, backtest_config, market_map, adx_max)
                for tk in candles_cache
            ]
            with ProcessPoolExecutor(max_workers=workers) as executor:
                kpis = list(executor.map(_simulate_one, tasks))
            trades: list[dict] = []
            for kpi in kpis:
                if kpi:
                    trades.extend(kpi.get("trades", []))

            pf = compute_pf(trades)
            total_pnl = sum(t["pnl"] for t in trades)
            bear_months = load_bear_months(app_config.db_path, trades)
            bear = filter_bear(trades, bear_months)
            bear_pf = compute_pf(bear)
            exit_dist = dict(Counter(t.get("exit_reason", "?") for t in trades))

            rec = {
                "bp": bp,
                "adx_max": adx_max,
                "trades": len(trades),
                "pf": pf,
                "total_pnl": total_pnl,
                "bear_trades": len(bear),
                "bear_pf": bear_pf,
                "bear_months": sorted(bear_months),
                "exit_dist": exit_dist,
            }
            results.append(rec)
            print(f"  trades={len(trades)} PF={pf:.2f} "
                  f"bear {len(bear)}건/{len(bear_months)}월 PF={bear_pf:.2f}")
    return results


# ======================================================================
# 리포트
# ======================================================================

def fmt_pf(x: float) -> str:
    if x == float("inf"):
        return "∞"
    return f"{x:.2f}"


def generate_report(results: list[dict]) -> None:
    baseline = next(
        (r for r in results if abs(r["bp"] - 0.03) < 1e-9 and r["adx_max"] is None),
        None,
    )
    if baseline is None:
        raise RuntimeError("baseline 조합 (bp=0.03, adx_max=None) 결과 없음")
    b_trades = baseline["trades"]
    b_pf = baseline["pf"]
    b_bear_pf = baseline["bear_pf"]
    b_pnl = baseline["total_pnl"]

    lines: list[str] = []

    def a(s: str = "") -> None:
        lines.append(s)

    a("# 돌파 하한 × ADX 상한 5×4 그리드")
    a()
    a(f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    a(f"> baseline (bp=0.03, adx_max=없음): trades **{b_trades}**, "
      f"PF **{fmt_pf(b_pf)}**, bear PF **{fmt_pf(b_bear_pf)}** "
      f"({baseline['bear_trades']}건/{len(baseline['bear_months'])}월)")
    a(f"> 그리드: bp {BREAKOUT_GRID} × adx_max {[str(x) if x else 'none' for x in ADX_MAX_GRID]}")
    a(f"> 통계 기준: 거래수 **< 200건은 통계 부족** 표시")
    a()
    a("---")
    a()

    # ── 1. 전체 20 조합 ──
    a("## 1. 전체 20 조합 결과")
    a()
    a("| # | min_bp | adx_max | trades | PF | ΔPF | PnL | ΔPnL | bear 건수 | bear_PF | ΔbearPF | exit 분포 | 판정 |")
    a("|---|--------|---------|--------|-----|-----|------|------|-----------|---------|---------|-----------|------|")
    for i, r in enumerate(results, 1):
        adx_s = str(int(r["adx_max"])) if r["adx_max"] is not None else "없음"
        pf_s = fmt_pf(r["pf"])
        bear_s = fmt_pf(r["bear_pf"])
        dpf = r["pf"] - b_pf if r["pf"] != float("inf") and b_pf != float("inf") else 0
        dbear = r["bear_pf"] - b_bear_pf if r["bear_pf"] != float("inf") and b_bear_pf != float("inf") else 0
        dpnl = r["total_pnl"] - b_pnl
        stat = "부족" if r["trades"] < 200 else "OK"
        is_baseline = abs(r["bp"] - 0.03) < 1e-9 and r["adx_max"] is None
        note = " (baseline)" if is_baseline else ""
        # exit dist 압축
        ed = r["exit_dist"]
        exit_str = f"fc{ed.get('forced_close', 0)}/be{ed.get('breakeven_stop', 0)}/sl{ed.get('stop_loss', 0)}/tr{ed.get('trailing_stop', 0)}"
        a(
            f"| {i} | {r['bp']:.2f} | {adx_s} | {r['trades']} | {pf_s} | {dpf:+.2f} | "
            f"{r['total_pnl']:+,.0f} | {dpnl:+,.0f} | {r['bear_trades']} | {bear_s} | "
            f"{dbear:+.2f} | {exit_str} | {stat}{note} |"
        )
    a()

    # ── 2. PF 히트맵 ──
    def heatmap(title: str, value_key: str, fmt_fn=fmt_pf, with_n: bool = True) -> None:
        a(f"## {title}")
        a()
        header = "| min_bp \\ adx_max | " + " | ".join(
            "없음" if x is None else str(int(x)) for x in ADX_MAX_GRID
        ) + " |"
        a(header)
        a("|" + "---|" * (len(ADX_MAX_GRID) + 1))
        for bp in BREAKOUT_GRID:
            row = [f"**{bp:.2f}**"]
            for am in ADX_MAX_GRID:
                r = next(
                    (x for x in results
                     if abs(x["bp"] - bp) < 1e-9 and x["adx_max"] == am),
                    None,
                )
                if r is None:
                    row.append("—")
                    continue
                v = r[value_key]
                v_s = fmt_fn(v) if callable(fmt_fn) else str(v)
                if with_n:
                    n_s = r["trades"] if value_key != "bear_pf" else r["bear_trades"]
                    stat_weak = r["trades"] < 200
                    cell = f"_{v_s}_ (n={n_s})" if stat_weak else f"**{v_s}** (n={n_s})"
                else:
                    cell = v_s
                row.append(cell)
            a("| " + " | ".join(row) + " |")
        a("> *italic* = 거래수 < 200 (통계 부족)")
        a()

    heatmap("2. PF 히트맵", "pf")
    heatmap("3. 약세장 PF 히트맵", "bear_pf")

    # ── 4. 거래수 변화 ──
    a("## 4. 거래수 히트맵")
    a()
    a("| min_bp \\ adx_max | " + " | ".join(
        "없음" if x is None else str(int(x)) for x in ADX_MAX_GRID
    ) + " |")
    a("|" + "---|" * (len(ADX_MAX_GRID) + 1))
    for bp in BREAKOUT_GRID:
        row = [f"**{bp:.2f}**"]
        for am in ADX_MAX_GRID:
            r = next(
                (x for x in results
                 if abs(x["bp"] - bp) < 1e-9 and x["adx_max"] == am),
                None,
            )
            if r is None:
                row.append("—")
                continue
            dtrades = r["trades"] - b_trades
            cell = f"{r['trades']} ({dtrades:+d})"
            if r["trades"] < 200:
                cell = f"_{cell}_"
            row.append(cell)
        a("| " + " | ".join(row) + " |")
    a()
    a("> 괄호 안: baseline(248) 대비 증감")
    a()

    # ── 5. sweet spot 후보 ──
    a("## 5. sweet spot 후보")
    a()
    valid = [r for r in results if r["trades"] >= 200]
    weak_all = [r for r in results if r["trades"] < 200]
    a(f"- 통계 충분 조합 (거래 ≥ 200): **{len(valid)}/{len(results)}**")
    a(f"- 통계 부족 조합: {len(weak_all)}")
    a()

    if not valid:
        a("통계 충분 조합 없음 — 모든 필터 강화가 거래수를 크게 줄임. "
          "현재 baseline 유지 권장.")
    else:
        # PF Top 5
        by_pf = sorted(valid, key=lambda x: x["pf"], reverse=True)[:5]
        a("### 5-1. PF 상위 5 (trades ≥ 200)")
        a()
        a("| # | min_bp | adx_max | trades | PF | ΔPF | bear_PF | ΔbearPF | PnL |")
        a("|---|--------|---------|--------|-----|-----|---------|---------|------|")
        for i, r in enumerate(by_pf, 1):
            adx_s = str(int(r["adx_max"])) if r["adx_max"] is not None else "없음"
            dpf = r["pf"] - b_pf if r["pf"] != float("inf") else 0
            dbear = r["bear_pf"] - b_bear_pf if r["bear_pf"] != float("inf") else 0
            a(f"| {i} | {r['bp']:.2f} | {adx_s} | {r['trades']} | {fmt_pf(r['pf'])} | "
              f"{dpf:+.2f} | {fmt_pf(r['bear_pf'])} | {dbear:+.2f} | {r['total_pnl']:+,.0f} |")
        a()

        # bear PF 상위 (bear_trades ≥ 10)
        bear_valid = [r for r in valid if r["bear_trades"] >= 10]
        if bear_valid:
            by_bear = sorted(bear_valid, key=lambda x: x["bear_pf"], reverse=True)[:5]
            a("### 5-2. 약세 PF 상위 5 (trades ≥ 200, bear ≥ 10)")
            a()
            a("| # | min_bp | adx_max | trades | PF | bear_trades | bear_PF | ΔbearPF |")
            a("|---|--------|---------|--------|-----|-------------|---------|---------|")
            for i, r in enumerate(by_bear, 1):
                adx_s = str(int(r["adx_max"])) if r["adx_max"] is not None else "없음"
                dbear = r["bear_pf"] - b_bear_pf if r["bear_pf"] != float("inf") else 0
                a(f"| {i} | {r['bp']:.2f} | {adx_s} | {r['trades']} | {fmt_pf(r['pf'])} | "
                  f"{r['bear_trades']} | {fmt_pf(r['bear_pf'])} | {dbear:+.2f} |")
            a()

        # 균형 점수: (PF − baseline_PF) + (bear_PF − baseline_bear_PF) × 0.5, 거래수 200 이상만
        a("### 5-3. 균형 점수 상위 (PF Δ + 0.5 × bear PF Δ)")
        a()
        a("회수 리스크 감소(bear PF) 를 PF 이득과 함께 고려한 종합 점수.")
        a()

        def balance_score(r: dict) -> float:
            pf_d = r["pf"] - b_pf if r["pf"] != float("inf") else 0
            bear_d = r["bear_pf"] - b_bear_pf if r["bear_pf"] != float("inf") else 0
            return pf_d + 0.5 * bear_d

        by_bal = sorted(valid, key=balance_score, reverse=True)[:5]
        a("| # | min_bp | adx_max | trades | PF (Δ) | bear_PF (Δ) | score | PnL |")
        a("|---|--------|---------|--------|--------|-------------|-------|------|")
        for i, r in enumerate(by_bal, 1):
            adx_s = str(int(r["adx_max"])) if r["adx_max"] is not None else "없음"
            dpf = r["pf"] - b_pf if r["pf"] != float("inf") else 0
            dbear = r["bear_pf"] - b_bear_pf if r["bear_pf"] != float("inf") else 0
            sc = balance_score(r)
            a(f"| {i} | {r['bp']:.2f} | {adx_s} | {r['trades']} | "
              f"{fmt_pf(r['pf'])} ({dpf:+.2f}) | {fmt_pf(r['bear_pf'])} ({dbear:+.2f}) | "
              f"{sc:+.2f} | {r['total_pnl']:+,.0f} |")
        a()

    a("---")
    a()

    # ── 6. 권장 ──
    a("## 6. 권장")
    a()
    if not valid:
        a("**현행 baseline 유지 (bp=0.03, adx_max=없음).**")
        a()
        a("20 조합 중 거래 200건 이상인 것이 없어 필터 강화는 통계적 근거 부족.")
    else:
        top = by_pf[0]
        top_adx = str(int(top["adx_max"])) if top["adx_max"] is not None else "없음"
        bal_top = sorted(valid, key=balance_score, reverse=True)[0]
        bal_adx = str(int(bal_top["adx_max"])) if bal_top["adx_max"] is not None else "없음"

        # baseline과 같으면 그냥 유지, 다르면 변경 권장
        baseline_is_top = (
            abs(top["bp"] - 0.03) < 1e-9 and top["adx_max"] is None
        )
        if baseline_is_top:
            a("**현행 baseline이 PF 기준 최적 — 유지 권장.**")
            a()
            a("모든 필터 강화 조합이 baseline PF를 하회하거나 거래수 < 200으로 유효하지 않음.")
        else:
            dpf = top["pf"] - b_pf
            dtrades = top["trades"] - b_trades
            dpnl = top["total_pnl"] - b_pnl
            dbear = top["bear_pf"] - b_bear_pf
            a(f"### PF 최적 제안: bp={top['bp']:.2f}, adx_max={top_adx}")
            a()
            a(f"- PF: {fmt_pf(b_pf)} → **{fmt_pf(top['pf'])}** ({dpf:+.2f})")
            a(f"- 거래수: {b_trades} → **{top['trades']}** ({dtrades:+d})")
            a(f"- bear PF: {fmt_pf(b_bear_pf)} → **{fmt_pf(top['bear_pf'])}** ({dbear:+.2f})")
            a(f"- 총 PnL: {b_pnl:+,.0f} → **{top['total_pnl']:+,.0f}** ({dpnl:+,.0f})")
            a()

            if (bal_top["bp"] != top["bp"]) or (bal_top["adx_max"] != top["adx_max"]):
                b_dpf = bal_top["pf"] - b_pf
                b_dtrades = bal_top["trades"] - b_trades
                b_dbear = bal_top["bear_pf"] - b_bear_pf
                a(f"### 균형 최적 제안: bp={bal_top['bp']:.2f}, adx_max={bal_adx}")
                a()
                a(f"PF와 bear PF 모두 고려 시 이쪽이 더 안전한 sweet spot:")
                a()
                a(f"- PF: {fmt_pf(b_pf)} → **{fmt_pf(bal_top['pf'])}** ({b_dpf:+.2f})")
                a(f"- 거래수: {b_trades} → **{bal_top['trades']}** ({b_dtrades:+d})")
                a(f"- bear PF: {fmt_pf(b_bear_pf)} → **{fmt_pf(bal_top['bear_pf'])}** ({b_dbear:+.2f})")
                a()

            a("### 후속 검증")
            a()
            a("- Walk-Forward (ADR-011 패턴): 학습 구간 PF vs 검증 구간 PF 확인")
            a("- 샘플 외 기간 추가 검증 필요 (본 그리드는 전체 기간 in-sample)")
            a("- config 반영 전 `MomentumStrategy` 에 `adx_max` 필드 필수 추가")
    a()

    a("---")
    a()
    a("## 7. 재현")
    a()
    a("```bash")
    a("python scripts/grid_breakout_adx.py")
    a("python scripts/grid_breakout_adx.py --start 2025-04-01 --end 2026-04-10")
    a("```")
    a()
    a("- baseline (bp=0.03, adx_max=None)은 반드시 포함 — 비교 기준")
    a("- config.yaml / universe.yaml 무수정 (런타임 `dc_replace`, 서브클래스 `AdxMaxMomentumStrategy`)")
    a()

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[REPORT] {REPORT_PATH}")


# ======================================================================
# main
# ======================================================================

async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-04-01")
    parser.add_argument("--end", default="2026-04-10")
    args = parser.parse_args()

    print("=" * 60)
    print(" Breakout × ADX 상한 5×4 그리드")
    print("=" * 60)
    results = await run_grid(args)

    # 콘솔 요약
    print("\n=== 결과 요약 ===")
    print(f"{'bp':>6} {'adxmax':>7} {'trades':>7} {'PF':>7} {'bear n':>7} {'bearPF':>7}")
    for r in results:
        adx_s = f"{int(r['adx_max'])}" if r["adx_max"] is not None else "none"
        print(f"{r['bp']:>6.2f} {adx_s:>7} {r['trades']:>7} {fmt_pf(r['pf']):>7} "
              f"{r['bear_trades']:>7} {fmt_pf(r['bear_pf']):>7}")

    generate_report(results)


if __name__ == "__main__":
    asyncio.run(main())
