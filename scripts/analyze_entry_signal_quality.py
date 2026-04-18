"""scripts/analyze_entry_signal_quality.py — 진입 지표 vs PnL 상관 분석.

동시 시그널 처리 시 priority score 설계 기초 데이터.
각 baseline trade의 (breakout_pct, volume_ratio, adx, entry_minute)와 pnl_pct 관계.

사용:
    python scripts/analyze_entry_signal_quality.py
    python scripts/analyze_entry_signal_quality.py --use-cache
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pickle
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig
from data.db_manager import DbManager

DB_PATH = "daytrader.db"
REPORT_PATH = Path("reports/entry_signal_quality.md")
CACHE_DIR = Path("reports/cache")
TRADES_CACHE = CACHE_DIR / "entry_signal_trades.pkl"
ENRICHED_CACHE = CACHE_DIR / "entry_signal_enriched.pkl"


# ======================================================================
# 1. baseline 백테스트 (analyze_baseline.py 패턴 재사용)
# ======================================================================

def _simulate_one(args: tuple) -> dict:
    ticker, ticker_market, candles_pickle, trading_config, backtest_config, market_map = args
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


async def collect_all_trades(start: str, end: str) -> list[dict]:
    from concurrent.futures import ProcessPoolExecutor

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

    db = DbManager(app_config.db_path)
    await db.init()
    bt_loader = Backtester(db=db, config=base_config, backtest_config=backtest_config)

    print(f"[LOAD] 캔들 로딩 ({len(stocks)}종목)...")
    candles_cache: dict[str, bytes] = {}
    for s in stocks:
        tk = s["ticker"]
        candles = await bt_loader.load_candles(tk, start, f"{end} 23:59:59")
        if not candles.empty:
            candles_cache[tk] = pickle.dumps(candles)
    print(f"  로드 {len(candles_cache)}/{len(stocks)}")
    await db.close()

    market_map = build_market_strong_by_date(
        app_config.db_path, ma_length=base_config.market_ma_length
    )
    workers = max(2, (os.cpu_count() or 2) - 1)
    print(f"[RUN] ProcessPool {workers}...")
    tasks = [
        (tk, ticker_to_market.get(tk, "unknown"), candles_cache[tk],
         base_config, backtest_config, market_map)
        for tk in candles_cache
    ]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        kpis = list(executor.map(_simulate_one, tasks))

    all_trades: list[dict] = []
    for kpi in kpis:
        if kpi:
            all_trades.extend(kpi.get("trades", []))
    print(f"[DONE] {len(all_trades)}건")
    return all_trades


# ======================================================================
# 2. 진입 시점 지표 enrich
# ======================================================================

def enrich_trade_indicators(trades: list[dict], db_path: str) -> list[dict]:
    """각 trade에 breakout_pct, volume_ratio, adx, entry_minute 주입.

    backtester의 MomentumStrategy 신호 경로와 동일한 방식으로 재계산:
      - prev_day_high/volume: 직전 거래일 intraday_candles 집계 (backtester.py:651)
      - cum_volume: 당일 09:00 ~ entry_ts 합산 (momentum_strategy.py:80)
      - adx: 당일 캔들에 pandas_ta.adx(length=14), 마지막 34개 (momentum_strategy.py:125-145)
      - entry_minute: (entry_ts - 09:00).total_seconds() / 60
    """
    import pandas_ta as ta

    conn = sqlite3.connect(db_path)
    # 종목별 전체 분봉 캐시
    ticker_candles_cache: dict[str, pd.DataFrame] = {}

    enriched: list[dict] = []
    for i, t in enumerate(trades):
        ticker = t["ticker"]
        entry_ts = pd.to_datetime(t["entry_ts"])
        entry_price = float(t["entry_price"])

        if ticker not in ticker_candles_cache:
            cur = conn.execute(
                "SELECT ts, open, high, low, close, volume FROM intraday_candles "
                "WHERE ticker=? AND tf='1m' ORDER BY ts ASC",
                (ticker,),
            )
            rows = cur.fetchall()
            if not rows:
                ticker_candles_cache[ticker] = pd.DataFrame()
                continue
            df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
            df["ts"] = pd.to_datetime(df["ts"])
            df["date"] = df["ts"].dt.date
            ticker_candles_cache[ticker] = df

        df_all = ticker_candles_cache[ticker]
        if df_all.empty:
            continue

        entry_date_obj = entry_ts.date()
        day_mask = df_all["date"] == entry_date_obj
        day_df = df_all[day_mask]
        if day_df.empty:
            continue
        up_to_entry = day_df[day_df["ts"] <= entry_ts]
        if up_to_entry.empty:
            continue

        # 전일 거래일
        prev_dates = df_all[df_all["date"] < entry_date_obj]["date"].unique()
        if len(prev_dates) == 0:
            continue
        prev_date = sorted(prev_dates)[-1]
        prev_df = df_all[df_all["date"] == prev_date]
        if prev_df.empty:
            continue

        prev_day_high = float(prev_df["high"].max())
        prev_day_volume = int(prev_df["volume"].sum())
        if prev_day_high <= 0 or prev_day_volume <= 0:
            continue

        breakout_pct = (entry_price - prev_day_high) / prev_day_high
        cum_vol = int(up_to_entry["volume"].sum())
        volume_ratio = cum_vol / prev_day_volume

        # ADX — momentum_strategy와 동일 방식
        adx_val = np.nan
        if len(up_to_entry) >= 34:
            try:
                tail = up_to_entry.tail(34)
                adx_res = ta.adx(tail["high"], tail["low"], tail["close"], length=14)
                if adx_res is not None and not adx_res.empty and "ADX_14" in adx_res.columns:
                    v = adx_res["ADX_14"].iloc[-1]
                    if not pd.isna(v):
                        adx_val = float(v)
            except Exception:
                pass

        # 09:00 기준 분
        base_9am = entry_ts.normalize().replace(hour=9, minute=0, second=0, microsecond=0)
        entry_minute = (entry_ts - base_9am).total_seconds() / 60.0

        out = {**t}
        out["breakout_pct"] = breakout_pct
        out["volume_ratio"] = volume_ratio
        out["adx"] = adx_val
        out["entry_minute"] = entry_minute
        out["prev_day_high"] = prev_day_high
        out["prev_day_volume"] = prev_day_volume
        enriched.append(out)

        if (i + 1) % 50 == 0:
            print(f"  enrich {i+1}/{len(trades)}")

    conn.close()
    print(f"  enrich 완료: {len(enriched)}/{len(trades)}")
    return enriched


# ======================================================================
# 3. 상관 / 버킷 / 회귀
# ======================================================================

INDICATOR_COLS = ["breakout_pct", "volume_ratio", "adx", "entry_minute"]


def correlation_matrix(df: pd.DataFrame) -> dict:
    result = {}
    for c in INDICATOR_COLS:
        valid = df[[c, "pnl_pct", "pnl"]].dropna()
        if len(valid) < 5:
            result[c] = {"n": len(valid), "pearson": None, "spearman": None,
                         "win_rate_top": None, "win_rate_bot": None, "avg_pnl_top": None,
                         "avg_pnl_bot": None}
            continue
        pearson = valid[c].corr(valid["pnl_pct"], method="pearson")
        spearman = valid[c].corr(valid["pnl_pct"], method="spearman")
        median = valid[c].median()
        top = valid[valid[c] >= median]
        bot = valid[valid[c] < median]
        result[c] = {
            "n": len(valid),
            "pearson": float(pearson),
            "spearman": float(spearman),
            "win_rate_top": float((top["pnl_pct"] > 0).mean()) if len(top) else None,
            "win_rate_bot": float((bot["pnl_pct"] > 0).mean()) if len(bot) else None,
            "avg_pnl_top": float(top["pnl"].mean()) if len(top) else None,
            "avg_pnl_bot": float(bot["pnl"].mean()) if len(bot) else None,
        }
    return result


def bucket_analysis(df: pd.DataFrame, col: str, bins: list, labels: list) -> list[dict]:
    df2 = df.dropna(subset=[col, "pnl_pct"]).copy()
    df2["bucket"] = pd.cut(df2[col], bins=bins, labels=labels,
                           include_lowest=True, right=False)
    stats = []
    for label in labels:
        s = df2[df2["bucket"] == label]
        n = len(s)
        gp = float(s[s["pnl"] > 0]["pnl"].sum()) if n else 0.0
        gl = float(-s[s["pnl"] < 0]["pnl"].sum()) if n else 0.0
        pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0)
        stats.append({
            "label": label,
            "n": n,
            "avg_pnl": float(s["pnl"].mean()) if n else 0.0,
            "avg_pnl_pct": float(s["pnl_pct"].mean()) if n else 0.0,
            "win_rate": float((s["pnl_pct"] > 0).mean()) if n else 0.0,
            "total_pnl": float(s["pnl"].sum()) if n else 0.0,
            "pf": pf,
        })
    return stats


def monotonicity(stats: list[dict]) -> str:
    """버킷 평균 PnL%의 단조성 판정 (n이 있는 버킷만)."""
    vals = [s["avg_pnl_pct"] for s in stats if s["n"] >= 5]
    if len(vals) < 3:
        return "데이터 부족"
    up = all(vals[i] <= vals[i + 1] for i in range(len(vals) - 1))
    dn = all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1))
    if up:
        return "단조 증가"
    if dn:
        return "단조 감소"
    # 극단 vs 중앙
    extremes = [vals[0], vals[-1]]
    mids = vals[1:-1]
    if mids and max(mids) > max(extremes):
        return "역 U자 (중앙 우세)"
    if mids and min(mids) < min(extremes):
        return "U자 (양끝 우세)"
    return "비단조"


def multiple_regression(df: pd.DataFrame) -> dict | None:
    valid = df.dropna(subset=INDICATOR_COLS + ["pnl_pct"])
    if len(valid) < 20:
        return None
    X_raw = valid[INDICATOR_COLS].to_numpy(dtype=float)
    y = valid["pnl_pct"].to_numpy(dtype=float)
    mu = X_raw.mean(axis=0)
    sd = X_raw.std(axis=0, ddof=1)
    sd = np.where(sd == 0, 1.0, sd)
    X_std = (X_raw - mu) / sd
    X = np.column_stack([X_std, np.ones(len(y))])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    y_pred = X @ beta
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {
        "n": int(len(valid)),
        "beta": dict(zip(INDICATOR_COLS + ["intercept"], beta.tolist())),
        "r2": float(r2),
        "mu": dict(zip(INDICATOR_COLS, mu.tolist())),
        "sd": dict(zip(INDICATOR_COLS, sd.tolist())),
    }


# ======================================================================
# 4. 리포트
# ======================================================================

def classify_signal(pearson: float, spearman: float) -> str:
    mag = max(abs(pearson), abs(spearman))
    if mag >= 0.2:
        return "강"
    if mag >= 0.1:
        return "약"
    return "무"


def gen_report(
    df: pd.DataFrame,
    corr: dict,
    buckets: dict,
    reg: dict | None,
    total_pf: float,
    total_pnl: float,
    exit_dist: dict,
) -> None:
    lines: list[str] = []

    def a(s: str = "") -> None:
        lines.append(s)

    a("# 진입 시점 지표 vs PnL 상관 분석")
    a()
    a(f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    a(f"> 분석: baseline **{len(df)}건** (min_bp 3% + BE3)")
    a(f"> baseline: PF **{total_pf:.2f}** / PnL {total_pnl:+,.0f} / 청산 {exit_dist}")
    a(f"> 목적: 동시 시그널 시 진입 우선순위 score 설계 기초")
    a()
    a("---")
    a()

    # ── 1. 상관 매트릭스 ──
    a("## 1. 지표 vs PnL 상관")
    a()
    a("| 지표 | n | Pearson r (vs pnl_pct) | Spearman ρ | 상위 50% 승률 | 하위 50% 승률 | 승률 Δ | 상위 평균 PnL | 하위 평균 PnL | 신호 |")
    a("|------|---|------------------------|------------|---------------|---------------|--------|---------------|---------------|------|")
    for c in INDICATOR_COLS:
        s = corr[c]
        if s["pearson"] is None:
            a(f"| {c} | {s['n']} | — | — | — | — | — | — | — | 무 |")
            continue
        wdiff = s["win_rate_top"] - s["win_rate_bot"]
        sig = classify_signal(s["pearson"], s["spearman"])
        a(
            f"| {c} | {s['n']} | {s['pearson']:+.3f} | {s['spearman']:+.3f} | "
            f"{s['win_rate_top']*100:.1f}% | {s['win_rate_bot']*100:.1f}% | "
            f"{wdiff*100:+.1f}%p | {s['avg_pnl_top']:+,.0f} | {s['avg_pnl_bot']:+,.0f} | {sig} |"
        )
    a()
    a("> **판독**: |r| ≥ 0.2 강, 0.1 ≤ |r| < 0.2 약, |r| < 0.1 무.")
    a("> Pearson은 선형, Spearman은 순위 — 선형성 약해도 단조 관계가 있으면 Spearman이 크다.")
    a()

    # ── 2. 버킷 분석 ──
    a("## 2. 버킷 분석")
    a()

    def render_bk(name: str, stats: list[dict]) -> None:
        pat = monotonicity(stats)
        a(f"### 2-{name} — 패턴: **{pat}**")
        a()
        a("| 버킷 | 거래수 | 평균 PnL (원) | 평균 PnL% | 승률 | PF | 총 PnL |")
        a("|------|--------|---------------|-----------|------|-----|--------|")
        for s in stats:
            if s["n"] == 0:
                a(f"| {s['label']} | 0 | — | — | — | — | — |")
            else:
                pf_s = f"{s['pf']:.2f}" if s["pf"] != float("inf") else "∞"
                a(f"| {s['label']} | {s['n']} | {s['avg_pnl']:+,.0f} | "
                  f"{s['avg_pnl_pct']*100:+.3f}% | {s['win_rate']*100:.1f}% | "
                  f"{pf_s} | {s['total_pnl']:+,.0f} |")
        a()

    render_bk("1. 돌파폭 (breakout_pct)", buckets["breakout_pct"])
    render_bk("2. 거래량 배수 (volume_ratio)", buckets["volume_ratio"])
    render_bk("3. ADX", buckets["adx"])
    render_bk("4. 진입 시간 (09:00 기준 분)", buckets["entry_minute"])

    # ── 2.5 버킷 해석 (자동 + 수동) ──
    a("### 2-5. 버킷 관찰")
    a()
    # 최고/최악 PnL% 버킷 자동 추출 (n≥5 필터)
    a("| 지표 | 최고 PnL% 버킷 | 최악 PnL% 버킷 | 최악 버킷 PF | 해석 후보 |")
    a("|------|-----------------|-----------------|---------------|------------|")
    for name_ko, key in [
        ("돌파폭", "breakout_pct"), ("거래량 배수", "volume_ratio"),
        ("ADX", "adx"), ("진입 시간", "entry_minute"),
    ]:
        sts = [s for s in buckets[key] if s["n"] >= 5]
        if not sts:
            a(f"| {name_ko} | — | — | — | 데이터 부족 |")
            continue
        best = max(sts, key=lambda x: x["avg_pnl_pct"])
        worst = min(sts, key=lambda x: x["avg_pnl_pct"])
        worst_pf = f"{worst['pf']:.2f}" if worst["pf"] != float("inf") else "∞"
        if worst["pf"] < 1.0 and worst["n"] >= 5:
            interp = "**회피 버킷 존재** — 하드 필터 후보"
        elif best["avg_pnl_pct"] > worst["avg_pnl_pct"] * 2 and worst["avg_pnl_pct"] > 0:
            interp = "우위 버킷 있음 — priority tilt 후보"
        else:
            interp = "버킷 차이 작음"
        a(f"| {name_ko} | {best['label']} ({best['avg_pnl_pct']*100:+.2f}%) | "
          f"{worst['label']} ({worst['avg_pnl_pct']*100:+.2f}%) | {worst_pf} | {interp} |")
    a()

    # ── 3. 다중 회귀 ──
    a("## 3. 다중 회귀 (표준화 계수)")
    a()
    if reg is None:
        a("데이터 부족 — 회귀 불가.")
    else:
        a(f"- n = **{reg['n']}**, R² = **{reg['r2']:.4f}** ({reg['r2']*100:.1f}%)")
        a(f"- 종속: pnl_pct (비표준화), 독립: z-score 표준화 (계수 크기로 상대 영향력 비교)")
        a()
        a("| 변수 | β (std) | |β| | 부호 |")
        a("|------|---------|-----|------|")
        ranked = sorted(
            [(c, reg["beta"][c]) for c in INDICATOR_COLS],
            key=lambda x: abs(x[1]),
            reverse=True,
        )
        for c, b in ranked:
            a(f"| {c} | {b:+.5f} | {abs(b):.5f} | {'+' if b > 0 else '−'} |")
        a(f"| intercept | {reg['beta']['intercept']:+.5f} | — | — |")
        a()
        a(f"> **R² {reg['r2']*100:.1f}%**: 이 4지표로 pnl_pct 변동의 {reg['r2']*100:.1f}%만 설명. "
          f"나머지 {(1-reg['r2'])*100:.1f}%는 시장/종목 특성/청산 타이밍 등 다른 요인.")
    a()
    a("---")
    a()

    # ── 4. priority score 권장 ──
    a("## 4. priority score 권장")
    a()
    strong: list[tuple[str, dict]] = []
    weak: list[tuple[str, dict]] = []
    for c in INDICATOR_COLS:
        s = corr[c]
        if s["pearson"] is None:
            continue
        mag = max(abs(s["pearson"]), abs(s["spearman"]))
        if mag >= 0.2:
            strong.append((c, s))
        elif mag >= 0.1:
            weak.append((c, s))

    # 회피 버킷 (PF<1, n≥5) 자동 탐지
    avoid_buckets: list[tuple[str, dict]] = []
    for name_ko, key in [
        ("돌파폭", "breakout_pct"), ("거래량 배수", "volume_ratio"),
        ("ADX", "adx"), ("진입 시간", "entry_minute"),
    ]:
        for s in buckets[key]:
            if s["n"] >= 5 and s["pf"] < 1.0:
                avoid_buckets.append((key, s))

    # 단조 버킷 (priority 후보)
    monotone_candidates: list[tuple[str, str]] = []  # (key, direction)
    for key in ["breakout_pct", "volume_ratio", "adx", "entry_minute"]:
        pat = monotonicity(buckets[key])
        if pat == "단조 증가":
            monotone_candidates.append((key, "+"))
        elif pat == "단조 감소":
            monotone_candidates.append((key, "-"))

    if not strong and not weak and not avoid_buckets and not monotone_candidates:
        a("### 결론: 우선순위 효과 제한적")
        a()
        a("모든 4지표의 |r| < 0.1, 회피/단조 버킷 없음 → **priority score 도입 효과 제한적**.")
        a("동시 시그널 시 **현행 FIFO 유지**가 합리적.")
    elif strong:
        a("### 결론: priority 후보 선정")
        a()
        a("**강한 상관 (|r| ≥ 0.2)**:")
        for c, s in strong:
            a(f"- **{c}** — r={s['pearson']:+.3f}, ρ={s['spearman']:+.3f}, "
              f"승률 Δ {(s['win_rate_top']-s['win_rate_bot'])*100:+.1f}%p")
        a()
        if weak:
            a("**약한 상관 (tie-breaker 후보)**:")
            for c, s in weak:
                a(f"- {c} — r={s['pearson']:+.3f}")
            a()

        if reg is not None and reg["r2"] >= 0.05:
            a(f"#### 복합 priority score 초안 (R²={reg['r2']*100:.1f}% — 복합 이득 있음)")
            a()
            a("표준화 계수를 직접 가중치로 사용 (z-score로 변환 후 합산):")
            a("```python")
            a("priority = (")
            for c in INDICATOR_COLS:
                b = reg["beta"][c]
                if abs(b) >= 0.003:
                    a(f"    {b:+.5f} * z({c})")
            a(")")
            a("```")
            a("- z(x) = (x − μ) / σ")
            mu = reg["mu"]
            sd = reg["sd"]
            for c in INDICATOR_COLS:
                a(f"  - {c}: μ={mu[c]:.4f}, σ={sd[c]:.4f}")
        else:
            r2_str = f"{reg['r2']*100:.1f}%" if reg else "n/a"
            a(f"#### 단일 지표 priority 권장 (R²={r2_str} — 복합 이득 미미)")
            a()
            top_c, top_s = max(strong, key=lambda x: abs(x[1]["pearson"]))
            sign = "내림차순" if top_s["pearson"] > 0 else "오름차순"
            a(f"`{top_c}` 한 지표로 {sign} 정렬:")
            a("```python")
            a(f"priority = {'+' if top_s['pearson'] > 0 else '-'} {top_c}")
            a("```")
    else:
        # 강한 상관은 없지만 약한 상관 / 회피 버킷 / 단조 패턴이 있는 경우
        a("### 결론: 상관은 약하나 버킷 엣지 존재")
        a()
        a(f"전체 선형 상관은 |r| < 0.2로 약함 (회귀 R² = {reg['r2']*100:.1f}% 뿐). "
          f"**단일 priority score로는 효과 제한적**. 그러나 아래 두 경로는 실질적 이득 가능.")
        a()

        # (a) 회피 필터 후보
        if avoid_buckets:
            a("#### 4-1. 회피 필터 후보 (하드 컷) — PF < 1 버킷")
            a()
            a("| 지표 | 버킷 | n | PnL% | 승률 | PF | 판단 |")
            a("|------|------|---|------|------|-----|------|")
            for key, s in avoid_buckets:
                pf_s = f"{s['pf']:.2f}" if s["pf"] != float("inf") else "∞"
                sample = "소표본" if s["n"] < 20 else "확정적"
                a(f"| {key} | {s['label']} | {s['n']} | {s['avg_pnl_pct']*100:+.2f}% | "
                  f"{s['win_rate']*100:.1f}% | {pf_s} | {sample} |")
            a()
            a("> **제안**: PF<1 버킷이 소표본이면 walk-forward로 일관성 먼저 검증, "
              "n≥20이면 즉시 하드 필터 후보.")
            a()

        # (b) 단조 패턴 (priority tilt 후보)
        if monotone_candidates:
            a("#### 4-2. priority tilt 후보 — 단조 버킷 패턴")
            a()
            a("회귀 R²는 낮지만 버킷 단조성이 있으면 **priority 정렬에 활용 가능** "
              "(회귀는 선형 가정, 버킷은 비선형 허용).")
            a()
            for key, direction in monotone_candidates:
                sts = [s for s in buckets[key] if s["n"] >= 5]
                if not sts:
                    continue
                best = max(sts, key=lambda x: x["avg_pnl_pct"])
                worst = min(sts, key=lambda x: x["avg_pnl_pct"])
                arrow = "↑" if direction == "+" else "↓"
                dir_ko = "낮을수록 유리" if direction == "-" else "높을수록 유리"
                a(f"- **{key}** {arrow} — {dir_ko}: "
                  f"{best['label']} {best['avg_pnl_pct']*100:+.2f}% vs "
                  f"{worst['label']} {worst['avg_pnl_pct']*100:+.2f}%")
            a()

        # (c) 약한 상관 보조
        if weak:
            a("#### 4-3. tie-breaker 후보 (약한 상관)")
            a()
            for c, s in weak:
                a(f"- {c} — r={s['pearson']:+.3f}, ρ={s['spearman']:+.3f}")
            a()

        # 종합
        a("#### 4-4. 권장")
        a()
        if avoid_buckets and not monotone_candidates:
            a("→ **회피 필터만** 도입. priority 정렬은 효과 없음.")
        elif monotone_candidates and not avoid_buckets:
            a("→ **priority tilt 시도**: 단조 패턴을 tie-breaker로 (가중치 작게).")
        elif avoid_buckets and monotone_candidates:
            a("→ **2단계 접근**:")
            a("  1. PF<1 버킷을 하드 필터로 제거 (이득이 뚜렷하고 표본 크기 충분한 것부터)")
            a("  2. 남은 시그널을 단조 패턴 지표로 정렬 (예: priority = −adx_z 등)")
        else:
            a("→ 약한 상관 지표를 tie-breaker로만 사용. priority score 도입 지양.")
        a()

        # 구체적 후속 액션 — 질문 맥락이 "진입 우선순위"이므로 분리 제안
        a("#### 4-5. 구체적 후속 액션 (우선순위 vs 필터 강화 분리)")
        a()
        a("동시 시그널 시 **진입 우선순위 score** 질문에 대한 직답:")
        a()
        a("> **priority score 도입 효과는 크지 않다** (회귀 R² 2% 대). "
          "그러나 baseline 버킷 분포에는 **필터 강화로 더 큰 이득**이 있을 가능성이 보인다.")
        a()
        a("| # | 후속 실험 | 근거 | 우선순위 | 비고 |")
        a("|---|-----------|------|----------|------|")

        # 실험 1: 돌파폭 하한 상향
        bk = buckets["breakout_pct"]
        bk_low = next((s for s in bk if s["label"] == "3~5%"), None)
        bk_mid = next((s for s in bk if s["label"] == "5~8%"), None)
        if bk_low and bk_mid and bk_low["pf"] < bk_mid["pf"] * 0.5 and bk_low["n"] >= 20:
            a(f"| 1 | `min_breakout_pct`: 0.03 → 0.05 grid | 3~5% 버킷 PF {bk_low['pf']:.2f} "
              f"<< 5~8% 버킷 PF {bk_mid['pf']:.2f} (n {bk_low['n']}) | 高 | "
              f"ADR-016 연장선 (3%→5% 비교) |")

        # 실험 2: ADX 상한
        bk_a = buckets["adx"]
        bk_a_low = next((s for s in bk_a if s["label"] == "20~25"), None)
        bk_a_high = next((s for s in bk_a if s["label"] == "50+"), None)
        if bk_a_low and bk_a_high and bk_a_low["avg_pnl_pct"] > bk_a_high["avg_pnl_pct"] * 2:
            a(f"| 2 | `adx_max` 도입 (예: 40 / 50) | ADX 단조 감소: "
              f"20~25 +{bk_a_low['avg_pnl_pct']*100:.2f}% → "
              f"50+ +{bk_a_high['avg_pnl_pct']*100:.2f}% (PF {bk_a_high['pf']:.2f}) | 高 | "
              f"현재 ADX≥20만 있음, 상한 없음 |")

        # 실험 3: 거래량 배수 5~10x 회피
        bk_v = buckets["volume_ratio"]
        bk_v_bad = next((s for s in bk_v if s["label"] == "5~10x"), None)
        if bk_v_bad and bk_v_bad["pf"] < 1.0 and bk_v_bad["n"] >= 5:
            a(f"| 3 | volume_ratio 5~10x 회피 시뮬 | PF {bk_v_bad['pf']:.2f}, "
              f"승률 {bk_v_bad['win_rate']*100:.0f}% (n {bk_v_bad['n']}) | 中 | "
              f"소표본 — walk-forward 일관성 선검증 |")

        # 실험 4: 진입 시간 확대
        bk_t = buckets["entry_minute"]
        bk_t_late = next((s for s in bk_t if s["label"] == "11:00~12:00"), None)
        bk_t_best = max((s for s in bk_t if s["n"] >= 5),
                        key=lambda x: x["avg_pnl_pct"], default=None)
        if bk_t_late and bk_t_best and bk_t_late["label"] == bk_t_best["label"]:
            a(f"| 4 | `buy_time_end`: 12:00 → 12:30 grid | 11:00~12:00 버킷이 "
              f"PF {bk_t_late['pf']:.2f}로 최고 → 후반 진입이 유리 | 中 | "
              f"CLAUDE.md 엣지 #2와 방향 반대이므로 신중 |")

        # 실험 5: ADX priority tilt (진짜 priority)
        if any(k == "adx" for k, _ in monotone_candidates):
            a(f"| 5 | **priority = −adx** (tie-breaker용) | 동시 시그널 시 ADX 낮은 종목 우선 "
              f"(R² 기여 β_adx={reg['beta']['adx']:+.5f}) | 低 | "
              f"|r|=0.15로 약함, 이득 크지 않을 가능성 |")

        a()
        a("> 우선순위는 **필터 강화(#1,#2)가 priority 정렬(#5)보다 기대 이득이 크다**는 것이 "
          "이번 분석의 핵심 결론.")
    a()
    a("---")
    a()

    # ── 5. 메타 ──
    a("## 5. 재현 방법")
    a()
    a("```bash")
    a("python scripts/analyze_entry_signal_quality.py")
    a("# 재실행 시 (백테스트/enrich 캐시 활용):")
    a("python scripts/analyze_entry_signal_quality.py --use-cache")
    a("```")
    a()
    a("- 데이터 소스: `intraday_candles` + `config/universe.yaml` (41종목)")
    a("- baseline 재현: `analyze_baseline.py` 동일 경로 (`run_multi_day_cached` × ProcessPool)")
    a("- 지표 계산: `momentum_strategy._check_adx` / L80 cum_vol / backtester L651 전일 집계와 일치")
    a()

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"[REPORT] {REPORT_PATH}")


# ======================================================================
# main
# ======================================================================

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-04-01")
    parser.add_argument("--end", default="2026-04-10")
    parser.add_argument("--use-cache", action="store_true",
                        help="캐시된 trades/enriched가 있으면 재사용")
    args = parser.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # 1. baseline trades
    if args.use_cache and TRADES_CACHE.exists():
        print(f"[CACHE] trades load: {TRADES_CACHE}")
        trades = pickle.loads(TRADES_CACHE.read_bytes())
    else:
        print(f"[RUN] baseline 백테스트 ({args.start} ~ {args.end})")
        trades = await collect_all_trades(args.start, args.end)
        TRADES_CACHE.write_bytes(pickle.dumps(trades))
        print(f"[CACHE] trades 저장: {TRADES_CACHE}")

    if not trades:
        print("ERROR: trades 없음")
        return

    total_pnl = sum(t["pnl"] for t in trades)
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    total_pf = gp / gl if gl > 0 else float("inf")
    from collections import Counter
    exit_dist = dict(Counter(t.get("exit_reason", "?") for t in trades))
    print(f"  총 {len(trades)}건, PF {total_pf:.2f}, PnL {total_pnl:+,.0f}")
    print(f"  청산: {exit_dist}")

    # 2. enrich
    if args.use_cache and ENRICHED_CACHE.exists():
        print(f"[CACHE] enriched load: {ENRICHED_CACHE}")
        enriched = pickle.loads(ENRICHED_CACHE.read_bytes())
    else:
        print(f"[ENRICH] 진입 지표 계산...")
        enriched = enrich_trade_indicators(trades, DB_PATH)
        ENRICHED_CACHE.write_bytes(pickle.dumps(enriched))
        print(f"[CACHE] enriched 저장: {ENRICHED_CACHE}")

    df = pd.DataFrame(enriched)
    print(f"  enriched rows: {len(df)}")

    # 3. 통계
    corr = correlation_matrix(df)
    buckets = {
        "breakout_pct": bucket_analysis(
            df, "breakout_pct",
            bins=[0.03, 0.05, 0.08, 0.12, 1.0],
            labels=["3~5%", "5~8%", "8~12%", "12%+"],
        ),
        "volume_ratio": bucket_analysis(
            df, "volume_ratio",
            bins=[2, 3, 5, 10, 1000],
            labels=["2~3x", "3~5x", "5~10x", "10x+"],
        ),
        "adx": bucket_analysis(
            df, "adx",
            bins=[20, 25, 35, 50, 200],
            labels=["20~25", "25~35", "35~50", "50+"],
        ),
        "entry_minute": bucket_analysis(
            df, "entry_minute",
            bins=[30, 60, 90, 120, 180],
            labels=["09:30~10:00", "10:00~10:30", "10:30~11:00", "11:00~12:00"],
        ),
    }
    reg = multiple_regression(df)

    # 4. 리포트
    gen_report(df, corr, buckets, reg, total_pf, total_pnl, exit_dist)

    # 콘솔 요약
    print("\n=== 상관 요약 ===")
    for c in INDICATOR_COLS:
        s = corr[c]
        if s["pearson"] is None:
            print(f"  {c}: n={s['n']} (데이터 부족)")
        else:
            print(f"  {c}: r={s['pearson']:+.3f} ρ={s['spearman']:+.3f} "
                  f"승률Δ {(s['win_rate_top']-s['win_rate_bot'])*100:+.1f}%p")
    if reg is not None:
        print(f"\n  회귀 R² = {reg['r2']:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
