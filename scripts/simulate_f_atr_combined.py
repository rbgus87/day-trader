"""scripts/simulate_f_atr_combined.py — F(Pure trailing) + ATR 필터 결합 시뮬.

시나리오 4종:
  H: F(60종목, control)
  I: F + ATR >= 6%  (41종목)
  J: F + ATR >= 7%  (35종목)
  K: F + ATR >= 8%  (24종목)

사용:
    python scripts/simulate_f_atr_combined.py
"""

import argparse
import asyncio
import os
import pickle
import sqlite3
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from loguru import logger

logger.remove()

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig
from core.cost_model import TradeCosts, apply_buy_costs, apply_sell_costs
from core.indicators import calculate_atr_trailing_stop, get_latest_atr
from data.db_manager import DbManager

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False

CHARTS_DIR = Path("reports/charts")
REPORT_PATH = Path("reports/f_atr_combined.md")
DB_PATH = "daytrader.db"


# ======================================================================
# F 시나리오 백테스트 (simulate_tp1_scenarios.py에서 추출)
# ======================================================================

def run_f_scenario_day(day_candles, strategy, costs, trading_config, ticker):
    """F(Pure trailing) 하루 백테스트."""
    from strategy.base_strategy import Signal

    candles = day_candles.reset_index(drop=True)
    if candles.empty:
        return []

    trades = []
    position = None

    as_of = None
    try:
        as_of = pd.to_datetime(candles["ts"].iloc[0]).strftime("%Y-%m-%d")
    except Exception:
        pass
    atr_pct = get_latest_atr(DB_PATH, ticker, as_of)

    trail_mult = getattr(trading_config, "atr_trail_multiplier", 2.5)
    trail_min = getattr(trading_config, "atr_trail_min_pct", 0.02)
    trail_max = getattr(trading_config, "atr_trail_max_pct", 0.10)
    trailing_pct_fb = getattr(trading_config, "trailing_stop_pct", 0.005)

    def calc_trail(peak):
        if atr_pct is not None:
            return calculate_atr_trailing_stop(peak, atr_pct, trail_mult, trail_min, trail_max)
        return peak * (1 - trailing_pct_fb)

    for idx, row in candles.iterrows():
        ts = row["ts"]
        if hasattr(ts, "time"):
            strategy.set_backtest_time(ts.time())

        tick = {
            "ticker": "BACKTEST",
            "price": float(row["close"]),
            "time": ts.strftime("%H%M") if hasattr(ts, "strftime") else str(ts)[11:16].replace(":", ""),
            "volume": int(row.get("volume", 0)),
        }
        candles_so_far = candles.iloc[:idx + 1]

        if position is None:
            signal = strategy.generate_signal(candles_so_far, tick)
            if signal is not None and signal.side == "buy":
                strategy.on_entry()
                entry_raw = float(row["close"])
                entry_price, net_entry = apply_buy_costs(entry_raw, costs)
                stop_loss = strategy.get_stop_loss(entry_price)

                position = {
                    "entry_ts": row["ts"],
                    "entry_price": entry_price,
                    "net_entry": net_entry,
                    "stop_loss": stop_loss,
                    "highest_price": float(row["high"]),
                }
                # F: 즉시 trailing
                new_stop = calc_trail(position["highest_price"])
                position["stop_loss"] = max(position["stop_loss"], new_stop)
            continue

        low = float(row["low"])
        high = float(row["high"])
        close = float(row["close"])
        is_last = idx == len(candles) - 1

        # 고점 갱신 + trailing 재계산
        if high > position["highest_price"]:
            position["highest_price"] = high
            new_stop = calc_trail(position["highest_price"])
            position["stop_loss"] = max(position["stop_loss"], new_stop)

        # 손절/trailing 체크
        if low <= position["stop_loss"]:
            exit_p, net_exit = apply_sell_costs(position["stop_loss"], costs)
            pnl = net_exit - position["net_entry"]
            pnl_pct = pnl / position["net_entry"]
            # trailing이 초기 stop보다 높으면 trailing_stop, 아니면 stop_loss
            reason = "trailing_stop" if position["stop_loss"] > position["entry_price"] * 0.975 else "stop_loss"
            trades.append({
                "entry_ts": position["entry_ts"], "exit_ts": row["ts"],
                "entry_price": position["entry_price"], "exit_price": exit_p,
                "pnl": pnl, "pnl_pct": pnl_pct, "exit_reason": reason,
            })
            position = None
            strategy.on_exit()
            continue

        if is_last:
            exit_p, net_exit = apply_sell_costs(close, costs)
            pnl = net_exit - position["net_entry"]
            pnl_pct = pnl / position["net_entry"]
            trades.append({
                "entry_ts": position["entry_ts"], "exit_ts": row["ts"],
                "entry_price": position["entry_price"], "exit_price": exit_p,
                "pnl": pnl, "pnl_pct": pnl_pct, "exit_reason": "forced_close",
            })
            position = None
            strategy.on_exit()

    strategy.set_backtest_time(None)
    return trades


def run_f_multi_day(ticker, all_candles, trading_config, backtest_config, ticker_market, market_map):
    """다일 F 시나리오 백테스트."""
    from strategy.momentum_strategy import MomentumStrategy

    costs = TradeCosts(
        commission_rate=backtest_config.commission,
        slippage_rate=backtest_config.slippage,
        tax_rate=backtest_config.tax,
    )
    strategy = MomentumStrategy(trading_config)

    if all_candles.empty:
        return []

    df = all_candles.copy()
    if "date" not in df.columns:
        df["date"] = df["ts"].dt.date

    all_trades = []
    prev_day_df = None

    mf_enabled = getattr(trading_config, "market_filter_enabled", False)
    bl_enabled = getattr(trading_config, "blacklist_enabled", False)
    bl_lookback = getattr(trading_config, "blacklist_lookback_days", 5)
    bl_thresh = getattr(trading_config, "blacklist_loss_threshold", 3)
    rest_enabled = getattr(trading_config, "consecutive_loss_rest_enabled", False)
    rest_thresh = getattr(trading_config, "consecutive_loss_threshold", 3)
    daily_pnl = {}

    for date, day_candles in df.groupby("date"):
        day_df = day_candles.drop(columns=["date"]).reset_index(drop=True)

        skip = False
        if mf_enabled and ticker_market in ("kospi", "kosdaq"):
            dk = date.strftime("%Y%m%d")
            strong = market_map.get(dk)
            if strong is not None and not strong.get(ticker_market, True):
                skip = True

        if not skip and bl_enabled:
            from datetime import timedelta
            cutoff = date - timedelta(days=bl_lookback)
            losses = sum(1 for t in all_trades if t.get("pnl", 0) < 0
                         and _edate(t) is not None and cutoff <= _edate(t) < date)
            if losses >= bl_thresh:
                skip = True

        if not skip and rest_enabled:
            past = sorted((d for d in daily_pnl if d < date), reverse=True)
            consec = 0
            for d in past:
                if daily_pnl[d] < 0:
                    consec += 1
                else:
                    break
            if consec >= rest_thresh:
                skip = True

        if skip:
            prev_day_df = day_df
            continue

        strategy.reset()
        if hasattr(strategy, "set_ticker"):
            strategy.set_ticker(ticker)
        if hasattr(strategy, "set_prev_day_data") and prev_day_df is not None:
            strategy.set_prev_day_data(float(prev_day_df["high"].max()), int(prev_day_df["volume"].sum()))

        day_trades = run_f_scenario_day(day_df, strategy, costs, trading_config, ticker)
        for t in day_trades:
            t["ticker"] = ticker
            t["ticker_market"] = ticker_market
        all_trades.extend(day_trades)
        daily_pnl[date] = sum(t.get("pnl", 0) for t in day_trades)
        prev_day_df = day_df

    return all_trades


def _edate(t):
    ex = t.get("exit_ts")
    if ex is None:
        return None
    try:
        return ex.date() if hasattr(ex, "date") else pd.to_datetime(ex).date()
    except Exception:
        return None


# ======================================================================
# 워커
# ======================================================================

def _worker(args):
    ticker, ticker_market, candles_pickle, trading_config, backtest_config, market_map = args
    candles = pickle.loads(candles_pickle)
    trades = run_f_multi_day(ticker, candles, trading_config, backtest_config, ticker_market, market_map)
    return ticker, trades


# ======================================================================
# 분석 함수
# ======================================================================

def compute_scenario_metrics(trades, regime_map):
    """단일 시나리오의 종합 메트릭."""
    total = len(trades)
    if total == 0:
        return {"trades": 0, "pf": 0, "pnl": 0, "per_trade": 0, "exit_dist": {},
                "avg_hold_min": 0, "max_dd": 0, "regime_pf": {},
                "trail_rate": 0, "fc_rate": 0, "occupy_rate": 0, "occupy_count": 0,
                "pf_below1_tickers": 0, "total_tickers": 0}

    pnl_list = [t["pnl"] for t in trades]
    gp = sum(p for p in pnl_list if p > 0)
    gl = abs(sum(p for p in pnl_list if p < 0))
    pf = gp / gl if gl > 0 else float("inf")

    exit_dist = Counter(t.get("exit_reason", "?") for t in trades)

    # 보유시간
    holds = []
    for t in trades:
        try:
            holds.append((pd.to_datetime(t["exit_ts"]) - pd.to_datetime(t["entry_ts"])).total_seconds() / 60)
        except Exception:
            pass

    # Max DD
    cum = peak = max_dd = 0.0
    for p in pnl_list:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    # 자리 점유: forced_close 중 |PnL%| < 0.5%
    fc_trades = [t for t in trades if t.get("exit_reason") == "forced_close"]
    occupy = [t for t in fc_trades if abs(t.get("pnl_pct", 0)) < 0.005]
    occupy_rate = len(occupy) / len(fc_trades) if fc_trades else 0

    # 국면별 PF
    regime_trades = defaultdict(list)
    for t in trades:
        try:
            month = pd.to_datetime(t["entry_ts"]).strftime("%Y-%m")
            regime_trades[regime_map.get(month, "?")].append(t)
        except Exception:
            pass
    regime_pf = {}
    for r, rt in regime_trades.items():
        r_gp = sum(t["pnl"] for t in rt if t["pnl"] > 0)
        r_gl = abs(sum(t["pnl"] for t in rt if t["pnl"] < 0))
        regime_pf[r] = r_gp / r_gl if r_gl > 0 else float("inf")

    # 종목별 PF<1 수
    ticker_pnl = defaultdict(lambda: {"gp": 0, "gl": 0})
    for t in trades:
        tk = t.get("ticker", "?")
        if t["pnl"] > 0:
            ticker_pnl[tk]["gp"] += t["pnl"]
        elif t["pnl"] < 0:
            ticker_pnl[tk]["gl"] += abs(t["pnl"])
    pf_below1 = sum(1 for v in ticker_pnl.values()
                    if v["gl"] > 0 and v["gp"] / v["gl"] < 1.0)

    return {
        "trades": total, "pf": pf, "pnl": sum(pnl_list),
        "per_trade": sum(pnl_list) / total,
        "exit_dist": dict(exit_dist),
        "avg_hold_min": np.mean(holds) if holds else 0,
        "max_dd": max_dd,
        "regime_pf": regime_pf,
        "trail_rate": (exit_dist.get("trailing_stop", 0)) / total,
        "fc_rate": exit_dist.get("forced_close", 0) / total,
        "occupy_rate": occupy_rate,
        "occupy_count": len(occupy),
        "fc_count": len(fc_trades),
        "pf_below1_tickers": pf_below1,
        "total_tickers": len(ticker_pnl),
    }


def build_regime_map(db_path):
    conn = sqlite3.connect(db_path)
    regime_map = {}
    for code, name in [("001", "kospi"), ("101", "kosdaq")]:
        cur = conn.execute("SELECT dt, close FROM index_candles WHERE index_code=? ORDER BY dt", (code,))
        rows = cur.fetchall()
        monthly = {}
        for dt, close in rows:
            ym = dt[:4] + "-" + dt[4:6]
            if ym not in monthly:
                monthly[ym] = {"first": close, "last": close}
            monthly[ym]["last"] = close
        for ym, v in monthly.items():
            ret = (v["last"] - v["first"]) / v["first"]
            regime_map.setdefault(ym, []).append(ret)
    conn.close()
    result = {}
    for ym, rets in regime_map.items():
        avg = np.mean(rets)
        result[ym] = "강세" if avg >= 0.05 else ("약세" if avg <= -0.05 else "횡보")
    return result


# ======================================================================
# 차트 + 보고서
# ======================================================================

def generate_charts(scenarios, metrics, all_results):
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    names = [s[0] for s in scenarios]
    descs = {s[0]: s[1] for s in scenarios}

    # 1. PF + 거래당 PnL 비교
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    pfs = [min(metrics[n]["pf"], 10) for n in names]
    colors = ["#2196F3", "#4CAF50", "#8BC34A", "#FF9800"]
    ax1.bar(names, pfs, color=colors[:len(names)], edgecolor="white", width=0.5)
    ax1.axhline(y=metrics["H"]["pf"], color="blue", linestyle="--", alpha=0.4, label=f'H(control) PF={metrics["H"]["pf"]:.2f}')
    for i, (n, pf_val) in enumerate(zip(names, pfs)):
        m = metrics[n]
        ax1.text(i, pf_val + 0.03, f"PF={pf_val:.2f}\nn={m['trades']}", ha="center", fontsize=10)
    ax1.set_ylabel("Profit Factor")
    ax1.set_title("PF 비교", fontsize=13, fontweight="bold")
    ax1.legend()

    pts = [metrics[n]["per_trade"] for n in names]
    ax2.bar(names, pts, color=colors[:len(names)], edgecolor="white", width=0.5)
    for i, (n, pt) in enumerate(zip(names, pts)):
        ax2.text(i, pt + 10, f"{pt:+,.0f}", ha="center", fontsize=10)
    ax2.set_ylabel("거래당 PnL (원)")
    ax2.set_title("거래당 PnL 비교", fontsize=13, fontweight="bold")
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "f_atr_pf_comparison.png", dpi=150, bbox_inches="tight")
    plt.close()

    # 2. 누적 PnL 곡선
    fig, ax = plt.subplots(figsize=(14, 6))
    styles = ["-", "--", "-.", ":"]
    for i, n in enumerate(names):
        trades = all_results[n]
        if not trades:
            continue
        cum = np.cumsum([t["pnl"] for t in trades])
        ax.plot(range(len(cum)), cum, label=f"{n}: {descs[n]}", linestyle=styles[i], linewidth=1.5, color=colors[i])
    ax.set_xlabel("거래 번호")
    ax.set_ylabel("누적 PnL (원)")
    ax.set_title("누적 PnL 곡선 (F + ATR 필터)", fontsize=14, fontweight="bold")
    ax.axhline(y=0, color="gray", alpha=0.3)
    ax.legend(fontsize=9)
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "f_atr_cumulative_pnl.png", dpi=150, bbox_inches="tight")
    plt.close()

    # 3. 자리 점유 비율 + 청산 분포
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    occ_rates = [metrics[n]["occupy_rate"] * 100 for n in names]
    fc_rates = [metrics[n]["fc_rate"] * 100 for n in names]
    x = range(len(names))
    bars = ax1.bar(names, occ_rates, color=["#F44336" if r > 40 else "#FF9800" if r > 25 else "#4CAF50" for r in occ_rates],
                   edgecolor="white", width=0.5)
    for bar, val, n in zip(bars, occ_rates, names):
        m = metrics[n]
        ax1.text(bar.get_x() + bar.get_width() / 2, val + 1,
                 f"{val:.0f}%\n({m['occupy_count']}/{m['fc_count']})", ha="center", fontsize=9)
    ax1.set_ylabel("비율 (%)")
    ax1.set_title("자리 점유 비율 (forced_close 중 |PnL|<0.5%)", fontsize=11, fontweight="bold")
    ax1.axhline(y=30, color="green", linestyle="--", alpha=0.4, label="목표 <30%")
    ax1.legend()

    reasons = ["forced_close", "stop_loss", "trailing_stop"]
    reason_colors = {"forced_close": "#9E9E9E", "stop_loss": "#F44336", "trailing_stop": "#4CAF50"}
    bottoms = np.zeros(len(names))
    for reason in reasons:
        vals = [metrics[n]["exit_dist"].get(reason, 0) / max(metrics[n]["trades"], 1) * 100 for n in names]
        ax2.bar(names, vals, bottom=bottoms, label=reason, color=reason_colors[reason], width=0.5)
        bottoms += np.array(vals)
    ax2.set_ylabel("비율 (%)")
    ax2.set_title("청산 분포", fontsize=13, fontweight="bold")
    ax2.legend()
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "f_atr_occupy_exit.png", dpi=150, bbox_inches="tight")
    plt.close()

    # 4. 시장 국면별 PF
    fig, ax = plt.subplots(figsize=(12, 5))
    regimes = ["강세", "횡보", "약세"]
    x = np.arange(len(names))
    width = 0.25
    rc = {"강세": "#4CAF50", "횡보": "#FF9800", "약세": "#F44336"}
    for j, regime in enumerate(regimes):
        vals = [min(metrics[n]["regime_pf"].get(regime, 0), 10) for n in names]
        ax.bar(x + j * width, vals, width, label=regime, color=rc[regime], alpha=0.7)
    ax.set_xticks(x + width)
    ax.set_xticklabels([f"{n}\n({descs[n][:15]})" for n in names], fontsize=9)
    ax.set_ylabel("Profit Factor")
    ax.set_title("시장 국면별 PF (F + ATR 필터)", fontsize=13, fontweight="bold")
    ax.axhline(y=1.0, color="red", linestyle="--", alpha=0.3)
    ax.legend()
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "f_atr_regime_pf.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  차트 4개 생성")


def generate_report(scenarios, metrics, all_results):
    lines = []
    a = lines.append
    names = [s[0] for s in scenarios]
    descs = {s[0]: s[1] for s in scenarios}
    tickers_count = {s[0]: s[2] for s in scenarios}

    a("# F(Pure Trailing) + ATR 필터 결합 검증")
    a("")
    a(f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    a(f"> Control(H) PF: {metrics['H']['pf']:.2f} / {metrics['H']['trades']}건")
    a("")
    a("---")
    a("")

    # 요약 매트릭스
    a("## 요약 매트릭스")
    a("")
    a("| 시나리오 | 종목수 | PF | 거래수 | 총 PnL | 거래당 PnL | trailing% | forced_close% | 자리 점유% | Max DD |")
    a("|---------|--------|-----|--------|--------|-----------|-----------|---------------|----------|--------|")
    for n in names:
        m = metrics[n]
        pf_s = f"{m['pf']:.2f}" if m["pf"] != float("inf") else "∞"
        a(f"| **{n}** ({descs[n][:20]}) | {tickers_count[n]} | {pf_s} | {m['trades']} | "
          f"{m['pnl']:+,.0f} | {m['per_trade']:+,.0f} | {m['trail_rate']*100:.1f}% | "
          f"{m['fc_rate']*100:.1f}% | {m['occupy_rate']*100:.1f}% | {m['max_dd']:,.0f} |")
    a("")
    a("![PF 비교](charts/f_atr_pf_comparison.png)")
    a("")
    a("![누적 PnL](charts/f_atr_cumulative_pnl.png)")
    a("")
    a("---")
    a("")

    # 청산 분포
    a("## 청산 분포")
    a("")
    a("| 시나리오 | forced_close | stop_loss | trailing_stop |")
    a("|---------|-------------|-----------|--------------|")
    for n in names:
        m = metrics[n]
        total = m["trades"] or 1
        ed = m["exit_dist"]
        a(f"| {n} | {ed.get('forced_close',0)} ({ed.get('forced_close',0)/total*100:.0f}%) | "
          f"{ed.get('stop_loss',0)} ({ed.get('stop_loss',0)/total*100:.0f}%) | "
          f"{ed.get('trailing_stop',0)} ({ed.get('trailing_stop',0)/total*100:.0f}%) |")
    a("")
    a("---")
    a("")

    # 자리 점유
    a("## 자리 점유 분석")
    a("")
    a("자리 점유 = forced_close 중 |PnL%| < 0.5% 비율")
    a("")
    a("| 시나리오 | forced_close | 자리 점유 | 비율 |")
    a("|---------|-------------|----------|------|")
    for n in names:
        m = metrics[n]
        a(f"| {n} | {m['fc_count']} | {m['occupy_count']} | **{m['occupy_rate']*100:.1f}%** |")
    a("")

    h_occ = metrics["H"]["occupy_rate"] * 100
    best_occ = min(names, key=lambda n: metrics[n]["occupy_rate"])
    best_occ_val = metrics[best_occ]["occupy_rate"] * 100
    if best_occ_val < 30:
        a(f"ATR 필터 강화로 자리 점유 {h_occ:.0f}% → {best_occ_val:.0f}% ({best_occ}) — **목표 <30% 달성**")
    elif best_occ_val < h_occ - 5:
        a(f"ATR 필터 강화로 자리 점유 {h_occ:.0f}% → {best_occ_val:.0f}% ({best_occ}) — 개선되었으나 목표 미달")
    else:
        a(f"ATR 필터 강화로도 자리 점유 변화 미미 ({h_occ:.0f}% → {best_occ_val:.0f}%) — 별도 대응 필요")
    a("")
    a("![자리 점유 + 청산](charts/f_atr_occupy_exit.png)")
    a("")
    a("---")
    a("")

    # 시장 국면
    a("## 시장 국면별 PF")
    a("")
    a("| 시나리오 | 강세 | 횡보 | 약세 |")
    a("|---------|------|------|------|")
    for n in names:
        rpf = metrics[n]["regime_pf"]
        def _f(r):
            v = rpf.get(r, 0)
            return f"{v:.2f}" if v != float("inf") else "∞"
        a(f"| {n} | {_f('강세')} | {_f('횡보')} | {_f('약세')} |")
    a("")
    a("![국면별 PF](charts/f_atr_regime_pf.png)")
    a("")
    a("---")
    a("")

    # 종목 품질
    a("## 종목 품질")
    a("")
    a("| 시나리오 | 활성 종목 | PF<1 종목 | PF<1 비율 |")
    a("|---------|----------|----------|----------|")
    for n in names:
        m = metrics[n]
        ratio = m["pf_below1_tickers"] / m["total_tickers"] * 100 if m["total_tickers"] else 0
        a(f"| {n} | {m['total_tickers']} | {m['pf_below1_tickers']} | {ratio:.0f}% |")
    a("")
    a("---")
    a("")

    # 결론
    a("## 결론 + 최종 권장")
    a("")
    best_pf = max(names, key=lambda n: metrics[n]["pf"] if metrics[n]["trades"] > 30 else 0)
    best_pt = max(names, key=lambda n: metrics[n]["per_trade"] if metrics[n]["trades"] > 30 else -9999)
    a(f"**PF 최고**: {best_pf} ({descs[best_pf]}) — PF {metrics[best_pf]['pf']:.2f}")
    a(f"**거래당 PnL 최고**: {best_pt} ({descs[best_pt]}) — {metrics[best_pt]['per_trade']:+,.0f}원/건")
    a("")

    # 트레이드오프 분석
    a("### 트레이드오프")
    a("")
    for n in names[1:]:
        m = metrics[n]
        h = metrics["H"]
        delta_pf = m["pf"] - h["pf"]
        delta_trades = m["trades"] - h["trades"]
        delta_pnl = m["pnl"] - h["pnl"]
        a(f"- **H→{n}**: PF {h['pf']:.2f}→{m['pf']:.2f} ({delta_pf:+.2f}), "
          f"거래수 {delta_trades:+d}, PnL {delta_pnl:+,.0f}")
    a("")

    # 채택 권장
    a("### 채택 시나리오")
    a("")
    best = best_pf
    bm = metrics[best]
    a(f"**권장: {best} ({descs[best]})**")
    a("")
    a(f"- PF: {bm['pf']:.2f} (H 대비 {bm['pf'] - metrics['H']['pf']:+.2f})")
    a(f"- 거래수: {bm['trades']}건")
    a(f"- 거래당 PnL: {bm['per_trade']:+,.0f}원")
    a(f"- 자리 점유: {bm['occupy_rate']*100:.0f}%")
    a("")

    # 다음 단계
    a("### 다음 단계")
    a("")
    if bm["occupy_rate"] < 0.30:
        a("1. 자리 점유 < 30% → 시간 컷 추가 불필요")
    else:
        a("1. 자리 점유 여전히 높음 → 조건부 시간 컷 또는 trail 파라미터 추가 조정")
    a("2. trail multiplier 그리드 서치 (1.0/1.5/2.0/2.5) — trailing_stop 빈도 최적화")
    a("3. 채택 시나리오 config.yaml 반영 + backtester 코드 변경")
    a("")

    report = "\n".join(lines)
    REPORT_PATH.write_text(report, encoding="utf-8")
    return report


# ======================================================================
# main
# ======================================================================

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-04-01")
    parser.add_argument("--end", default="2026-04-15")
    args = parser.parse_args()

    CHARTS_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 64)
    print(" F(Pure Trailing) + ATR 필터 결합 시뮬레이션")
    print("=" * 64)

    # ATR 필터: 최신 ATR% 기준
    conn = sqlite3.connect(DB_PATH)
    uni = yaml.safe_load(open("config/universe.yaml", encoding="utf-8"))
    stocks = uni.get("stocks", [])
    ticker_to_market = {s["ticker"]: s.get("market", "unknown") for s in stocks}
    all_tickers = [s["ticker"] for s in stocks]

    ticker_atr_latest = {}
    for tk in all_tickers:
        cur = conn.execute("SELECT atr_pct FROM ticker_atr WHERE ticker=? ORDER BY dt DESC LIMIT 1", (tk,))
        row = cur.fetchone()
        ticker_atr_latest[tk] = row[0] if row else 0
    conn.close()

    # 시나리오별 종목 리스트
    scenario_tickers = {
        "H": set(all_tickers),
        "I": {tk for tk, atr in ticker_atr_latest.items() if atr >= 6.0},
        "J": {tk for tk, atr in ticker_atr_latest.items() if atr >= 7.0},
        "K": {tk for tk, atr in ticker_atr_latest.items() if atr >= 8.0},
    }

    scenarios = [
        ("H", "F control (60종목)", len(scenario_tickers["H"])),
        ("I", "F + ATR≥6%", len(scenario_tickers["I"])),
        ("J", "F + ATR≥7%", len(scenario_tickers["J"])),
        ("K", "F + ATR≥8%", len(scenario_tickers["K"])),
    ]

    for name, desc, cnt in scenarios:
        print(f"  {name}: {desc} ({cnt}종목)")
    print()

    # 데이터 로드
    app_config = AppConfig.from_yaml()
    base_config = app_config.trading
    bt_cfg_raw = yaml.safe_load(open("config.yaml", encoding="utf-8")).get("backtest", {})
    backtest_config = BacktestConfig(
        commission=bt_cfg_raw.get("commission", 0.00015),
        tax=bt_cfg_raw.get("tax", 0.0018),
        slippage=bt_cfg_raw.get("slippage", 0.0003),
    )

    db = DbManager(app_config.db_path)
    await db.init()
    bt_loader = Backtester(db=db, config=base_config, backtest_config=backtest_config)

    print("[LOAD] 캔들 로딩...")
    candles_cache = {}
    for tk in all_tickers:
        candles = await bt_loader.load_candles(tk, args.start, f"{args.end} 23:59:59")
        if not candles.empty:
            candles_cache[tk] = pickle.dumps(candles)
    print(f"  로드 {len(candles_cache)}/{len(all_tickers)}")
    await db.close()

    market_map = build_market_strong_by_date(app_config.db_path, ma_length=base_config.market_ma_length)
    workers = max(2, (os.cpu_count() or 2) - 1)

    # 시나리오별 실행
    all_results = {}
    for sc_name, sc_desc, sc_cnt in scenarios:
        tickers_for_sc = scenario_tickers[sc_name]
        tasks = [
            (tk, ticker_to_market.get(tk, "unknown"),
             candles_cache[tk], base_config, backtest_config, market_map)
            for tk in tickers_for_sc if tk in candles_cache
        ]

        sc_trades = []
        with ProcessPoolExecutor(max_workers=workers) as executor:
            for ticker, trades in executor.map(_worker, tasks):
                sc_trades.extend(trades)

        total = len(sc_trades)
        pnl = sum(t["pnl"] for t in sc_trades)
        gp = sum(t["pnl"] for t in sc_trades if t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in sc_trades if t["pnl"] < 0))
        pf = gp / gl if gl > 0 else 0
        print(f"  [{sc_name}] {sc_desc:25s} | n={total:>4} PF={pf:>5.2f} PnL={pnl:>+10,.0f}")

        all_results[sc_name] = sc_trades

    # H 검증
    h_trades = len(all_results["H"])
    h_gp = sum(t["pnl"] for t in all_results["H"] if t["pnl"] > 0)
    h_gl = abs(sum(t["pnl"] for t in all_results["H"] if t["pnl"] < 0))
    h_pf = h_gp / h_gl if h_gl else 0
    print(f"\n  [검증] H(control): {h_trades}건, PF={h_pf:.2f}")
    if abs(h_pf - 2.68) > 0.15 or abs(h_trades - 332) > 10:
        print(f"  ⚠ F 재현 불일치 (기대: ~332건/PF ~2.68)")

    # 분석
    regime_map = build_regime_map(DB_PATH)
    metrics = {n: compute_scenario_metrics(all_results[n], regime_map) for n in [s[0] for s in scenarios]}

    print()
    generate_charts(scenarios, metrics, all_results)
    generate_report(scenarios, metrics, all_results)
    print(f"\n[REPORT] {REPORT_PATH}")
    print("\n" + "=" * 64)


if __name__ == "__main__":
    asyncio.run(main())
