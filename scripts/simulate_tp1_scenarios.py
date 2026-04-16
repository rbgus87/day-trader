"""scripts/simulate_tp1_scenarios.py — TP1/Trailing 시나리오 7종 비교 시뮬레이션.

backtester.py 영구 수정 없이, 청산 로직만 시나리오별로 분기하는 wrapper.

사용:
    python scripts/simulate_tp1_scenarios.py
    python scripts/simulate_tp1_scenarios.py --start 2025-04-01 --end 2026-04-15
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
from datetime import timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig
from core.cost_model import TradeCosts, apply_buy_costs, apply_sell_costs
from core.indicators import calculate_atr_trailing_stop, get_latest_atr
from data.db_manager import DbManager

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False

CHARTS_DIR = Path("reports/charts")
REPORT_PATH = Path("reports/tp1_scenario_comparison.md")
DB_PATH = "daytrader.db"


# ======================================================================
# 시나리오 정의
# ======================================================================

@dataclass
class ScenarioConfig:
    name: str
    desc: str
    tp1_pct: float | None  # TP1 목표 (고정 %). None = TP1 없음 (F/G)
    tp1_use_atr: bool       # True면 ATR 기반 TP1 사용 (A만 해당)
    split_sell: bool         # TP1 hit 시 분할매도 여부
    trail_from_entry: bool   # 진입 즉시 trailing 활성 (F)
    trail_buffer_pct: float | None  # 버퍼 임계 (G: +2%)
    breakeven_on_trigger: bool  # trailing 활성 시 stop→본전


SCENARIOS = [
    ScenarioConfig("A", "Baseline (TP1 ATR×3, 50% split)", None, True, True, False, None, True),
    ScenarioConfig("B", "TP1 +5% + 50% split", 0.05, False, True, False, None, True),
    ScenarioConfig("C", "TP1 +8% + 50% split", 0.08, False, True, False, None, True),
    ScenarioConfig("D", "TP1 +5% trigger, trail 100%", 0.05, False, False, False, None, True),
    ScenarioConfig("E", "TP1 +8% trigger, trail 100%", 0.08, False, False, False, None, True),
    ScenarioConfig("F", "Pure trailing (즉시)", None, False, False, True, None, False),
    ScenarioConfig("G", "Pure trailing + 2% buffer", None, False, False, False, 0.02, True),
]


# ======================================================================
# 시나리오 백테스트 엔진
# ======================================================================

def run_scenario_day(
    day_candles: pd.DataFrame,
    strategy,
    scenario: ScenarioConfig,
    costs: TradeCosts,
    trading_config,
    ticker: str,
    db_path: str = "daytrader.db",
) -> list[dict]:
    """하루치 캔들에 대해 시나리오별 청산 로직으로 백테스트.

    진입 로직은 strategy.generate_signal() 그대로 사용 (baseline과 동일).
    청산 로직만 시나리오에 따라 분기.
    """
    from strategy.base_strategy import Signal

    candles = day_candles.reset_index(drop=True)
    if candles.empty:
        return []

    trades = []
    position = None

    # ATR% 조회 (trailing 계산용)
    as_of = None
    try:
        as_of = pd.to_datetime(candles["ts"].iloc[0]).strftime("%Y-%m-%d")
    except Exception:
        pass
    atr_pct = get_latest_atr(db_path, ticker, as_of)
    # ATR trailing 파라미터
    trail_mult = getattr(trading_config, "atr_trail_multiplier", 2.5)
    trail_min = getattr(trading_config, "atr_trail_min_pct", 0.02)
    trail_max = getattr(trading_config, "atr_trail_max_pct", 0.10)
    # fallback trailing
    trailing_pct_fallback = getattr(trading_config, "trailing_stop_pct", 0.005)

    def calc_trailing_stop(peak: float) -> float:
        if atr_pct is not None:
            return calculate_atr_trailing_stop(peak, atr_pct, trail_mult, trail_min, trail_max)
        return peak * (1 - trailing_pct_fallback)

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

        # ── 포지션 없음 → 진입 (모든 시나리오 동일) ──
        if position is None:
            signal = strategy.generate_signal(candles_so_far, tick)
            if signal is not None and signal.side == "buy":
                strategy.on_entry()
                entry_price_raw = float(row["close"])
                entry_price, net_entry = apply_buy_costs(entry_price_raw, costs)

                stop_loss = strategy.get_stop_loss(entry_price)

                # TP1 가격 결정
                if scenario.tp1_use_atr:
                    tp1_price = strategy.get_take_profit(entry_price)
                elif scenario.tp1_pct is not None:
                    tp1_price = entry_price * (1 + scenario.tp1_pct)
                else:
                    tp1_price = None  # F/G: TP1 없음

                position = {
                    "entry_ts": row["ts"],
                    "entry_price": entry_price,
                    "net_entry": net_entry,
                    "stop_loss": stop_loss,
                    "tp1_price": tp1_price,
                    "trailing_active": scenario.trail_from_entry,  # F만 True
                    "tp1_hit": False,
                    "remaining_ratio": 1.0,
                    "highest_price": float(row["high"]),
                }

                # F: 진입 즉시 trailing → highest 추적 시작
                if scenario.trail_from_entry:
                    new_stop = calc_trailing_stop(position["highest_price"])
                    position["stop_loss"] = max(position["stop_loss"], new_stop)

            continue

        # ── 포지션 보유 → 시나리오별 청산 ──
        low = float(row["low"])
        high = float(row["high"])
        close = float(row["close"])
        remaining = position["remaining_ratio"]
        is_last = idx == len(candles) - 1

        # (1) 손절 체크 (공통)
        if low <= position["stop_loss"]:
            exit_price_sl, net_exit = apply_sell_costs(position["stop_loss"], costs)
            pnl = (net_exit - position["net_entry"]) * remaining
            pnl_pct = (net_exit - position["net_entry"]) / position["net_entry"]
            trades.append({
                "entry_ts": position["entry_ts"], "exit_ts": row["ts"],
                "entry_price": position["entry_price"], "exit_price": exit_price_sl,
                "pnl": pnl, "pnl_pct": pnl_pct,
                "exit_reason": "stop_loss",
            })
            position = None
            strategy.on_exit()
            continue

        # (2) Trailing 활성 상태 → trailing 체크
        if position["trailing_active"]:
            # 고점 갱신
            if high > position["highest_price"]:
                position["highest_price"] = high
                new_stop = calc_trailing_stop(position["highest_price"])
                position["stop_loss"] = max(position["stop_loss"], new_stop)

            if low <= position["stop_loss"]:
                exit_price_ts, net_exit = apply_sell_costs(position["stop_loss"], costs)
                pnl = (net_exit - position["net_entry"]) * remaining
                pnl_pct = (net_exit - position["net_entry"]) / position["net_entry"]
                trades.append({
                    "entry_ts": position["entry_ts"], "exit_ts": row["ts"],
                    "entry_price": position["entry_price"], "exit_price": exit_price_ts,
                    "pnl": pnl, "pnl_pct": pnl_pct,
                    "exit_reason": "trailing_stop",
                })
                position = None
                strategy.on_exit()
                continue

            if is_last:
                exit_price_fc, net_exit = apply_sell_costs(close, costs)
                pnl = (net_exit - position["net_entry"]) * remaining
                pnl_pct = (net_exit - position["net_entry"]) / position["net_entry"]
                trades.append({
                    "entry_ts": position["entry_ts"], "exit_ts": row["ts"],
                    "entry_price": position["entry_price"], "exit_price": exit_price_fc,
                    "pnl": pnl, "pnl_pct": pnl_pct,
                    "exit_reason": "forced_close",
                })
                position = None
                strategy.on_exit()
            continue

        # (3) TP1/버퍼 트리거 체크 (trailing 미활성 상태)
        triggered = False

        # 시나리오 A/B/C/D/E: TP1 가격 도달 체크
        if position["tp1_price"] is not None and high >= position["tp1_price"]:
            triggered = True
            trigger_price = position["tp1_price"]

            if scenario.split_sell:
                # A/B/C: 분할매도
                tp1_slipped, net_tp1 = apply_sell_costs(trigger_price, costs)
                sell_ratio = getattr(trading_config, "tp1_sell_ratio", 0.5)
                pnl = (net_tp1 - position["net_entry"]) * sell_ratio
                pnl_pct = (net_tp1 - position["net_entry"]) / position["net_entry"]
                trades.append({
                    "entry_ts": position["entry_ts"], "exit_ts": row["ts"],
                    "entry_price": position["entry_price"], "exit_price": tp1_slipped,
                    "pnl": pnl, "pnl_pct": pnl_pct,
                    "exit_reason": "tp1_hit",
                })
                position["remaining_ratio"] = 1.0 - sell_ratio
            # else: D/E — 분할매도 X, 전량 유지

            # trailing 활성화 + 본전 이동
            position["trailing_active"] = True
            position["tp1_hit"] = True
            position["highest_price"] = high
            if scenario.breakeven_on_trigger:
                position["stop_loss"] = max(position["stop_loss"], position["entry_price"])

            # 분할매도 + 마지막 캔들: 잔여 강제청산
            if scenario.split_sell and is_last and position is not None:
                rem = position["remaining_ratio"]
                fc_slipped, net_fc = apply_sell_costs(close, costs)
                fc_pnl = (net_fc - position["net_entry"]) * rem
                fc_pnl_pct = (net_fc - position["net_entry"]) / position["net_entry"]
                trades.append({
                    "entry_ts": position["entry_ts"], "exit_ts": row["ts"],
                    "entry_price": position["entry_price"], "exit_price": fc_slipped,
                    "pnl": fc_pnl, "pnl_pct": fc_pnl_pct,
                    "exit_reason": "forced_close",
                })
                position = None
                strategy.on_exit()

        # 시나리오 G: +2% 버퍼 도달 체크
        elif scenario.trail_buffer_pct is not None:
            buffer_price = position["entry_price"] * (1 + scenario.trail_buffer_pct)
            if high >= buffer_price:
                triggered = True
                position["trailing_active"] = True
                position["highest_price"] = high
                if scenario.breakeven_on_trigger:
                    position["stop_loss"] = max(position["stop_loss"], position["entry_price"])

        # (4) 트리거 안 됨 + 마지막 캔들 → forced_close
        if not triggered and is_last and position is not None:
            exit_price_fc, net_exit = apply_sell_costs(close, costs)
            pnl = (net_exit - position["net_entry"]) * remaining
            pnl_pct = (net_exit - position["net_entry"]) / position["net_entry"]
            trades.append({
                "entry_ts": position["entry_ts"], "exit_ts": row["ts"],
                "entry_price": position["entry_price"], "exit_price": exit_price_fc,
                "pnl": pnl, "pnl_pct": pnl_pct,
                "exit_reason": "forced_close",
            })
            position = None
            strategy.on_exit()

    strategy.set_backtest_time(None)
    return trades


def run_scenario_multi_day(
    ticker: str,
    all_candles: pd.DataFrame,
    trading_config,
    backtest_config: BacktestConfig,
    scenario: ScenarioConfig,
    ticker_market: str,
    market_strong_by_date: dict,
) -> dict:
    """다일 시나리오 백테스트 (run_multi_day_cached 동치)."""
    from strategy.momentum_strategy import MomentumStrategy

    costs = TradeCosts(
        commission_rate=backtest_config.commission,
        slippage_rate=backtest_config.slippage,
        tax_rate=backtest_config.tax,
    )

    strategy = MomentumStrategy(trading_config)

    if all_candles.empty:
        return {"trades": [], "total_trades": 0, "total_pnl": 0, "profit_factor": 0}

    df = all_candles.copy()
    if "date" not in df.columns:
        df["date"] = df["ts"].dt.date

    all_trades = []
    prev_day_df = None

    market_filter_enabled = getattr(trading_config, "market_filter_enabled", False)
    blacklist_enabled = getattr(trading_config, "blacklist_enabled", False)
    bl_lookback = getattr(trading_config, "blacklist_lookback_days", 5)
    bl_threshold = getattr(trading_config, "blacklist_loss_threshold", 3)
    rest_enabled = getattr(trading_config, "consecutive_loss_rest_enabled", False)
    rest_threshold = getattr(trading_config, "consecutive_loss_threshold", 3)

    daily_pnl_by_date = {}

    for date, day_candles in df.groupby("date"):
        day_df = day_candles.drop(columns=["date"]).reset_index(drop=True)

        skip_day = False
        if market_filter_enabled and ticker_market in ("kospi", "kosdaq"):
            date_key = date.strftime("%Y%m%d")
            strong = market_strong_by_date.get(date_key)
            if strong is not None and not strong.get(ticker_market, True):
                skip_day = True

        if not skip_day and blacklist_enabled:
            from datetime import timedelta as _td
            cutoff = date - _td(days=bl_lookback)
            recent_losses = sum(
                1 for t in all_trades
                if t.get("pnl", 0) < 0 and _exit_date(t) is not None
                and cutoff <= _exit_date(t) < date
            )
            if recent_losses >= bl_threshold:
                skip_day = True

        if not skip_day and rest_enabled:
            past_dates = sorted((d for d in daily_pnl_by_date if d < date), reverse=True)
            consecutive = 0
            for d in past_dates:
                if daily_pnl_by_date[d] < 0:
                    consecutive += 1
                else:
                    break
            if consecutive >= rest_threshold:
                skip_day = True

        if skip_day:
            prev_day_df = day_df
            continue

        strategy.reset()
        # setup day (replicate backtester._setup_strategy_day)
        if hasattr(strategy, "set_ticker"):
            strategy.set_ticker(ticker)
        if hasattr(strategy, "set_prev_day_data") and prev_day_df is not None:
            strategy.set_prev_day_data(float(prev_day_df["high"].max()), int(prev_day_df["volume"].sum()))

        day_trades = run_scenario_day(day_df, strategy, scenario, costs, trading_config, ticker)
        for t in day_trades:
            t["ticker"] = ticker
            t["ticker_market"] = ticker_market
        all_trades.extend(day_trades)
        daily_pnl_by_date[date] = sum(t.get("pnl", 0) for t in day_trades)
        prev_day_df = day_df

    # KPI 계산
    total = len(all_trades)
    pnl_list = [t["pnl"] for t in all_trades]
    gp = sum(p for p in pnl_list if p > 0)
    gl = abs(sum(p for p in pnl_list if p < 0))
    pf = gp / gl if gl > 0 else float("inf")

    return {
        "trades": all_trades,
        "total_trades": total,
        "total_pnl": sum(pnl_list),
        "profit_factor": pf,
    }


def _exit_date(t):
    exit_ts = t.get("exit_ts")
    if exit_ts is None:
        return None
    try:
        return exit_ts.date() if hasattr(exit_ts, "date") else pd.to_datetime(exit_ts).date()
    except Exception:
        return None


# ======================================================================
# 워커 (ProcessPool)
# ======================================================================

def _worker(args):
    """워커: 단일 종목 × 전체 시나리오."""
    (
        ticker, ticker_market, candles_pickle,
        trading_config, backtest_config, market_map, scenario_dicts,
    ) = args

    candles = pickle.loads(candles_pickle)
    results = {}

    for sd in scenario_dicts:
        sc = ScenarioConfig(**sd)
        result = run_scenario_multi_day(
            ticker, candles, trading_config, backtest_config,
            sc, ticker_market, market_map,
        )
        results[sc.name] = result

    return ticker, results


# ======================================================================
# 분석 + 보고
# ======================================================================

def compute_metrics(all_results: dict[str, list[dict]], market_regime_map: dict) -> dict:
    """시나리오별 종합 메트릭 계산."""
    metrics = {}

    for sc_name, trades in all_results.items():
        total = len(trades)
        if total == 0:
            metrics[sc_name] = {
                "trades": 0, "pf": 0, "pnl": 0, "per_trade": 0,
                "exit_dist": {}, "avg_hold_min": 0, "max_dd": 0,
                "regime_pf": {}, "trail_active_rate": 0, "forced_close_rate": 0,
            }
            continue

        pnl_list = [t["pnl"] for t in trades]
        gp = sum(p for p in pnl_list if p > 0)
        gl = abs(sum(p for p in pnl_list if p < 0))
        pf = gp / gl if gl > 0 else float("inf")
        total_pnl = sum(pnl_list)

        # 청산 분포
        exit_dist = Counter(t.get("exit_reason", "?") for t in trades)

        # 평균 보유 시간
        hold_times = []
        for t in trades:
            try:
                entry = pd.to_datetime(t["entry_ts"])
                exit_ = pd.to_datetime(t["exit_ts"])
                hold_times.append((exit_ - entry).total_seconds() / 60)
            except Exception:
                pass
        avg_hold = np.mean(hold_times) if hold_times else 0

        # 최대 drawdown
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnl_list:
            cum += p
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd

        # trailing 활성률: trailing_stop + tp1_hit(분할매도 후 trailing된 것) / 전체
        trail_exits = exit_dist.get("trailing_stop", 0) + exit_dist.get("tp1_hit", 0)
        trail_rate = trail_exits / total if total else 0

        forced_rate = exit_dist.get("forced_close", 0) / total if total else 0

        # 시장 국면별 PF
        regime_trades = defaultdict(list)
        for t in trades:
            try:
                month = pd.to_datetime(t["entry_ts"]).strftime("%Y-%m")
                regime = market_regime_map.get(month, "?")
                regime_trades[regime].append(t)
            except Exception:
                pass

        regime_pf = {}
        for regime, rt in regime_trades.items():
            r_gp = sum(t["pnl"] for t in rt if t["pnl"] > 0)
            r_gl = abs(sum(t["pnl"] for t in rt if t["pnl"] < 0))
            regime_pf[regime] = r_gp / r_gl if r_gl > 0 else float("inf")

        metrics[sc_name] = {
            "trades": total,
            "pf": pf,
            "pnl": total_pnl,
            "per_trade": total_pnl / total,
            "exit_dist": dict(exit_dist),
            "avg_hold_min": avg_hold,
            "max_dd": max_dd,
            "regime_pf": regime_pf,
            "trail_active_rate": trail_rate,
            "forced_close_rate": forced_rate,
        }

    return metrics


def build_market_regime_map(db_path: str) -> dict:
    """월별 시장 국면 라벨 (강세/횡보/약세)."""
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
        if avg >= 0.05:
            result[ym] = "강세"
        elif avg <= -0.05:
            result[ym] = "약세"
        else:
            result[ym] = "횡보"
    return result


def generate_charts(metrics: dict, all_results: dict):
    """시각화 생성."""
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    sc_names = [s.name for s in SCENARIOS]
    sc_descs = {s.name: s.desc for s in SCENARIOS}

    # 1. PF 비교 막대
    fig, ax = plt.subplots(figsize=(12, 5))
    pfs = [min(metrics[n]["pf"], 10) for n in sc_names]
    colors = ["#2196F3" if n == "A" else "#4CAF50" if pfs[i] > metrics["A"]["pf"] else "#FF9800"
              for i, n in enumerate(sc_names)]
    bars = ax.bar(sc_names, pfs, color=colors, edgecolor="white", width=0.6)
    ax.axhline(y=metrics["A"]["pf"], color="blue", linestyle="--", alpha=0.4, label=f"Baseline PF={metrics['A']['pf']:.2f}")
    ax.axhline(y=1.0, color="red", linestyle="--", alpha=0.3)
    for bar, pf_val, n in zip(bars, pfs, sc_names):
        m = metrics[n]
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.05,
                f"PF={pf_val:.2f}\nn={m['trades']}", ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("Profit Factor")
    ax.set_title("시나리오별 PF 비교", fontsize=14, fontweight="bold")
    ax.legend()
    labels = [f"{n}\n{sc_descs[n][:15]}" for n in sc_names]
    ax.set_xticks(range(len(sc_names)))
    ax.set_xticklabels(labels, fontsize=8)
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "scenario_pf_comparison.png", dpi=150, bbox_inches="tight")
    plt.close()

    # 2. 누적 PnL 곡선
    fig, ax = plt.subplots(figsize=(14, 6))
    line_styles = ["-", "--", "-.", ":", "-", "--", "-."]
    line_colors = ["#2196F3", "#4CAF50", "#8BC34A", "#FF9800", "#FF5722", "#9C27B0", "#607D8B"]
    for i, n in enumerate(sc_names):
        trades = all_results[n]
        if not trades:
            continue
        cum_pnl = np.cumsum([t["pnl"] for t in trades])
        ax.plot(range(len(cum_pnl)), cum_pnl, label=f"{n}: {sc_descs[n][:20]}",
                linestyle=line_styles[i % len(line_styles)], color=line_colors[i], linewidth=1.5)
    ax.set_xlabel("거래 번호")
    ax.set_ylabel("누적 PnL (원)")
    ax.set_title("시나리오별 누적 PnL 곡선", fontsize=14, fontweight="bold")
    ax.axhline(y=0, color="gray", linestyle="-", alpha=0.3)
    ax.legend(fontsize=8, loc="upper left")
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "scenario_cumulative_pnl.png", dpi=150, bbox_inches="tight")
    plt.close()

    # 3. 시장 국면별 PF (시나리오 × 국면)
    regimes = ["강세", "횡보", "약세"]
    fig, ax = plt.subplots(figsize=(12, 5))
    x = np.arange(len(sc_names))
    width = 0.25
    regime_colors = {"강세": "#4CAF50", "횡보": "#FF9800", "약세": "#F44336"}

    for j, regime in enumerate(regimes):
        vals = []
        for n in sc_names:
            rpf = metrics[n]["regime_pf"].get(regime, 0)
            vals.append(min(rpf, 10) if rpf != float("inf") else 10)
        ax.bar(x + j * width, vals, width, label=regime, color=regime_colors[regime], alpha=0.7)

    ax.set_xticks(x + width)
    ax.set_xticklabels(sc_names)
    ax.set_ylabel("Profit Factor")
    ax.set_title("시장 국면별 PF (시나리오 비교)", fontsize=14, fontweight="bold")
    ax.axhline(y=1.0, color="red", linestyle="--", alpha=0.3)
    ax.legend()
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "scenario_regime_pf.png", dpi=150, bbox_inches="tight")
    plt.close()

    # 4. 청산 분포 stacked bar
    fig, ax = plt.subplots(figsize=(12, 5))
    reasons = ["forced_close", "stop_loss", "tp1_hit", "trailing_stop"]
    reason_colors = {"forced_close": "#9E9E9E", "stop_loss": "#F44336", "tp1_hit": "#FF9800", "trailing_stop": "#4CAF50"}
    bottoms = np.zeros(len(sc_names))
    for reason in reasons:
        vals = []
        for n in sc_names:
            total = metrics[n]["trades"] or 1
            cnt = metrics[n]["exit_dist"].get(reason, 0)
            vals.append(cnt / total * 100)
        ax.bar(sc_names, vals, bottom=bottoms, label=reason, color=reason_colors.get(reason, "#999"), width=0.6)
        bottoms += np.array(vals)
    ax.set_ylabel("비율 (%)")
    ax.set_title("시나리오별 청산 분포", fontsize=14, fontweight="bold")
    ax.legend(loc="upper right")
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "scenario_exit_distribution.png", dpi=150, bbox_inches="tight")
    plt.close()

    print("  차트 4개 생성 완료")


def generate_report(metrics: dict, all_results: dict):
    """Markdown 보고서 생성."""
    lines = []
    a = lines.append

    a("# TP1/Trailing 시나리오 비교 보고서")
    a("")
    a(f"> 생성: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    a(f"> Baseline PF: {metrics['A']['pf']:.2f} / {metrics['A']['trades']}건")
    a("")

    # 시나리오 정의 요약
    a("## 시나리오 정의")
    a("")
    a("| ID | 설명 | TP1 | 분할매도 | Trail 시작 | 본전 이동 |")
    a("|-----|------|-----|---------|-----------|----------|")
    for sc in SCENARIOS:
        tp1 = "ATR×3.0" if sc.tp1_use_atr else (f"+{sc.tp1_pct*100:.0f}%" if sc.tp1_pct else "없음")
        split = "50%" if sc.split_sell else "없음"
        trail = "진입 즉시" if sc.trail_from_entry else (f"+{sc.trail_buffer_pct*100:.0f}% 도달" if sc.trail_buffer_pct else "TP1 후")
        be = "O" if sc.breakeven_on_trigger else "X"
        if sc.tp1_pct is None and not sc.tp1_use_atr:
            trail = "진입 즉시" if sc.trail_from_entry else f"+{sc.trail_buffer_pct*100:.0f}% 도달"
        a(f"| {sc.name} | {sc.desc} | {tp1} | {split} | {trail} | {be} |")
    a("")
    a("---")
    a("")

    # 요약 매트릭스
    a("## 요약 매트릭스")
    a("")
    a("| 시나리오 | PF | 거래수 | 총 PnL | 거래당 PnL | trailing 활성률 | forced_close% | Max DD |")
    a("|---------|-----|--------|--------|-----------|---------------|-------------|--------|")
    for sc in SCENARIOS:
        m = metrics[sc.name]
        pf_str = f"{m['pf']:.2f}" if m["pf"] != float("inf") else "∞"
        a(f"| **{sc.name}** | {pf_str} | {m['trades']} | {m['pnl']:+,.0f} | {m['per_trade']:+,.0f} | "
          f"{m['trail_active_rate']*100:.1f}% | {m['forced_close_rate']*100:.1f}% | {m['max_dd']:,.0f} |")
    a("")
    a("![PF 비교](charts/scenario_pf_comparison.png)")
    a("")
    a("![누적 PnL](charts/scenario_cumulative_pnl.png)")
    a("")
    a("---")
    a("")

    # 청산 분포 상세
    a("## 청산 분포 상세")
    a("")
    a("| 시나리오 | forced_close | stop_loss | tp1_hit | trailing_stop |")
    a("|---------|-------------|-----------|---------|--------------|")
    for sc in SCENARIOS:
        m = metrics[sc.name]
        total = m["trades"] or 1
        ed = m["exit_dist"]
        fc = ed.get("forced_close", 0)
        sl = ed.get("stop_loss", 0)
        tp = ed.get("tp1_hit", 0)
        ts = ed.get("trailing_stop", 0)
        a(f"| {sc.name} | {fc} ({fc/total*100:.0f}%) | {sl} ({sl/total*100:.0f}%) | "
          f"{tp} ({tp/total*100:.0f}%) | {ts} ({ts/total*100:.0f}%) |")
    a("")
    a("![청산 분포](charts/scenario_exit_distribution.png)")
    a("")
    a("---")
    a("")

    # 시장 국면별
    a("## 시장 국면별 PF")
    a("")
    a("| 시나리오 | 강세 | 횡보 | 약세 |")
    a("|---------|------|------|------|")
    for sc in SCENARIOS:
        rpf = metrics[sc.name]["regime_pf"]
        def _fmt(r):
            v = rpf.get(r, 0)
            return f"{v:.2f}" if v != float("inf") else "∞"
        a(f"| {sc.name} | {_fmt('강세')} | {_fmt('횡보')} | {_fmt('약세')} |")
    a("")
    a("![국면별 PF](charts/scenario_regime_pf.png)")
    a("")
    a("---")
    a("")

    # 핵심 비교
    a("## 핵심 비교")
    a("")

    def _compare(a_name, b_name, label):
        ma, mb = metrics[a_name], metrics[b_name]
        delta_pf = mb["pf"] - ma["pf"]
        delta_pnl = mb["pnl"] - ma["pnl"]
        delta_pt = mb["per_trade"] - ma["per_trade"]
        return (f"- **{label}**: PF {ma['pf']:.2f} → {mb['pf']:.2f} ({delta_pf:+.2f}), "
                f"PnL {delta_pnl:+,.0f}, 거래당 {delta_pt:+,.0f}")

    a("### A vs B vs C (TP1 임계값 영향)")
    a("")
    a(_compare("A", "B", "A→B (ATR×3→+5%)"))
    a(_compare("A", "C", "A→C (ATR×3→+8%)"))
    a("")

    a("### B vs D, C vs E (분할매도 vs Trailing-only) — 핵심")
    a("")
    a(_compare("B", "D", "B→D (+5% split→trail)"))
    a(_compare("C", "E", "C→E (+8% split→trail)"))
    a("")

    a("### D/E vs F/G (TP1 트리거 유무)")
    a("")
    a(_compare("D", "F", "D→F (trigger→pure trail)"))
    a(_compare("E", "G", "E→G (trigger→buffer trail)"))
    a("")

    a("### F vs A (가장 극단적 비교)")
    a("")
    a(_compare("A", "F", "A→F (baseline→pure trail)"))
    a(_compare("A", "G", "A→G (baseline→buffer trail)"))
    a("")
    a("---")
    a("")

    # 결론
    a("## 결론 + 권장")
    a("")
    best = max(SCENARIOS, key=lambda s: metrics[s.name]["pf"] if metrics[s.name]["trades"] > 50 else 0)
    best_pt = max(SCENARIOS, key=lambda s: metrics[s.name]["per_trade"] if metrics[s.name]["trades"] > 50 else -9999)
    best_m = metrics[best.name]
    best_pt_m = metrics[best_pt.name]

    a(f"**PF 최고**: 시나리오 **{best.name}** ({best.desc}) — PF {best_m['pf']:.2f}, {best_m['trades']}건")
    a(f"**거래당 PnL 최고**: 시나리오 **{best_pt.name}** ({best_pt.desc}) — {best_pt_m['per_trade']:+,.0f}원/건")
    a("")

    # D vs B 핵심 판정
    d_pf, b_pf = metrics["D"]["pf"], metrics["B"]["pf"]
    if d_pf > b_pf:
        a("**사용자 제안 검증 (D: TP1 트리거 + 전량 trailing)**: 분할매도 대비 **PF 개선 확인** — 채택 권장")
    elif d_pf < b_pf:
        a("**사용자 제안 검증 (D: TP1 트리거 + 전량 trailing)**: 분할매도 대비 **PF 하락** — 분할매도 유지 권장")
    else:
        a("**사용자 제안 검증 (D: TP1 트리거 + 전량 trailing)**: 분할매도와 동등 — 추가 검증 필요")
    a("")

    # 채택 시 코드 변경 영향
    a("### 채택 시 코드 변경")
    a("")
    a(f"시나리오 **{best.name}** 채택 시:")
    a("")
    if best.name in ("B", "C"):
        a("- `config.yaml`: `atr_tp_enabled: false`, `tp1_pct` 변경")
        a("- 코드 변경 없음 (config만 조정)")
    elif best.name in ("D", "E"):
        a("- `backtest/backtester.py`: TP1 hit 시 분할매도 → trailing-only 분기 추가")
        a("- `config.yaml`: 새 파라미터 `tp1_trail_only: true`")
        a("- 영향 범위: backtester ~20줄 수정")
    elif best.name == "F":
        a("- `backtest/backtester.py`: TP1 로직 스킵, trailing 즉시 활성 분기")
        a("- `config.yaml`: `tp1_enabled: false`, `trail_from_entry: true`")
        a("- 영향 범위: backtester ~30줄 수정")
    elif best.name == "G":
        a("- `backtest/backtester.py`: buffer 도달 시 trailing 활성 분기")
        a("- `config.yaml`: `trail_buffer_pct: 0.02`")
        a("- 영향 범위: backtester ~25줄 수정")
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
    print(" TP1/Trailing 시나리오 7종 비교 시뮬레이션")
    print("=" * 64)
    print(f"  기간: {args.start} ~ {args.end}")
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

    uni = yaml.safe_load(open("config/universe.yaml", encoding="utf-8"))
    stocks = uni.get("stocks", [])
    ticker_to_market = {s["ticker"]: s.get("market", "unknown") for s in stocks}

    db = DbManager(app_config.db_path)
    await db.init()
    bt_loader = Backtester(db=db, config=base_config, backtest_config=backtest_config)

    print(f"[LOAD] 캔들 로딩 ({len(stocks)}종목)...")
    candles_cache = {}
    for s in stocks:
        tk = s["ticker"]
        candles = await bt_loader.load_candles(tk, args.start, f"{args.end} 23:59:59")
        if not candles.empty:
            candles_cache[tk] = pickle.dumps(candles)
    print(f"  로드 {len(candles_cache)}/{len(stocks)}")
    await db.close()

    market_map = build_market_strong_by_date(app_config.db_path, ma_length=base_config.market_ma_length)

    # 시나리오를 dict로 직렬화 (ProcessPool pickle)
    scenario_dicts = [
        {
            "name": s.name, "desc": s.desc, "tp1_pct": s.tp1_pct,
            "tp1_use_atr": s.tp1_use_atr, "split_sell": s.split_sell,
            "trail_from_entry": s.trail_from_entry, "trail_buffer_pct": s.trail_buffer_pct,
            "breakeven_on_trigger": s.breakeven_on_trigger,
        }
        for s in SCENARIOS
    ]

    workers = max(2, (os.cpu_count() or 2) - 1)
    print(f"[RUN] 워커 {workers}, 시나리오 {len(SCENARIOS)}종...")

    tasks = [
        (
            tk, ticker_to_market.get(tk, "unknown"),
            candles_cache[tk], base_config, backtest_config, market_map, scenario_dicts,
        )
        for tk in candles_cache
    ]

    all_results = {s.name: [] for s in SCENARIOS}
    with ProcessPoolExecutor(max_workers=workers) as executor:
        for ticker, ticker_results in executor.map(_worker, tasks):
            for sc_name, result in ticker_results.items():
                all_results[sc_name].extend(result["trades"])

    # 결과 요약
    print()
    for sc in SCENARIOS:
        trades = all_results[sc.name]
        total = len(trades)
        pnl = sum(t["pnl"] for t in trades)
        gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
        pf = gp / gl if gl > 0 else float("inf")
        fc = sum(1 for t in trades if t.get("exit_reason") == "forced_close")
        ts = sum(1 for t in trades if t.get("exit_reason") == "trailing_stop")
        print(f"  [{sc.name}] {sc.desc[:30]:30s} | n={total:>4} PF={pf:>5.2f} PnL={pnl:>+10,.0f} | "
              f"fc={fc} ts={ts}")

    # Baseline 검증
    a_trades = len(all_results["A"])
    a_pnl = sum(t["pnl"] for t in all_results["A"])
    a_gp = sum(t["pnl"] for t in all_results["A"] if t["pnl"] > 0)
    a_gl = abs(sum(t["pnl"] for t in all_results["A"] if t["pnl"] < 0))
    a_pf = a_gp / a_gl if a_gl > 0 else 0
    print(f"\n  [검증] Baseline A: {a_trades}건, PF={a_pf:.2f}, PnL={a_pnl:+,.0f}")
    if abs(a_pf - 2.61) > 0.15 or abs(a_trades - 326) > 10:
        print(f"  ⚠ Baseline 재현 불일치 (기대: 326건/PF 2.61)")

    # 시장 국면 맵
    regime_map = build_market_regime_map(DB_PATH)

    # 메트릭 계산
    metrics = compute_metrics(all_results, regime_map)

    # 차트 + 보고서
    print()
    generate_charts(metrics, all_results)
    generate_report(metrics, all_results)
    print(f"\n[REPORT] {REPORT_PATH}")

    print("\n" + "=" * 64)


if __name__ == "__main__":
    asyncio.run(main())
