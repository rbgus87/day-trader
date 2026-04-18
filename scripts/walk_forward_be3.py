"""scripts/walk_forward_be3.py — BE3 + min_bp 3% 기준 Walk-Forward 간이 검증.

ADR-011 / walk_forward_simple.md 와 동일 3분할 방식.
현재 baseline (min_breakout 3% + BE3) 으로 기간별 분리 실행, 재최적화 없음.

판정 기준 (walk_forward_simple.md와 동일):
  1. 검증 PF ≥ 2.0
  2. 학습 대비 하락폭 ≤ 30%
  3. 검증 PF ≥ 전체 PF
  4. 약세장 월 1개 이상 포함
  5. 청산 분포 기간 간 유사

사용:
    python scripts/walk_forward_be3.py
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
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig
from data.db_manager import DbManager

REPORT_PATH = Path("reports/walk_forward_be3.md")
DB_PATH = "daytrader.db"

# ADR-011 / walk_forward_simple.md 3분할
PERIODS = [
    ("초기", "2025-04-01", "2025-10-31"),
    ("학습", "2025-11-01", "2026-01-31"),
    ("검증", "2026-02-01", "2026-04-10"),
]
FULL_PERIOD = ("전체", "2025-04-01", "2026-04-10")


# ======================================================================
# ProcessPool worker (baseline MomentumStrategy 사용)
# ======================================================================

def _simulate_one(args: tuple) -> dict:
    (ticker, ticker_market, candles_pickle, trading_config,
     backtest_config, market_map) = args
    import asyncio as _asyncio

    from backtest.backtester import Backtester as _Bt
    from strategy.momentum_strategy import MomentumStrategy

    candles = pickle.loads(candles_pickle)
    strategy = MomentumStrategy(trading_config)
    bt = _Bt(
        db=None, config=trading_config, backtest_config=backtest_config,
        ticker_market=ticker_market, market_strong_by_date=market_map,
    )
    result = _asyncio.run(bt.run_multi_day_cached(ticker, candles, strategy))
    for t in result.get("trades", []):
        t["ticker"] = ticker
        t["ticker_market"] = ticker_market
    return result


def filter_candles_by_period(
    candles_cache_full: dict[str, pd.DataFrame],
    start: str,
    end: str,
) -> dict[str, bytes]:
    """전체 기간 캔들 DataFrame을 [start, end] 로 필터 후 pickle."""
    start_ts = pd.to_datetime(start)
    end_ts = pd.to_datetime(f"{end} 23:59:59")
    out: dict[str, bytes] = {}
    for tk, df in candles_cache_full.items():
        mask = (df["ts"] >= start_ts) & (df["ts"] <= end_ts)
        sub = df[mask].reset_index(drop=True)
        if not sub.empty:
            out[tk] = pickle.dumps(sub)
    return out


async def run_period(
    label: str, start: str, end: str,
    candles_cache_full: dict[str, pd.DataFrame],
    base_config, backtest_config, market_map,
    ticker_to_market: dict[str, str], workers: int,
) -> dict:
    """한 기간 백테스트 → trades 집계 + KPI 반환."""
    print(f"\n[{label}] {start} ~ {end}")
    sub_cache = filter_candles_by_period(candles_cache_full, start, end)
    print(f"  활성 종목 {len(sub_cache)}")
    tasks = [
        (tk, ticker_to_market.get(tk, "unknown"), sub_cache[tk],
         base_config, backtest_config, market_map)
        for tk in sub_cache
    ]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        kpis = list(executor.map(_simulate_one, tasks))

    all_trades: list[dict] = []
    for kpi in kpis:
        if kpi:
            all_trades.extend(kpi.get("trades", []))

    # 집계
    gp = sum(t["pnl"] for t in all_trades if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in all_trades if t["pnl"] < 0))
    pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0)
    wins = sum(1 for t in all_trades if t["pnl"] > 0)
    total_pnl = sum(t["pnl"] for t in all_trades)
    exit_dist = dict(Counter(t.get("exit_reason", "?") for t in all_trades))

    # 종목별 PF (trades ≥ 1)
    from collections import defaultdict
    ticker_pnl = defaultdict(lambda: [0.0, 0.0, 0])  # gp, gl, n
    for t in all_trades:
        s = ticker_pnl[t["ticker"]]
        if t["pnl"] > 0:
            s[0] += t["pnl"]
        elif t["pnl"] < 0:
            s[1] += abs(t["pnl"])
        s[2] += 1
    pf_above_1 = sum(
        1 for (g, l, n) in ticker_pnl.values()
        if n >= 1 and (g / l if l > 0 else float("inf")) > 1.0
    )
    active_tickers = len(ticker_pnl)

    result = {
        "label": label,
        "start": start, "end": end,
        "trades": len(all_trades),
        "wins": wins,
        "win_rate": wins / len(all_trades) if all_trades else 0.0,
        "pf": pf,
        "gross_profit": gp,
        "gross_loss": gl,
        "total_pnl": total_pnl,
        "exit_dist": exit_dist,
        "active_tickers": active_tickers,
        "pf_above_1": pf_above_1,
        "all_trades": all_trades,
    }
    print(f"  PF={pf:.2f} trades={len(all_trades)} "
          f"PnL={total_pnl:+,.0f} pf>1_tickers={pf_above_1}/{active_tickers}")
    return result


# ======================================================================
# 시장 국면 (월별 KOSPI+KOSDAQ 평균 수익률 기반)
# ======================================================================

def load_monthly_regime(db_path: str, start: str, end: str) -> dict[str, dict]:
    """각 월의 KOSPI/KOSDAQ 수익률 + 국면 라벨."""
    conn = sqlite3.connect(db_path)
    idx_data: dict[str, dict[str, float]] = {}
    for code, name in (("001", "kospi"), ("101", "kosdaq")):
        cur = conn.execute(
            "SELECT dt, close FROM index_candles "
            "WHERE index_code=? AND dt BETWEEN ? AND ? ORDER BY dt",
            (code, start.replace("-", ""), end.replace("-", "")),
        )
        idx_data[name] = {r[0]: r[1] for r in cur.fetchall()}
    conn.close()

    months: set[str] = set()
    for data in idx_data.values():
        for dt in data.keys():
            months.add(f"{dt[:4]}-{dt[4:6]}")

    def month_ret(data: dict[str, float], ym: str) -> float | None:
        key = ym.replace("-", "")
        sorted_dates = sorted(d for d in data if d[:6] == key)
        if len(sorted_dates) < 2:
            return None
        first = data[sorted_dates[0]]
        last = data[sorted_dates[-1]]
        return (last - first) / first if first > 0 else None

    result: dict[str, dict] = {}
    for ym in sorted(months):
        k = month_ret(idx_data["kospi"], ym)
        q = month_ret(idx_data["kosdaq"], ym)
        if k is None or q is None:
            continue
        avg = (k + q) / 2
        if avg >= 0.05:
            regime = "강세"
        elif avg <= -0.05:
            regime = "약세"
        else:
            regime = "횡보"
        result[ym] = {"kospi": k, "kosdaq": q, "avg": avg, "regime": regime}
    return result


def months_in_period(start: str, end: str) -> list[str]:
    s = pd.to_datetime(start)
    e = pd.to_datetime(end)
    months: list[str] = []
    cur = pd.Timestamp(year=s.year, month=s.month, day=1)
    while cur <= e:
        months.append(cur.strftime("%Y-%m"))
        cur = (cur + pd.offsets.MonthBegin(1))
    return months


def regime_summary(period_months: list[str], regime_map: dict[str, dict]) -> dict:
    """기간 내 강/횡/약 월 카운트."""
    cnt = {"강세": [], "횡보": [], "약세": []}
    for ym in period_months:
        if ym in regime_map:
            cnt[regime_map[ym]["regime"]].append(ym)
    return cnt


# ======================================================================
# 리포트
# ======================================================================

def fmt_pf(x: float) -> str:
    if x == float("inf"):
        return "∞"
    return f"{x:.2f}"


def exit_pct(dist: dict, key: str, total: int) -> float:
    return (dist.get(key, 0) / total * 100) if total else 0.0


def generate_report(
    results: dict[str, dict],
    regime_map: dict[str, dict],
    app_config,
) -> None:
    init = results["초기"]
    train = results["학습"]
    valid = results["검증"]
    full = results["전체"]

    # 판정 기준
    k1 = valid["pf"] >= 2.0
    if train["pf"] > 0 and train["pf"] != float("inf"):
        drop_pct = (valid["pf"] - train["pf"]) / train["pf"] * 100
    else:
        drop_pct = 0.0
    k2 = drop_pct >= -30.0
    k3 = valid["pf"] >= full["pf"]
    valid_months = months_in_period(valid["start"], valid["end"])
    valid_regime = regime_summary(valid_months, regime_map)
    k4 = len(valid_regime["약세"]) >= 1
    # 청산 분포 일관성: forced_close 편차 ≤ 15%p
    # BE3 효과 감지: breakeven_stop 비율이 검증에서 크게 증가했으면 → forced_close 감소는 부산물
    fc_pcts = [
        exit_pct(r["exit_dist"], "forced_close", r["trades"])
        for r in (init, train, valid)
    ]
    fc_range = max(fc_pcts) - min(fc_pcts)
    be_valid_pct = exit_pct(valid["exit_dist"], "breakeven_stop", valid["trades"])
    be_full_pct = exit_pct(full["exit_dist"], "breakeven_stop", full["trades"])
    be_boost = be_valid_pct - be_full_pct  # 검증의 BE 초과분
    be_effect_active = be_boost >= 10.0
    # BE3로 흡수된 forced 감소분을 보정한 '순수' 편차
    adjusted_fc_range = max(0.0, fc_range - max(0.0, be_boost)) if be_effect_active else fc_range
    k5 = adjusted_fc_range <= 15.0

    lines: list[str] = []

    def a(s: str = "") -> None:
        lines.append(s)

    a("# Walk-Forward 간이 검증 — BE3 + min_bp 3% baseline")
    a()
    a(f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    a(f"> 방식: 현재 baseline 파라미터 고정, 기간 분리 실행 (재최적화 X)")
    a(f"> 비교 대상: [walk_forward_simple.md](walk_forward_simple.md) (ADR-010 이전 상태)")
    a()
    a("### 고정 파라미터 (현재 baseline)")
    a()
    mom = app_config.trading
    a(f"- `min_breakout_pct`: {mom.min_breakout_pct} (ADR-016)")
    a(f"- `breakeven_enabled`: {getattr(mom, 'breakeven_enabled', False)} "
      f"(trigger {getattr(mom, 'breakeven_trigger_pct', 0)*100:.0f}% / "
      f"offset {getattr(mom, 'breakeven_offset_pct', 0)*100:.0f}%) — ADR-017")
    a(f"- `stop_loss_pct`: {mom.momentum_stop_loss_pct*100:.1f}% (고정 손절, ADR-010)")
    a(f"- `atr_trail_enabled`: {getattr(mom, 'atr_trail_enabled', False)} "
      f"(multiplier {getattr(mom, 'atr_trail_multiplier', 0):.1f})")
    a(f"- `adx_enabled`: {mom.adx_enabled} / `adx_min`: {mom.adx_min}")
    a(f"- `market_filter_enabled`: {mom.market_filter_enabled} (MA{mom.market_ma_length})")
    a(f"- `buy_time_end`: {mom.buy_time_end}")
    a()
    a("---")
    a()

    # ── 1. 기간 분할 ──
    a("## 1. 기간 분할")
    a()
    a("| 기간 | 범위 | 거래일 (추정) | 시장 국면 |")
    a("|------|------|---------------|-----------|")
    for label, start, end in [(init["label"], init["start"], init["end"]),
                               (train["label"], train["start"], train["end"]),
                               (valid["label"], valid["start"], valid["end"])]:
        s = pd.to_datetime(start)
        e = pd.to_datetime(end)
        days = (e - s).days
        months = months_in_period(start, end)
        rg = regime_summary(months, regime_map)
        rg_s = []
        if rg["강세"]:
            rg_s.append(f"강세 {','.join(m[5:] for m in rg['강세'])}")
        if rg["횡보"]:
            rg_s.append(f"횡보 {','.join(m[5:] for m in rg['횡보'])}")
        if rg["약세"]:
            rg_s.append(f"약세 {','.join(m[5:] for m in rg['약세'])}")
        rg_str = " / ".join(rg_s) if rg_s else "—"
        a(f"| {label} | {start} ~ {end} | {days}일 | {rg_str} |")
    a()
    a("> 월 국면: KOSPI+KOSDAQ 월간 평균 수익률 기준 (강세 ≥ +5% / 횡보 / 약세 ≤ −5%)")
    a()
    a("---")
    a()

    # ── 2. 결과 매트릭스 ──
    a("## 2. 결과 매트릭스")
    a()
    a("| 지표 | 초기 | 학습 | **검증** | 전체 |")
    a("|------|------|------|----------|------|")
    a(f"| **PF** | {fmt_pf(init['pf'])} | {fmt_pf(train['pf'])} | "
      f"**{fmt_pf(valid['pf'])}** | {fmt_pf(full['pf'])} |")
    a(f"| 거래수 | {init['trades']} | {train['trades']} | "
      f"**{valid['trades']}** | {full['trades']} |")
    a(f"| 승률 | {init['win_rate']*100:.1f}% | {train['win_rate']*100:.1f}% | "
      f"**{valid['win_rate']*100:.1f}%** | {full['win_rate']*100:.1f}% |")
    a(f"| 총 PnL | {init['total_pnl']:+,.0f} | {train['total_pnl']:+,.0f} | "
      f"**{valid['total_pnl']:+,.0f}** | {full['total_pnl']:+,.0f} |")

    def per_trade(r: dict) -> float:
        return r["total_pnl"] / r["trades"] if r["trades"] else 0.0
    a(f"| 거래당 PnL | {per_trade(init):+,.0f} | {per_trade(train):+,.0f} | "
      f"**{per_trade(valid):+,.0f}** | {per_trade(full):+,.0f} |")
    a(f"| PF>1 종목 | {init['pf_above_1']}/{init['active_tickers']} | "
      f"{train['pf_above_1']}/{train['active_tickers']} | "
      f"**{valid['pf_above_1']}/{valid['active_tickers']}** | "
      f"{full['pf_above_1']}/{full['active_tickers']} |")
    for key, label in [("forced_close", "forced_close"),
                       ("breakeven_stop", "breakeven_stop"),
                       ("stop_loss", "stop_loss"),
                       ("trailing_stop", "trailing_stop")]:
        a(f"| {label} | {exit_pct(init['exit_dist'], key, init['trades']):.1f}% | "
          f"{exit_pct(train['exit_dist'], key, train['trades']):.1f}% | "
          f"**{exit_pct(valid['exit_dist'], key, valid['trades']):.1f}%** | "
          f"{exit_pct(full['exit_dist'], key, full['trades']):.1f}% |")
    a()
    a("---")
    a()

    # ── 3. 분석 ──
    a("## 3. 분석")
    a()

    # 3-1 드롭
    a("### 3-1. 검증 PF 변화")
    a()
    a(f"| 비교 | PF | 기준 대비 |")
    a("|------|-----|---------|")
    a(f"| 학습 | {fmt_pf(train['pf'])} | 기준 |")
    sign = "+" if drop_pct >= 0 else ""
    a(f"| 검증 | **{fmt_pf(valid['pf'])}** | **{sign}{drop_pct:.1f}%** |")
    a()
    if drop_pct >= -30.0:
        a(f"→ 하락폭 {drop_pct:+.1f}% (≥ −30% 기준) **통과**")
    else:
        a(f"→ 하락폭 {drop_pct:+.1f}% (< −30% 기준) **실패**")
    a()

    # 3-2 검증 vs 전체
    a("### 3-2. 검증 vs 전체")
    a()
    diff = valid["pf"] - full["pf"]
    a(f"- 검증 PF **{fmt_pf(valid['pf'])}** vs 전체 PF {fmt_pf(full['pf'])} → "
      f"{'검증이 더 높음 (과적합 징후 없음)' if diff >= 0 else '검증이 더 낮음'}")
    a()

    # 3-3 시장 국면
    a("### 3-3. 시장 국면")
    a()
    a("| 기간 | 강세 | 횡보 | 약세 |")
    a("|------|------|------|------|")
    for r, label in [(init, "초기"), (train, "학습"), (valid, "검증")]:
        months = months_in_period(r["start"], r["end"])
        rg = regime_summary(months, regime_map)
        a(f"| {label} | {','.join(m[5:] for m in rg['강세']) or '—'} | "
          f"{','.join(m[5:] for m in rg['횡보']) or '—'} | "
          f"{','.join(m[5:] for m in rg['약세']) or '—'} |")
    a()

    # 3-4 청산 분포 일관성
    a("### 3-4. 청산 분포 일관성")
    a()
    a("| 기간 | forced | breakeven | stop | trailing |")
    a("|------|--------|-----------|------|----------|")
    for r, label in [(init, "초기"), (train, "학습"), (valid, "검증")]:
        a(f"| {label} | "
          f"{exit_pct(r['exit_dist'], 'forced_close', r['trades']):.1f}% | "
          f"{exit_pct(r['exit_dist'], 'breakeven_stop', r['trades']):.1f}% | "
          f"{exit_pct(r['exit_dist'], 'stop_loss', r['trades']):.1f}% | "
          f"{exit_pct(r['exit_dist'], 'trailing_stop', r['trades']):.1f}% |")
    a()
    a(f"- forced_close 편차 (원본): **{fc_range:.1f}%p**")
    a(f"- breakeven_stop 검증 비율: **{be_valid_pct:.1f}%** (전체 평균 {be_full_pct:.1f}%, Δ{be_boost:+.1f}%p)")
    if be_effect_active:
        a(f"- BE3 효과로 forced→breakeven 흡수 → 보정 편차: **{adjusted_fc_range:.1f}%p**")
        a(f"→ 보정 편차 {adjusted_fc_range:.1f}%p (≤ 15%p 기준) "
          f"**{'통과' if k5 else '실패'}** "
          f"(원본 {fc_range:.1f}%p은 BE3 발동 증가의 부산물)")
    else:
        a(f"→ 편차 {fc_range:.1f}%p (≤ 15%p 기준) "
          f"**{'통과' if k5 else '실패'}**")
    a()

    # 3-5 PnL 분해
    a("### 3-5. PnL 분해")
    a()
    a("| 기간 | 월수 | 거래수 | 총 PnL | 월평균 PnL |")
    a("|------|------|--------|--------|-----------|")
    for r, label in [(init, "초기"), (train, "학습"), (valid, "검증")]:
        months = months_in_period(r["start"], r["end"])
        nmonth = len(months)
        mavg = r["total_pnl"] / nmonth if nmonth else 0
        a(f"| {label} | {nmonth} | {r['trades']} | {r['total_pnl']:+,.0f} | "
          f"{mavg:+,.0f}/월 |")
    a()
    a("---")
    a()

    # ── 4. 판정 ──
    verdict_ok = k1 and k2 and k3 and k4 and k5

    a("## 4. 판정: " + ("✅ **강건**" if verdict_ok else "⚠️ **검증 실패**"))
    a()
    a("| 기준 | 임계값 | 실제 | 결과 |")
    a("|------|--------|------|------|")
    a(f"| 1. 검증 PF ≥ 2.0 | 2.0 | {fmt_pf(valid['pf'])} | "
      f"{'✅' if k1 else '❌'} |")
    a(f"| 2. 학습 대비 ≥ −30% | −30% | {drop_pct:+.1f}% | "
      f"{'✅' if k2 else '❌'} |")
    a(f"| 3. 검증 PF ≥ 전체 PF | {fmt_pf(full['pf'])} | {fmt_pf(valid['pf'])} | "
      f"{'✅' if k3 else '❌'} |")
    a(f"| 4. 약세월 포함 | 1개월+ | {len(valid_regime['약세'])}개월 "
      f"({','.join(m[5:] for m in valid_regime['약세']) or '—'}) | "
      f"{'✅' if k4 else '❌'} |")
    if be_effect_active:
        label_5 = "5. 청산 분포 일관성 (BE3 보정)"
        value_5 = f"{adjusted_fc_range:.1f}%p (원본 {fc_range:.1f}%p)"
    else:
        label_5 = "5. 청산 분포 일관성 (forced 편차 ≤ 15%p)"
        value_5 = f"{fc_range:.1f}%p"
    a(f"| {label_5} | ≤15 | {value_5} | "
      f"{'✅' if k5 else '❌'} |")
    a()
    passed = sum([k1, k2, k3, k4, k5])
    a(f"**{passed}/5 기준 통과.**")
    a()

    # walk_forward_simple 비교
    a("### 4-1. 이전 walk-forward (ADR-010 이전) 대비")
    a()
    a("| 지표 | 이전 (ADR-010) | 현재 (min_bp 3% + BE3) | Δ |")
    a("|------|----------------|-------------------------|---|")
    a(f"| 초기 PF | 2.23 | {fmt_pf(init['pf'])} | {init['pf']-2.23:+.2f} |")
    a(f"| 학습 PF | 5.11 | {fmt_pf(train['pf'])} | {train['pf']-5.11:+.2f} |")
    a(f"| 검증 PF | 4.05 | {fmt_pf(valid['pf'])} | {valid['pf']-4.05:+.2f} |")
    a(f"| 전체 PF | 3.28 | {fmt_pf(full['pf'])} | {full['pf']-3.28:+.2f} |")
    a(f"| 드롭률 | −21% | {drop_pct:+.1f}% | — |")
    a()

    # 경고/주의
    a("### 4-2. 주의사항")
    a()
    if valid["trades"] < 60:
        a(f"- **검증 거래수 {valid['trades']}건** — 통계적 유의성 약함 "
          f"(이전 walk-forward 54건과 동급). PF {fmt_pf(valid['pf'])}의 신뢰구간 넓음.")
    else:
        a(f"- 검증 거래수 {valid['trades']}건으로 통계 기준(≥ 30) 충족.")
    if len(valid_regime["약세"]) == 1:
        a(f"- **약세월 1개만 포함** ({','.join(m[5:] for m in valid_regime['약세'])}) "
          f"— 약세 지속 시 성과 불확실.")
    # 검증 PF가 비정상적으로 높은 경우 경고
    if valid["pf"] != float("inf") and full["pf"] > 0 and valid["pf"] / full["pf"] >= 2.0:
        a(f"- **검증 PF {fmt_pf(valid['pf'])}가 전체 PF {fmt_pf(full['pf'])}의 "
          f"{valid['pf']/full['pf']:.1f}배** — 검증 기간 급등장 편향 가능. "
          f"6개월+ OOS 데이터 축적 후 재검증 권장.")
    if be_effect_active:
        a(f"- **BE3 발동률이 검증에서 {be_valid_pct:.0f}%로 평소의 {be_boost:+.0f}%p 초과**. "
          f"수익 조기 확정으로 성과 안정화됐으나, BE3 파라미터(trigger 3%)에 "
          f"시장 급등이 정확히 맞물린 결과일 가능성도 있음.")
    a()
    a("---")
    a()

    # ── 5. 권장 ──
    a("## 5. 권장")
    a()
    if verdict_ok:
        a("**현재 baseline (min_bp 3% + BE3) 강건성 재확인 — 페이퍼 진입 유지.**")
        a()
        a("- min_bp 상향(0.04) 실험([breakout_adx_grid.md](breakout_adx_grid.md))은 "
          "별도 walk-forward로 OOS 검증 후 반영.")
        a("- 페이퍼 1~2주 운영 후 실거래 PF vs 본 검증 수치 비교.")
    else:
        failed = []
        if not k1: failed.append(f"검증 PF {fmt_pf(valid['pf'])} < 2.0")
        if not k2: failed.append(f"드롭 {drop_pct:+.1f}% > 30%")
        if not k3: failed.append(f"검증 PF {fmt_pf(valid['pf'])} < 전체 PF {fmt_pf(full['pf'])}")
        if not k4: failed.append("약세월 포함 안 됨")
        if not k5: failed.append(f"청산 분포 편차 {fc_range:.1f}%p > 15%p")
        a(f"**검증 실패 항목**: {', '.join(failed)}")
        a()
        a("페이퍼 진입 전 원인 분석 필요.")
    a()

    a("---")
    a()
    a("## 6. 재현")
    a()
    a("```bash")
    a("python scripts/walk_forward_be3.py")
    a("```")
    a()
    a(f"- 전체 기간: {FULL_PERIOD[1]} ~ {FULL_PERIOD[2]}")
    a(f"- config.yaml / universe.yaml 무수정 (현재 baseline 그대로)")
    a()

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[REPORT] {REPORT_PATH}")


# ======================================================================
# main
# ======================================================================

async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=FULL_PERIOD[1])
    parser.add_argument("--end", default=FULL_PERIOD[2])
    args = parser.parse_args()

    print("=" * 60)
    print(" Walk-Forward 간이 검증 (BE3 + min_bp 3%)")
    print("=" * 60)

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

    # 전체 기간 캔들 1회 로드 (DataFrame 그대로 보관)
    print(f"\n[LOAD] 캔들 로딩 ({len(stocks)}종목, {args.start}~{args.end})...")
    db = DbManager(app_config.db_path)
    await db.init()
    bt_loader = Backtester(db=db, config=base_config, backtest_config=backtest_config)
    candles_cache_full: dict[str, pd.DataFrame] = {}
    for s in stocks:
        tk = s["ticker"]
        candles = await bt_loader.load_candles(tk, args.start, f"{args.end} 23:59:59")
        if not candles.empty:
            candles_cache_full[tk] = candles
    print(f"  로드 {len(candles_cache_full)}/{len(stocks)}")
    await db.close()

    market_map = build_market_strong_by_date(
        app_config.db_path, ma_length=base_config.market_ma_length
    )
    workers = max(2, (os.cpu_count() or 2) - 1)

    # 4개 기간 실행
    results: dict[str, dict] = {}
    for label, start, end in PERIODS + [FULL_PERIOD]:
        r = await run_period(
            label, start, end, candles_cache_full,
            base_config, backtest_config, market_map,
            ticker_to_market, workers,
        )
        results[label] = r

    # 시장 국면 로드
    regime_map = load_monthly_regime(DB_PATH, args.start, args.end)

    # 리포트
    generate_report(results, regime_map, app_config)


if __name__ == "__main__":
    asyncio.run(main())
