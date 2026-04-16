"""scripts/simulate_capital_multiplier.py — 시나리오 I × 자본/multiplier 그리드.

시뮬 1: 자본 4종 (100/200/300/500만) × ATR≥6% × mult 2.5
시뮬 2: mult 5종 (1.0/1.5/2.0/2.5/3.0) × ATR≥6% × 자본 100만
총 8개 시나리오 (I-100/I-M25 중복 → 8개)

사용:
    python scripts/simulate_capital_multiplier.py
"""

import argparse
import asyncio
import os
import pickle
import sqlite3
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
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
REPORT_PATH = Path("reports/capital_multiplier_grid.md")
DB_PATH = "daytrader.db"


# ======================================================================
# 시나리오 정의
# ======================================================================

@dataclass
class GridConfig:
    name: str
    desc: str
    capital: int
    max_positions: int
    trail_mult: float
    trail_min: float
    trail_max: float


def build_scenarios():
    base_mult = 2.5
    base_cap = 1_000_000
    max_pos = 3

    scenarios = []

    # 시뮬 1: 자본 그리드 (mult 2.5 고정)
    for cap, label in [(1_000_000, "100만"), (2_000_000, "200만"),
                       (3_000_000, "300만"), (5_000_000, "500만")]:
        scenarios.append(GridConfig(
            f"C{cap//1_000_000}", f"자본 {label}", cap, max_pos, base_mult, 0.02, 0.10))

    # 시뮬 2: multiplier 그리드 (자본 100만 고정)
    for mult in [1.0, 1.5, 2.0, 3.0]:  # 2.5는 C1과 동일 → 제외
        scenarios.append(GridConfig(
            f"M{mult:.1f}".replace(".", ""), f"mult {mult}", base_cap, max_pos, mult, 0.02, 0.10))

    return scenarios


# ======================================================================
# F 시나리오 백테스트 (자본 사이징 추가)
# ======================================================================

def run_f_day_with_capital(day_candles, strategy, costs, trading_config,
                           ticker, gc: GridConfig):
    """F(Pure trailing) + 자본 사이징 하루 백테스트."""
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
    trailing_pct_fb = getattr(trading_config, "trailing_stop_pct", 0.005)

    def calc_trail(peak):
        if atr_pct is not None:
            return calculate_atr_trailing_stop(peak, atr_pct, gc.trail_mult, gc.trail_min, gc.trail_max)
        return peak * (1 - trailing_pct_fb)

    per_position_budget = gc.capital / gc.max_positions

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
                entry_raw = float(row["close"])
                entry_price, net_entry = apply_buy_costs(entry_raw, costs)

                # 자본 사이징: qty 계산
                qty = int(per_position_budget / entry_price)
                if qty <= 0:
                    continue  # 1주도 못 삼

                strategy.on_entry()
                stop_loss = strategy.get_stop_loss(entry_price)
                buy_amount = net_entry * qty

                position = {
                    "entry_ts": row["ts"],
                    "entry_price": entry_price,
                    "net_entry": net_entry,
                    "stop_loss": stop_loss,
                    "highest_price": float(row["high"]),
                    "qty": qty,
                    "buy_amount": buy_amount,
                }
                new_stop = calc_trail(position["highest_price"])
                position["stop_loss"] = max(position["stop_loss"], new_stop)
            continue

        low = float(row["low"])
        high = float(row["high"])
        close = float(row["close"])
        is_last = idx == len(candles) - 1
        qty = position["qty"]

        # 고점 갱신 + trailing
        if high > position["highest_price"]:
            position["highest_price"] = high
            new_stop = calc_trail(position["highest_price"])
            position["stop_loss"] = max(position["stop_loss"], new_stop)

        if low <= position["stop_loss"]:
            exit_p, net_exit = apply_sell_costs(position["stop_loss"], costs)
            pnl = (net_exit - position["net_entry"]) * qty
            pnl_pct = (net_exit - position["net_entry"]) / position["net_entry"]
            reason = "trailing_stop" if position["stop_loss"] > position["entry_price"] * 0.975 else "stop_loss"
            trades.append({
                "entry_ts": position["entry_ts"], "exit_ts": row["ts"],
                "entry_price": position["entry_price"], "exit_price": exit_p,
                "pnl": pnl, "pnl_pct": pnl_pct, "exit_reason": reason,
                "qty": qty, "buy_amount": position["buy_amount"],
            })
            position = None
            strategy.on_exit()
            continue

        if is_last:
            exit_p, net_exit = apply_sell_costs(close, costs)
            pnl = (net_exit - position["net_entry"]) * qty
            pnl_pct = (net_exit - position["net_entry"]) / position["net_entry"]
            trades.append({
                "entry_ts": position["entry_ts"], "exit_ts": row["ts"],
                "entry_price": position["entry_price"], "exit_price": exit_p,
                "pnl": pnl, "pnl_pct": pnl_pct, "exit_reason": "forced_close",
                "qty": qty, "buy_amount": position["buy_amount"],
            })
            position = None
            strategy.on_exit()

    strategy.set_backtest_time(None)
    return trades


def run_f_multi_day_grid(ticker, all_candles, trading_config, backtest_config,
                         ticker_market, market_map, gc: GridConfig):
    """다일 F + 자본 사이징."""
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

        day_trades = run_f_day_with_capital(day_df, strategy, costs, trading_config, ticker, gc)
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
    ticker, ticker_market, candles_pickle, trading_config, backtest_config, market_map, gc_dict = args
    candles = pickle.loads(candles_pickle)
    gc = GridConfig(**gc_dict)
    trades = run_f_multi_day_grid(ticker, candles, trading_config, backtest_config,
                                  ticker_market, market_map, gc)
    return ticker, trades


# ======================================================================
# 분석
# ======================================================================

def compute_metrics(trades, regime_map, capital):
    total = len(trades)
    if total == 0:
        return {"trades": 0, "pf": 0, "pnl": 0, "per_trade": 0, "exit_dist": {},
                "avg_hold_min": 0, "max_dd": 0, "regime_pf": {}, "trail_rate": 0,
                "fc_rate": 0, "occupy_rate": 0, "occupy_count": 0, "fc_count": 0,
                "capital_util": 0, "skipped_trades": 0, "avg_qty": 0,
                "pnl_pct_on_capital": 0}

    pnl_list = [t["pnl"] for t in trades]
    gp = sum(p for p in pnl_list if p > 0)
    gl = abs(sum(p for p in pnl_list if p < 0))
    pf = gp / gl if gl > 0 else float("inf")
    total_pnl = sum(pnl_list)

    exit_dist = Counter(t.get("exit_reason", "?") for t in trades)

    holds = []
    for t in trades:
        try:
            holds.append((pd.to_datetime(t["exit_ts"]) - pd.to_datetime(t["entry_ts"])).total_seconds() / 60)
        except Exception:
            pass

    cum = peak = max_dd = 0.0
    for p in pnl_list:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    fc_trades = [t for t in trades if t.get("exit_reason") == "forced_close"]
    occupy = [t for t in fc_trades if abs(t.get("pnl_pct", 0)) < 0.005]

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

    # 자본 활용률
    buy_amounts = [t.get("buy_amount", 0) for t in trades]
    avg_buy = np.mean(buy_amounts) if buy_amounts else 0
    capital_util = avg_buy / (capital / 3) if capital else 0  # per-position budget 대비

    avg_qty = np.mean([t.get("qty", 1) for t in trades])

    return {
        "trades": total, "pf": pf, "pnl": total_pnl,
        "per_trade": total_pnl / total,
        "exit_dist": dict(exit_dist),
        "avg_hold_min": np.mean(holds) if holds else 0,
        "max_dd": max_dd,
        "regime_pf": regime_pf,
        "trail_rate": exit_dist.get("trailing_stop", 0) / total,
        "fc_rate": exit_dist.get("forced_close", 0) / total,
        "occupy_rate": len(occupy) / len(fc_trades) if fc_trades else 0,
        "occupy_count": len(occupy), "fc_count": len(fc_trades),
        "capital_util": capital_util,
        "avg_qty": avg_qty,
        "pnl_pct_on_capital": total_pnl / capital * 100 if capital else 0,
    }


def build_regime_map(db_path):
    conn = sqlite3.connect(db_path)
    rm = {}
    for code in [("001", "kospi"), ("101", "kosdaq")]:
        cur = conn.execute("SELECT dt, close FROM index_candles WHERE index_code=? ORDER BY dt", (code[0],))
        rows = cur.fetchall()
        monthly = {}
        for dt, close in rows:
            ym = dt[:4] + "-" + dt[4:6]
            if ym not in monthly:
                monthly[ym] = {"first": close, "last": close}
            monthly[ym]["last"] = close
        for ym, v in monthly.items():
            ret = (v["last"] - v["first"]) / v["first"]
            rm.setdefault(ym, []).append(ret)
    conn.close()
    result = {}
    for ym, rets in rm.items():
        avg = np.mean(rets)
        result[ym] = "강세" if avg >= 0.05 else ("약세" if avg <= -0.05 else "횡보")
    return result


# ======================================================================
# 차트 + 보고서
# ======================================================================

def generate_all(cap_scenarios, mult_scenarios, cap_metrics, mult_metrics,
                 cap_results, mult_results):
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)

    # ── 차트 1: 자본별 PF + 거래당 PnL ──
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    caps = [s.capital // 1_000_000 for s in cap_scenarios]
    cap_names = [s.name for s in cap_scenarios]
    pfs = [cap_metrics[n]["pf"] for n in cap_names]
    pts = [cap_metrics[n]["per_trade"] for n in cap_names]

    ax1.plot(caps, pfs, "bo-", markersize=8, linewidth=2)
    for x, y, n in zip(caps, pfs, cap_names):
        ax1.annotate(f"PF={y:.2f}\nn={cap_metrics[n]['trades']}", (x, y),
                     textcoords="offset points", xytext=(0, 12), ha="center", fontsize=9)
    ax1.set_xlabel("자본 (백만원)")
    ax1.set_ylabel("Profit Factor")
    ax1.set_title("자본별 PF", fontsize=13, fontweight="bold")

    ax2.plot(caps, pts, "go-", markersize=8, linewidth=2)
    for x, y in zip(caps, pts):
        ax2.annotate(f"{y:+,.0f}", (x, y), textcoords="offset points", xytext=(0, 12),
                     ha="center", fontsize=9)
    ax2.set_xlabel("자본 (백만원)")
    ax2.set_ylabel("거래당 PnL (원)")
    ax2.set_title("자본별 거래당 PnL", fontsize=13, fontweight="bold")
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "grid_capital.png", dpi=150, bbox_inches="tight")
    plt.close()

    # ── 차트 2: multiplier별 PF + 청산 분포 ──
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    mults = [s.trail_mult for s in mult_scenarios]
    m_names = [s.name for s in mult_scenarios]
    m_pfs = [mult_metrics[n]["pf"] for n in m_names]

    ax1.plot(mults, m_pfs, "ro-", markersize=8, linewidth=2)
    for x, y, n in zip(mults, m_pfs, m_names):
        ax1.annotate(f"PF={y:.2f}\nn={mult_metrics[n]['trades']}", (x, y),
                     textcoords="offset points", xytext=(0, 12), ha="center", fontsize=9)
    ax1.set_xlabel("ATR Trail Multiplier")
    ax1.set_ylabel("Profit Factor")
    ax1.set_title("Multiplier별 PF", fontsize=13, fontweight="bold")

    # 청산 분포 stacked
    fc_pcts = [mult_metrics[n]["fc_rate"] * 100 for n in m_names]
    sl_pcts = [mult_metrics[n]["exit_dist"].get("stop_loss", 0) / max(mult_metrics[n]["trades"], 1) * 100 for n in m_names]
    ts_pcts = [mult_metrics[n]["trail_rate"] * 100 for n in m_names]

    ax2.fill_between(mults, 0, fc_pcts, alpha=0.3, color="#9E9E9E", label="forced_close")
    ax2.fill_between(mults, fc_pcts, [f + s for f, s in zip(fc_pcts, sl_pcts)],
                     alpha=0.3, color="#F44336", label="stop_loss")
    ax2.fill_between(mults, [f + s for f, s in zip(fc_pcts, sl_pcts)],
                     [f + s + t for f, s, t in zip(fc_pcts, sl_pcts, ts_pcts)],
                     alpha=0.3, color="#4CAF50", label="trailing_stop")
    ax2.plot(mults, ts_pcts, "g^-", markersize=8, label="trailing% (절대)")
    for x, y in zip(mults, ts_pcts):
        ax2.annotate(f"{y:.0f}%", (x, y), textcoords="offset points", xytext=(0, 8),
                     ha="center", fontsize=9, color="green")
    ax2.set_xlabel("ATR Trail Multiplier")
    ax2.set_ylabel("비율 (%)")
    ax2.set_title("Multiplier별 청산 분포", fontsize=13, fontweight="bold")
    ax2.legend()
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "grid_multiplier.png", dpi=150, bbox_inches="tight")
    plt.close()

    # ── 차트 3: 시장 국면별 PF (자본 + multiplier) ──
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 5))
    regimes = ["강세", "횡보", "약세"]
    rc = {"강세": "#4CAF50", "횡보": "#FF9800", "약세": "#F44336"}
    width = 0.2

    for j, r in enumerate(regimes):
        vals = [min(cap_metrics[n]["regime_pf"].get(r, 0), 10) for n in cap_names]
        ax1.bar(np.arange(len(cap_names)) + j * width, vals, width, label=r, color=rc[r], alpha=0.7)
    ax1.set_xticks(np.arange(len(cap_names)) + width)
    ax1.set_xticklabels([f"{c}백만" for c in caps])
    ax1.axhline(y=1, color="red", linestyle="--", alpha=0.3)
    ax1.set_title("자본별 시장 국면 PF", fontsize=12, fontweight="bold")
    ax1.set_ylabel("PF")
    ax1.legend()

    for j, r in enumerate(regimes):
        vals = [min(mult_metrics[n]["regime_pf"].get(r, 0), 10) for n in m_names]
        ax2.bar(np.arange(len(m_names)) + j * width, vals, width, label=r, color=rc[r], alpha=0.7)
    ax2.set_xticks(np.arange(len(m_names)) + width)
    ax2.set_xticklabels([f"×{m}" for m in mults])
    ax2.axhline(y=1, color="red", linestyle="--", alpha=0.3)
    ax2.set_title("Multiplier별 시장 국면 PF", fontsize=12, fontweight="bold")
    ax2.set_ylabel("PF")
    ax2.legend()
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "grid_regime_pf.png", dpi=150, bbox_inches="tight")
    plt.close()

    # ── 차트 4: 누적 PnL (multiplier) ──
    fig, ax = plt.subplots(figsize=(14, 6))
    colors = ["#F44336", "#FF9800", "#2196F3", "#4CAF50", "#9C27B0"]
    for i, (sc, n) in enumerate(zip(mult_scenarios, m_names)):
        trades = mult_results[n]
        if not trades:
            continue
        cum = np.cumsum([t["pnl"] for t in trades])
        ax.plot(range(len(cum)), cum, label=f"×{sc.trail_mult} (PF={mult_metrics[n]['pf']:.2f})",
                color=colors[i], linewidth=1.5)
    ax.set_xlabel("거래 번호")
    ax.set_ylabel("누적 PnL (원)")
    ax.set_title("Multiplier별 누적 PnL (자본 100만)", fontsize=14, fontweight="bold")
    ax.axhline(y=0, color="gray", alpha=0.3)
    ax.legend(fontsize=9)
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "grid_mult_cumulative.png", dpi=150, bbox_inches="tight")
    plt.close()

    print("  차트 4개 생성")


def generate_report(cap_scenarios, mult_scenarios, cap_metrics, mult_metrics):
    lines = []
    a = lines.append

    a("# 시나리오 I — 자본/Multiplier 그리드 보고서")
    a("")
    a(f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    a(f"> Base: 시나리오 I (F + ATR≥6%, 41종목)")
    a("")
    a("---")
    a("")

    # ── 시뮬 1: 자본 그리드 ──
    a("## 시뮬 1: 자본 그리드 (multiplier 2.5 고정)")
    a("")
    a("| 자본 | PF | 거래수 | 총 PnL | 거래당 PnL | 평균 qty | 자본활용률 | 수익률 | Max DD |")
    a("|------|-----|--------|--------|-----------|---------|----------|--------|--------|")
    for sc in cap_scenarios:
        m = cap_metrics[sc.name]
        pf_s = f"{m['pf']:.2f}" if m["pf"] != float("inf") else "∞"
        a(f"| {sc.capital//10000:,}만 | {pf_s} | {m['trades']} | {m['pnl']:+,.0f} | "
          f"{m['per_trade']:+,.0f} | {m['avg_qty']:.1f} | {m['capital_util']*100:.0f}% | "
          f"{m['pnl_pct_on_capital']:+.1f}% | {m['max_dd']:,.0f} |")
    a("")

    # PF 안정성 분석
    pf_vals = [cap_metrics[sc.name]["pf"] for sc in cap_scenarios]
    pf_range = max(pf_vals) - min(pf_vals)
    if pf_range < 0.1:
        a(f"**PF 안정성**: PF 범위 {pf_range:.3f} — 자본 크기에 거의 무관 (이상적)")
    elif pf_range < 0.3:
        a(f"**PF 안정성**: PF 범위 {pf_range:.3f} — 자본에 약간 영향")
    else:
        a(f"**PF 안정성**: PF 범위 {pf_range:.3f} — 자본 영향 유의미")
    a("")

    # 정수 절단 분석
    c1_trades = cap_metrics["C1"]["trades"]
    for sc in cap_scenarios:
        m = cap_metrics[sc.name]
        lost = c1_trades - m["trades"] if sc.name == "C1" else 0
        # 자본이 작으면 고가주 매수 불가 → 거래 감소
    c1_n = cap_metrics["C1"]["trades"]
    c5_n = cap_metrics["C5"]["trades"]
    if c1_n < c5_n:
        a(f"**정수 절단**: 100만원 시 {c5_n - c1_n}건 매수 불가 (고가주 1주 불가)")
    elif c1_n == c5_n:
        a("**정수 절단**: 100만원에서도 모든 종목 매수 가능 (절단 손실 없음)")
    a("")
    a("![자본별 PF](charts/grid_capital.png)")
    a("")
    a("---")
    a("")

    # ── 시뮬 2: multiplier 그리드 ──
    a("## 시뮬 2: Multiplier 그리드 (자본 100만 고정)")
    a("")
    a("| Multiplier | PF | 거래수 | 총 PnL | 거래당 PnL | trailing% | forced_close% | 자리점유% | 약세 PF |")
    a("|-----------|-----|--------|--------|-----------|-----------|---------------|----------|---------|")
    for sc in mult_scenarios:
        m = mult_metrics[sc.name]
        pf_s = f"{m['pf']:.2f}" if m["pf"] != float("inf") else "∞"
        bear = m["regime_pf"].get("약세", 0)
        bear_s = f"{bear:.2f}" if bear != float("inf") else "∞"
        a(f"| ×{sc.trail_mult} | {pf_s} | {m['trades']} | {m['pnl']:+,.0f} | "
          f"{m['per_trade']:+,.0f} | {m['trail_rate']*100:.1f}% | "
          f"{m['fc_rate']*100:.1f}% | {m['occupy_rate']*100:.1f}% | {bear_s} |")
    a("")

    a("![Multiplier별 PF/청산](charts/grid_multiplier.png)")
    a("")
    a("![Multiplier 누적 PnL](charts/grid_mult_cumulative.png)")
    a("")

    # 핵심 분석
    a("### 핵심 분석")
    a("")
    best_pf_m = max(mult_scenarios, key=lambda s: mult_metrics[s.name]["pf"])
    best_pt_m = max(mult_scenarios, key=lambda s: mult_metrics[s.name]["per_trade"])
    most_stable = min(mult_scenarios, key=lambda s: abs(
        mult_metrics[s.name]["regime_pf"].get("강세", 0) - mult_metrics[s.name]["regime_pf"].get("약세", 0))
        if mult_metrics[s.name]["regime_pf"].get("강세") and mult_metrics[s.name]["regime_pf"].get("약세") else 999)

    a(f"- **PF 최고**: ×{best_pf_m.trail_mult} — PF {mult_metrics[best_pf_m.name]['pf']:.2f}")
    a(f"- **거래당 PnL 최고**: ×{best_pt_m.trail_mult} — {mult_metrics[best_pt_m.name]['per_trade']:+,.0f}원")
    a(f"- **국면 편차 최소**: ×{most_stable.trail_mult}")
    a("")

    # trailing 빈도 변화
    a("### Trailing 빈도 변화")
    a("")
    for sc in mult_scenarios:
        m = mult_metrics[sc.name]
        ts_n = m["exit_dist"].get("trailing_stop", 0)
        a(f"- ×{sc.trail_mult}: trailing_stop {ts_n}건 ({m['trail_rate']*100:.1f}%)")
    a("")

    a("---")
    a("")

    # 시장 국면
    a("## 시장 국면별 PF")
    a("")
    a("![국면별 PF](charts/grid_regime_pf.png)")
    a("")

    a("### 자본별")
    a("")
    a("| 자본 | 강세 | 횡보 | 약세 |")
    a("|------|------|------|------|")
    for sc in cap_scenarios:
        rpf = cap_metrics[sc.name]["regime_pf"]
        def _f(r):
            v = rpf.get(r, 0)
            return f"{v:.2f}" if v != float("inf") else "∞"
        a(f"| {sc.capital//10000:,}만 | {_f('강세')} | {_f('횡보')} | {_f('약세')} |")
    a("")

    a("### Multiplier별")
    a("")
    a("| Mult | 강세 | 횡보 | 약세 |")
    a("|------|------|------|------|")
    for sc in mult_scenarios:
        rpf = mult_metrics[sc.name]["regime_pf"]
        def _f(r):
            v = rpf.get(r, 0)
            return f"{v:.2f}" if v != float("inf") else "∞"
        a(f"| ×{sc.trail_mult} | {_f('강세')} | {_f('횡보')} | {_f('약세')} |")
    a("")
    a("---")
    a("")

    # ── 종합 권장 ──
    a("## 종합 권장")
    a("")

    # 자본 권장
    best_cap = max(cap_scenarios, key=lambda s: cap_metrics[s.name]["pnl_pct_on_capital"])
    a(f"### 자본: {best_cap.capital//10000:,}만원 권장")
    a(f"- 수익률 {cap_metrics[best_cap.name]['pnl_pct_on_capital']:+.1f}% (자본 대비)")
    a(f"- PF {cap_metrics[best_cap.name]['pf']:.2f}")
    a("")

    # Multiplier 권장
    a(f"### Multiplier: ×{best_pf_m.trail_mult} 권장")
    bm = mult_metrics[best_pf_m.name]
    a(f"- PF {bm['pf']:.2f}, trailing {bm['trail_rate']*100:.1f}%")
    a(f"- 약세장 PF {bm['regime_pf'].get('약세', 0):.2f}")
    a("")

    # 최종 baseline
    a("### 최종 baseline 후보")
    a("")
    a(f"**I + 자본 {best_cap.capital//10000:,}만 + multiplier ×{best_pf_m.trail_mult}**")
    a("")
    a("| 항목 | 값 |")
    a("|------|-----|")
    a(f"| 전략 | Pure trailing (TP1 우회) |")
    a(f"| 유니버스 | ATR ≥ 6% ({41}종목) |")
    a(f"| 자본 | {best_cap.capital//10000:,}만원 |")
    a(f"| Trail multiplier | ×{best_pf_m.trail_mult} |")
    a(f"| 예상 PF | {bm['pf']:.2f} |")
    a(f"| 예상 거래수 | {bm['trades']}건/년 |")
    a(f"| 예상 거래당 PnL | {bm['per_trade']:+,.0f}원 |")
    a("")

    a("### 다음 단계")
    a("")
    a("1. config.yaml 반영 (trail_from_entry, atr_trail_multiplier, universe 필터)")
    a("2. backtester.py 코드 변경 (Pure trailing 모드)")
    a("3. 1주일 페이퍼 트레이딩 검증")
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
    print(" 시나리오 I - 자본/Multiplier 그리드 시뮬레이션")
    print("=" * 64)

    scenarios = build_scenarios()
    cap_scenarios = [s for s in scenarios if s.name.startswith("C")]
    mult_scenarios = [s for s in scenarios if s.name.startswith("M")]
    # C1은 M25와 동일하므로 mult_scenarios에 C1을 M25로 삽입
    c1 = [s for s in cap_scenarios if s.name == "C1"][0]
    mult_scenarios_full = []
    for s in mult_scenarios:
        if s.trail_mult == 2.5:
            continue  # skip, C1이 대체
        if s.trail_mult > 2.5:
            mult_scenarios_full.append(GridConfig("M25", "mult 2.5 (=C1)", c1.capital, c1.max_positions, 2.5, 0.02, 0.10))
        mult_scenarios_full.append(s)
    if not any(s.trail_mult == 2.5 for s in mult_scenarios_full):
        mult_scenarios_full.append(GridConfig("M25", "mult 2.5 (=C1)", c1.capital, c1.max_positions, 2.5, 0.02, 0.10))
    mult_scenarios_full.sort(key=lambda s: s.trail_mult)

    all_scenarios = cap_scenarios + [s for s in mult_scenarios_full if s.name != "C1"]
    unique_names = set()
    deduped = []
    for s in all_scenarios:
        if s.name not in unique_names:
            deduped.append(s)
            unique_names.add(s.name)

    print(f"  총 {len(deduped)}개 시나리오:")
    for s in deduped:
        print(f"    {s.name}: {s.desc} (cap={s.capital//10000}만, mult={s.trail_mult})")
    print()

    # ATR ≥ 6% 종목
    conn = sqlite3.connect(DB_PATH)
    uni = yaml.safe_load(open("config/universe.yaml", encoding="utf-8"))
    stocks = uni.get("stocks", [])
    ticker_to_market = {s["ticker"]: s.get("market", "unknown") for s in stocks}
    atr6_tickers = set()
    for s in stocks:
        cur = conn.execute("SELECT atr_pct FROM ticker_atr WHERE ticker=? ORDER BY dt DESC LIMIT 1", (s["ticker"],))
        row = cur.fetchone()
        if row and row[0] >= 6.0:
            atr6_tickers.add(s["ticker"])
    conn.close()
    print(f"  ATR≥6%: {len(atr6_tickers)}종목")

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
    for tk in atr6_tickers:
        candles = await bt_loader.load_candles(tk, args.start, f"{args.end} 23:59:59")
        if not candles.empty:
            candles_cache[tk] = pickle.dumps(candles)
    print(f"  로드 {len(candles_cache)}/{len(atr6_tickers)}")
    await db.close()

    market_map = build_market_strong_by_date(app_config.db_path, ma_length=base_config.market_ma_length)
    workers = max(2, (os.cpu_count() or 2) - 1)

    # 시나리오별 실행
    all_results = {}
    for sc in deduped:
        gc_dict = {
            "name": sc.name, "desc": sc.desc, "capital": sc.capital,
            "max_positions": sc.max_positions, "trail_mult": sc.trail_mult,
            "trail_min": sc.trail_min, "trail_max": sc.trail_max,
        }
        tasks = [
            (tk, ticker_to_market.get(tk, "unknown"), candles_cache[tk],
             base_config, backtest_config, market_map, gc_dict)
            for tk in atr6_tickers if tk in candles_cache
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
        ts_n = sum(1 for t in sc_trades if t.get("exit_reason") == "trailing_stop")
        print(f"  [{sc.name}] {sc.desc:30s} | n={total:>4} PF={pf:>5.2f} PnL={pnl:>+12,.0f} ts={ts_n}")
        all_results[sc.name] = sc_trades

    # C1 = M25
    if "M25" not in all_results:
        all_results["M25"] = all_results["C1"]

    # 검증
    c1_pf = cap_scenarios[0]
    m = all_results["C1"]
    gp = sum(t["pnl"] for t in m if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in m if t["pnl"] < 0))
    pf_c1 = gp / gl if gl else 0
    print(f"\n  [검증] C1/M25: {len(m)}건, PF={pf_c1:.2f}")

    regime_map = build_regime_map(DB_PATH)

    cap_metrics = {sc.name: compute_metrics(all_results[sc.name], regime_map, sc.capital) for sc in cap_scenarios}
    mult_metrics = {}
    for sc in mult_scenarios_full:
        key = sc.name if sc.name in all_results else "C1"
        mult_metrics[sc.name] = compute_metrics(all_results[key], regime_map, sc.capital)

    cap_results = {sc.name: all_results[sc.name] for sc in cap_scenarios}
    mult_results_data = {sc.name: all_results.get(sc.name, all_results.get("C1", [])) for sc in mult_scenarios_full}

    print()
    generate_all(cap_scenarios, mult_scenarios_full, cap_metrics, mult_metrics,
                 cap_results, mult_results_data)
    generate_report(cap_scenarios, mult_scenarios_full, cap_metrics, mult_metrics)
    print(f"\n[REPORT] {REPORT_PATH}")
    print("\n" + "=" * 64)


if __name__ == "__main__":
    asyncio.run(main())
