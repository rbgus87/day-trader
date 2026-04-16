"""scripts/analyze_baseline.py — baseline 백테스트 종합 분석.

4가지 가설 검증:
  1. 자리 점유 (forced_close 보유시간 + time_stop 시뮬)
  2. 시장 국면별 PF (월별/분기별 + 지수 추세 라벨)
  3. 종목별 PF 편차 (PF<1 종목 영향 + ATR 상관)
  4. ATR 필터 적정성 (3%/4%/5% 시뮬)

사용:
    python scripts/analyze_baseline.py
    python scripts/analyze_baseline.py --start 2025-04-01 --end 2026-04-10
"""

import argparse
import asyncio
import os
import pickle
import sqlite3
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig
from core.cost_model import TradeCosts, apply_sell_costs
from data.db_manager import DbManager

# matplotlib 한글 설정
plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False

CHARTS_DIR = Path("reports/charts")
REPORT_PATH = Path("reports/baseline_analysis.md")
DB_PATH = "daytrader.db"


# ======================================================================
# 0. 백테스트 실행 (trades 수집)
# ======================================================================

def _simulate_one(args: tuple) -> dict:
    """워커: 단일 종목 multi-day 백테스트."""
    (
        ticker, ticker_market, candles_pickle,
        trading_config, backtest_config, market_map,
    ) = args

    import asyncio as _asyncio
    from backtest.backtester import Backtester as _Bt
    from strategy.momentum_strategy import MomentumStrategy

    candles = pickle.loads(candles_pickle)
    strategy = MomentumStrategy(trading_config)
    bt = _Bt(
        db=None,
        config=trading_config,
        backtest_config=backtest_config,
        ticker_market=ticker_market,
        market_strong_by_date=market_map,
    )
    result = _asyncio.run(bt.run_multi_day_cached(ticker, candles, strategy))
    # ticker 태깅
    for t in result.get("trades", []):
        t["ticker"] = ticker
        t["ticker_market"] = ticker_market
    return result


async def collect_all_trades(start: str, end: str) -> list[dict]:
    """전체 유니버스 백테스트 실행 → 태깅된 trades 반환."""
    app_config = AppConfig.from_yaml()
    base_config = app_config.trading

    bt_cfg_raw = yaml.safe_load(
        open("config.yaml", encoding="utf-8")
    ).get("backtest", {})
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

    print(f"[LOAD] 캔들 로딩 ({len(stocks)}종목, {start}~{end})...")
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
    print(f"[RUN] 백테스트 워커 {workers}...")

    tasks = [
        (
            tk, ticker_to_market.get(tk, "unknown"),
            candles_cache[tk], base_config, backtest_config, market_map,
        )
        for tk in candles_cache
    ]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        kpis = list(executor.map(_simulate_one, tasks))

    all_trades = []
    for kpi in kpis:
        if kpi:
            all_trades.extend(kpi.get("trades", []))

    print(f"[DONE] 총 {len(all_trades)}건 수집\n")
    return all_trades


# ======================================================================
# 1. 자리 점유 분석
# ======================================================================

def analyze_position_occupancy(trades: list[dict], db_path: str) -> dict:
    """forced_close 보유시간 분포 + time_stop 시뮬."""
    import pandas as pd

    forced = [t for t in trades if t.get("exit_reason") == "forced_close"]
    print(f"[분석1] 자리 점유: forced_close {len(forced)}건 분석")

    conn = sqlite3.connect(db_path)
    costs = TradeCosts(commission_rate=0.00015, slippage_rate=0.0003, tax_rate=0.0015)

    results = []
    for t in forced:
        entry_ts = pd.to_datetime(t["entry_ts"])
        exit_ts = pd.to_datetime(t["exit_ts"])
        hold_min = (exit_ts - entry_ts).total_seconds() / 60
        pnl_pct = t.get("pnl_pct", 0.0)
        ticker = t.get("ticker", "")

        # 진입 후 각 시점 가격 조회 (60/120/180분)
        entry_date = entry_ts.strftime("%Y-%m-%d")
        net_entry = t["entry_price"] * (1 + costs.slippage_rate)
        net_entry = net_entry * (1 + costs.commission_rate)

        checkpoints = {}
        for mins in [60, 120, 180]:
            ts_check = entry_ts + timedelta(minutes=mins)
            ts_str = ts_check.strftime("%Y-%m-%d %H:%M:%S")
            cur = conn.execute(
                "SELECT close FROM intraday_candles "
                "WHERE ticker=? AND tf='1m' AND ts >= ? AND ts <= ? "
                "ORDER BY ts LIMIT 1",
                (ticker, ts_str, f"{entry_date} 15:35:00"),
            )
            row = cur.fetchone()
            if row:
                _, net_exit_cp = apply_sell_costs(row[0], costs)
                checkpoints[mins] = {
                    "price": row[0],
                    "pnl_pct": (net_exit_cp - net_entry) / net_entry,
                }
            else:
                checkpoints[mins] = None

        results.append({
            "ticker": ticker,
            "entry_ts": entry_ts,
            "exit_ts": exit_ts,
            "hold_min": hold_min,
            "pnl": t["pnl"],
            "pnl_pct": pnl_pct,
            "checkpoints": checkpoints,
        })

    conn.close()

    # 보유시간 구간별 집계
    # 진입 09:05~12:00, 청산 15:10 → 최소 ~190분. 실질적 구간으로 분류
    bins = [0, 240, 300, 330, 360, 9999]
    labels = ["<4시간", "4~5시간", "5~5.5시간", "5.5~6시간", "6시간+"]
    bin_stats = {}
    for i, label in enumerate(labels):
        lo, hi = bins[i], bins[i + 1]
        subset = [r for r in results if lo <= r["hold_min"] < hi]
        avg_pnl = np.mean([r["pnl"] for r in subset]) if subset else 0
        avg_pnl_pct = np.mean([r["pnl_pct"] for r in subset]) if subset else 0
        bin_stats[label] = {
            "count": len(subset),
            "avg_pnl": avg_pnl,
            "avg_pnl_pct": avg_pnl_pct,
        }

    # 자리 점유 비율: 전체 forced_close 중 PnL < +0.5% 비율
    occupying = [r for r in results if r["pnl_pct"] < 0.005]
    occupy_ratio = len(occupying) / len(results) if results else 0
    occupy_avg_pnl = np.mean([r["pnl"] for r in occupying]) if occupying else 0

    # 각 시점별 time_stop 효과
    checkpoint_analysis = {}
    for mins in [60, 120, 180]:
        valid = [r for r in results if r["checkpoints"].get(mins) is not None]
        better = worse = 0
        pnl_diff_sum = 0.0
        for r in valid:
            cp_pnl = r["checkpoints"][mins]["pnl_pct"]
            diff = cp_pnl - r["pnl_pct"]
            pnl_diff_sum += diff
            if cp_pnl > r["pnl_pct"]:
                better += 1
            elif cp_pnl < r["pnl_pct"]:
                worse += 1

        # 해당 시점에 +0.5% 이상이었지만 결국 forced_close로 하락한 비율
        profitable_then_lost = [
            r for r in valid
            if r["checkpoints"][mins]["pnl_pct"] >= 0.005
        ]

        checkpoint_analysis[mins] = {
            "valid": len(valid),
            "better": better,
            "worse": worse,
            "avg_diff": pnl_diff_sum / len(valid) if valid else 0,
            "profitable_then_lost": len(profitable_then_lost),
            "profitable_then_lost_ratio": len(profitable_then_lost) / len(valid) if valid else 0,
        }

    return {
        "total_forced": len(forced),
        "bin_stats": bin_stats,
        "occupy_ratio": occupy_ratio,
        "occupy_count": len(occupying),
        "occupy_avg_pnl": occupy_avg_pnl,
        "checkpoint_analysis": checkpoint_analysis,
        "results": results,
    }


def plot_occupancy(analysis: dict):
    """자리 점유 차트 생성."""
    # 1. 보유시간 히스토그램
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    labels = list(analysis["bin_stats"].keys())
    counts = [analysis["bin_stats"][l]["count"] for l in labels]
    avg_pnls = [analysis["bin_stats"][l]["avg_pnl_pct"] * 100 for l in labels]

    ax1 = axes[0]
    bars = ax1.bar(labels, counts, color=["#4CAF50" if p > 0 else "#F44336" for p in avg_pnls],
                   edgecolor="white", linewidth=0.5)
    ax1.set_title("forced_close 보유시간 분포", fontsize=13, fontweight="bold")
    ax1.set_ylabel("거래 수")
    for bar, cnt in zip(bars, counts):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                 str(cnt), ha="center", va="bottom", fontsize=10)

    ax2 = axes[1]
    colors = ["#4CAF50" if p > 0 else "#F44336" for p in avg_pnls]
    bars2 = ax2.bar(labels, avg_pnls, color=colors, edgecolor="white", linewidth=0.5)
    ax2.set_title("구간별 평균 PnL%", fontsize=13, fontweight="bold")
    ax2.set_ylabel("평균 PnL (%)")
    ax2.axhline(y=0, color="gray", linewidth=0.8, linestyle="--")
    for bar, val in zip(bars2, avg_pnls):
        ax2.text(bar.get_x() + bar.get_width() / 2,
                 bar.get_height() + (0.01 if val >= 0 else -0.03),
                 f"{val:.2f}%", ha="center", va="bottom" if val >= 0 else "top", fontsize=9)

    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "occupancy_distribution.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  -> occupancy_distribution.png")

    # 2. 각 시점별 time_stop scatter (60/120/180분)
    results = analysis["results"]
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    for ax, mins in zip(axes, [60, 120, 180]):
        valid = [r for r in results if r["checkpoints"].get(mins) is not None]
        if not valid:
            continue
        actual = [r["pnl_pct"] * 100 for r in valid]
        virtual = [r["checkpoints"][mins]["pnl_pct"] * 100 for r in valid]
        ax.scatter(actual, virtual, alpha=0.4, s=20, c="#2196F3")
        all_vals = actual + virtual
        lim = max(abs(min(all_vals)), abs(max(all_vals))) * 1.1
        ax.plot([-lim, lim], [-lim, lim], "k--", alpha=0.3, linewidth=0.8)
        ax.set_xlabel("실제 forced_close PnL%")
        ax.set_ylabel(f"{mins}분 시점 PnL%")
        cp = analysis["checkpoint_analysis"][mins]
        ax.set_title(f"{mins}분 컷 (유리:{cp['better']} / 불리:{cp['worse']})", fontsize=11, fontweight="bold")
        ax.axhline(y=0.5, color="green", alpha=0.3, linestyle=":")
        ax.axhline(y=0, color="gray", alpha=0.3, linestyle="--")
        ax.axvline(x=0, color="gray", alpha=0.3, linestyle="--")
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "time_stop_scatter.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  -> time_stop_scatter.png")


# ======================================================================
# 2. 시장 국면별 PF 분해
# ======================================================================

def analyze_market_regime(trades: list[dict], db_path: str) -> dict:
    """월별/분기별 시장 국면 PF 분해."""
    import pandas as pd

    print(f"[분석2] 시장 국면별 PF: {len(trades)}건 분석")

    # 지수 데이터 로드
    conn = sqlite3.connect(db_path)
    index_data = {}
    for code, name in [("001", "kospi"), ("101", "kosdaq")]:
        cur = conn.execute(
            "SELECT dt, close FROM index_candles WHERE index_code=? ORDER BY dt",
            (code,),
        )
        rows = cur.fetchall()
        index_data[name] = {r[0]: r[1] for r in rows}
    conn.close()

    # 월별 지수 수익률 계산
    def get_monthly_returns(data: dict, year_months: list) -> dict:
        """각 월의 시작/종료 close 기반 수익률."""
        result = {}
        sorted_dates = sorted(data.keys())
        for ym in year_months:
            month_dates = [d for d in sorted_dates if d[:6] == ym.replace("-", "")]
            if len(month_dates) < 2:
                continue
            first_close = data[month_dates[0]]
            last_close = data[month_dates[-1]]
            ret = (last_close - first_close) / first_close
            result[ym] = ret
        return result

    # 거래 날짜에서 월 목록 추출
    trade_months = set()
    trade_quarters = set()
    for t in trades:
        ts = pd.to_datetime(t["entry_ts"])
        ym = ts.strftime("%Y-%m")
        trade_months.add(ym)
        q = (ts.month - 1) // 3 + 1
        trade_quarters.add(f"{ts.year}-Q{q}")

    year_months = sorted(trade_months)

    # 월별 지수 수익률
    kospi_monthly = get_monthly_returns(index_data["kospi"], year_months)
    kosdaq_monthly = get_monthly_returns(index_data["kosdaq"], year_months)

    # 시장 국면 라벨링
    def regime_label(ret: float) -> str:
        if ret >= 0.05:
            return "강세"
        elif ret <= -0.05:
            return "약세"
        return "횡보"

    # 월별 집계
    monthly_stats = []
    for ym in year_months:
        month_trades = [
            t for t in trades
            if pd.to_datetime(t["entry_ts"]).strftime("%Y-%m") == ym
        ]
        if not month_trades:
            continue

        gp = sum(t["pnl"] for t in month_trades if t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in month_trades if t["pnl"] < 0))
        pf = gp / gl if gl > 0 else float("inf")

        exit_dist = Counter(t.get("exit_reason", "?") for t in month_trades)

        kospi_ret = kospi_monthly.get(ym, 0)
        kosdaq_ret = kosdaq_monthly.get(ym, 0)
        avg_ret = (kospi_ret + kosdaq_ret) / 2
        regime = regime_label(avg_ret)

        monthly_stats.append({
            "month": ym,
            "trades": len(month_trades),
            "pf": pf,
            "pnl": sum(t["pnl"] for t in month_trades),
            "kospi_ret": kospi_ret,
            "kosdaq_ret": kosdaq_ret,
            "regime": regime,
            "exit_dist": dict(exit_dist),
        })

    # 분기별 집계
    quarterly_stats = []
    for yq in sorted(trade_quarters):
        year, qstr = yq.split("-")
        q = int(qstr[1])
        q_start_month = (q - 1) * 3 + 1
        q_months = [f"{year}-{m:02d}" for m in range(q_start_month, q_start_month + 3)]

        q_trades = [
            t for t in trades
            if pd.to_datetime(t["entry_ts"]).strftime("%Y-%m") in q_months
        ]
        if not q_trades:
            continue

        gp = sum(t["pnl"] for t in q_trades if t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in q_trades if t["pnl"] < 0))
        pf = gp / gl if gl > 0 else float("inf")

        # 분기 지수 수익률 (분기 내 월별 수익률 합산)
        kospi_q = sum(kospi_monthly.get(m, 0) for m in q_months)
        kosdaq_q = sum(kosdaq_monthly.get(m, 0) for m in q_months)
        avg_q = (kospi_q + kosdaq_q) / 2
        regime = regime_label(avg_q)

        exit_dist = Counter(t.get("exit_reason", "?") for t in q_trades)

        quarterly_stats.append({
            "quarter": yq,
            "trades": len(q_trades),
            "pf": pf,
            "pnl": sum(t["pnl"] for t in q_trades),
            "kospi_ret": kospi_q,
            "kosdaq_ret": kosdaq_q,
            "regime": regime,
            "exit_dist": dict(exit_dist),
        })

    # 국면별 집계 (월별 기준 — 분기는 희석 위험)
    regime_agg = defaultdict(lambda: {"trades": 0, "gp": 0, "gl": 0, "pnl": 0})
    for ms in monthly_stats:
        r = ms["regime"]
        month_trades_for_regime = [
            t for t in trades
            if pd.to_datetime(t["entry_ts"]).strftime("%Y-%m") == ms["month"]
        ]
        gp = sum(t["pnl"] for t in month_trades_for_regime if t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in month_trades_for_regime if t["pnl"] < 0))
        regime_agg[r]["trades"] += len(month_trades_for_regime)
        regime_agg[r]["gp"] += gp
        regime_agg[r]["gl"] += gl
        regime_agg[r]["pnl"] += sum(t["pnl"] for t in month_trades_for_regime)

    for r in regime_agg:
        gl = regime_agg[r]["gl"]
        regime_agg[r]["pf"] = regime_agg[r]["gp"] / gl if gl > 0 else float("inf")

    return {
        "monthly": monthly_stats,
        "quarterly": quarterly_stats,
        "regime_summary": dict(regime_agg),
    }


def _trade_in_quarter(t: dict, yq: str) -> bool:
    import pandas as pd
    ts = pd.to_datetime(t["entry_ts"])
    year, qstr = yq.split("-")
    q = int(qstr[1])
    return ts.year == int(year) and (ts.month - 1) // 3 + 1 == q


def plot_market_regime(analysis: dict):
    """시장 국면 차트."""
    monthly = analysis["monthly"]
    quarterly = analysis["quarterly"]

    if not monthly:
        return

    # 월별 PF + 거래수 차트
    fig, ax1 = plt.subplots(figsize=(14, 5))

    months = [m["month"] for m in monthly]
    pfs = [min(m["pf"], 10) for m in monthly]  # cap for display
    trade_counts = [m["trades"] for m in monthly]
    regimes = [m["regime"] for m in monthly]

    regime_colors = {"강세": "#4CAF50", "횡보": "#FF9800", "약세": "#F44336"}
    colors = [regime_colors.get(r, "#999") for r in regimes]

    ax1.bar(months, pfs, color=colors, alpha=0.7, edgecolor="white")
    ax1.axhline(y=1.0, color="red", linestyle="--", alpha=0.5, label="PF=1 (손익분기)")
    ax1.set_ylabel("Profit Factor")
    ax1.set_title("월별 PF + 시장 국면", fontsize=13, fontweight="bold")
    ax1.tick_params(axis="x", rotation=45)

    ax2 = ax1.twinx()
    ax2.plot(months, trade_counts, "ko-", markersize=4, alpha=0.5, label="거래 수")
    ax2.set_ylabel("거래 수")

    # 범례
    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor="#4CAF50", label="강세 (+5%↑)"),
        Patch(facecolor="#FF9800", label="횡보 (±5%)"),
        Patch(facecolor="#F44336", label="약세 (-5%↓)"),
    ]
    ax1.legend(handles=legend_elements, loc="upper left")
    ax2.legend(loc="upper right")

    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "market_regime_monthly.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  -> market_regime_monthly.png")

    # 분기별 PF 차트
    if quarterly:
        fig, ax = plt.subplots(figsize=(10, 5))
        quarters = [q["quarter"] for q in quarterly]
        q_pfs = [min(q["pf"], 10) for q in quarterly]
        q_regimes = [q["regime"] for q in quarterly]
        q_colors = [regime_colors.get(r, "#999") for r in q_regimes]
        q_trades = [q["trades"] for q in quarterly]

        bars = ax.bar(quarters, q_pfs, color=q_colors, alpha=0.7, edgecolor="white")
        ax.axhline(y=1.0, color="red", linestyle="--", alpha=0.5)
        ax.set_title("분기별 PF + 시장 국면", fontsize=13, fontweight="bold")
        ax.set_ylabel("Profit Factor")
        for bar, cnt, pf_val in zip(bars, q_trades, q_pfs):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.05,
                    f"n={cnt}\nPF={pf_val:.2f}", ha="center", va="bottom", fontsize=9)

        ax.legend(handles=legend_elements, loc="upper left")
        plt.tight_layout()
        plt.savefig(CHARTS_DIR / "market_regime_quarterly.png", dpi=150, bbox_inches="tight")
        plt.close()
        print("  -> market_regime_quarterly.png")


# ======================================================================
# 3. 종목별 PF 편차
# ======================================================================

def analyze_ticker_pf(trades: list[dict], db_path: str) -> dict:
    """종목별 PF, ATR 상관 분석."""
    import pandas as pd

    print(f"[분석3] 종목별 PF: {len(trades)}건 분석")

    # 종목별 집계
    ticker_stats = defaultdict(lambda: {"trades": [], "gp": 0, "gl": 0})
    for t in trades:
        tk = t.get("ticker", "?")
        ticker_stats[tk]["trades"].append(t)
        if t["pnl"] > 0:
            ticker_stats[tk]["gp"] += t["pnl"]
        elif t["pnl"] < 0:
            ticker_stats[tk]["gl"] += abs(t["pnl"])

    ticker_list = []
    for tk, s in ticker_stats.items():
        pf = s["gp"] / s["gl"] if s["gl"] > 0 else float("inf")
        total_pnl = sum(t["pnl"] for t in s["trades"])
        ticker_list.append({
            "ticker": tk,
            "trades": len(s["trades"]),
            "pf": pf,
            "pnl": total_pnl,
            "gp": s["gp"],
            "gl": s["gl"],
        })

    ticker_list.sort(key=lambda x: x["pf"], reverse=True)

    # ATR% 조회
    conn = sqlite3.connect(db_path)
    ticker_atr = {}
    for item in ticker_list:
        cur = conn.execute(
            "SELECT atr_pct FROM ticker_atr WHERE ticker=? ORDER BY dt DESC LIMIT 1",
            (item["ticker"],),
        )
        row = cur.fetchone()
        item["atr_pct"] = row[0] if row else None
        if row:
            ticker_atr[item["ticker"]] = row[0]
    conn.close()

    # PF<1, PF<0.5 분류
    pf_above_1 = [t for t in ticker_list if t["pf"] > 1.0 and t["trades"] > 0]
    pf_below_1 = [t for t in ticker_list if t["pf"] <= 1.0 and t["trades"] > 0]
    pf_below_05 = [t for t in ticker_list if t["pf"] <= 0.5 and t["trades"] > 0]

    # PF<1 종목 제외 시 전체 PF
    trades_pf_above = [t for t in trades if t.get("ticker") in {x["ticker"] for x in pf_above_1}]
    gp_above = sum(t["pnl"] for t in trades_pf_above if t["pnl"] > 0)
    gl_above = abs(sum(t["pnl"] for t in trades_pf_above if t["pnl"] < 0))
    pf_without_losers = gp_above / gl_above if gl_above > 0 else float("inf")

    # ATR% 평균: PF>1 vs PF<1
    atr_above = [t["atr_pct"] for t in pf_above_1 if t["atr_pct"] is not None]
    atr_below = [t["atr_pct"] for t in pf_below_1 if t["atr_pct"] is not None]
    avg_atr_above = np.mean(atr_above) if atr_above else 0
    avg_atr_below = np.mean(atr_below) if atr_below else 0

    # 전체 PF (참조)
    total_gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    total_gl = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    overall_pf = total_gp / total_gl if total_gl > 0 else float("inf")

    return {
        "ticker_list": ticker_list,
        "pf_above_1_count": len(pf_above_1),
        "pf_below_1_count": len(pf_below_1),
        "pf_below_05_count": len(pf_below_05),
        "overall_pf": overall_pf,
        "pf_without_losers": pf_without_losers,
        "trades_without_losers": len(trades_pf_above),
        "avg_atr_pf_above": avg_atr_above,
        "avg_atr_pf_below": avg_atr_below,
    }


def plot_ticker_pf(analysis: dict):
    """종목별 PF 차트."""
    tickers = analysis["ticker_list"]
    if not tickers:
        return

    # 1. PF 분포 bar chart (top/bottom)
    fig, ax = plt.subplots(figsize=(16, 6))
    sorted_t = sorted(tickers, key=lambda x: x["pf"])
    names = [t["ticker"] for t in sorted_t]
    pfs = [min(t["pf"], 10) for t in sorted_t]
    colors = ["#4CAF50" if p > 1 else "#F44336" for p in pfs]

    ax.barh(range(len(names)), pfs, color=colors, edgecolor="white", linewidth=0.3, height=0.7)
    ax.axvline(x=1.0, color="black", linestyle="--", alpha=0.5)
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=6)
    ax.set_xlabel("Profit Factor")
    ax.set_title(f"종목별 PF 분포 (PF>1: {analysis['pf_above_1_count']}개 / PF<1: {analysis['pf_below_1_count']}개)",
                 fontsize=13, fontweight="bold")

    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "ticker_pf_distribution.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  -> ticker_pf_distribution.png")

    # 2. ATR% vs PF 산점도
    valid = [t for t in tickers if t["atr_pct"] is not None and t["trades"] > 0]
    if valid:
        fig, ax = plt.subplots(figsize=(8, 6))
        atrs = [t["atr_pct"] for t in valid]
        pf_vals = [min(t["pf"], 10) for t in valid]
        sizes = [t["trades"] * 5 for t in valid]

        scatter = ax.scatter(atrs, pf_vals, s=sizes, alpha=0.6, c=pf_vals,
                             cmap="RdYlGn", vmin=0, vmax=5, edgecolors="gray", linewidth=0.5)
        ax.axhline(y=1.0, color="red", linestyle="--", alpha=0.4)
        ax.set_xlabel("ATR% (최신)")
        ax.set_ylabel("Profit Factor")
        ax.set_title("ATR% vs PF (크기=거래수)", fontsize=13, fontweight="bold")
        plt.colorbar(scatter, ax=ax, label="PF")

        # 상관계수 (PF=inf 제외)
        finite = [t for t in valid if t["pf"] != float("inf")]
        if len(finite) > 2:
            corr = np.corrcoef([t["atr_pct"] for t in finite], [t["pf"] for t in finite])[0, 1]
            ax.text(0.05, 0.95, f"r = {corr:.3f}", transform=ax.transAxes,
                    fontsize=11, va="top", bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5))

        plt.tight_layout()
        plt.savefig(CHARTS_DIR / "atr_vs_pf_scatter.png", dpi=150, bbox_inches="tight")
        plt.close()
        print("  -> atr_vs_pf_scatter.png")


# ======================================================================
# 4. ATR 필터 시뮬
# ======================================================================

def analyze_atr_filter(trades: list[dict], db_path: str) -> dict:
    """ATR 필터 강화 시뮬레이션."""
    print(f"[분석4] ATR 필터 시뮬: {len(trades)}건 분석")

    # 종목별 최신 ATR%
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "SELECT ticker, atr_pct FROM ticker_atr "
        "WHERE (ticker, dt) IN (SELECT ticker, MAX(dt) FROM ticker_atr GROUP BY ticker)"
    )
    latest_atr = {r[0]: r[1] for r in cur.fetchall()}
    conn.close()

    # 현재 유니버스 ATR% 분포
    uni = yaml.safe_load(open("config/universe.yaml", encoding="utf-8"))
    stocks = uni.get("stocks", [])
    all_tickers = {s["ticker"] for s in stocks}

    atr_dist = {tk: latest_atr.get(tk) for tk in all_tickers if latest_atr.get(tk) is not None}

    # 각 임계값별 시뮬 (현재 유니버스가 이미 ATR≥2~3% 필터됨, 5~8% 구간이 핵심)
    thresholds = [0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08]
    sims = []

    for thresh in thresholds:
        surviving = {tk for tk, atr in atr_dist.items() if atr >= thresh * 100}
        filtered_trades = [t for t in trades if t.get("ticker") in surviving]

        gp = sum(t["pnl"] for t in filtered_trades if t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in filtered_trades if t["pnl"] < 0))
        pf = gp / gl if gl > 0 else float("inf")
        total_pnl = sum(t["pnl"] for t in filtered_trades)
        per_trade = total_pnl / len(filtered_trades) if filtered_trades else 0

        exit_dist = Counter(t.get("exit_reason", "?") for t in filtered_trades)

        sims.append({
            "threshold": thresh,
            "surviving_tickers": len(surviving),
            "total_tickers": len(atr_dist),
            "trades": len(filtered_trades),
            "pf": pf,
            "pnl": total_pnl,
            "per_trade": per_trade,
            "exit_dist": dict(exit_dist),
        })

    return {
        "atr_distribution": atr_dist,
        "simulations": sims,
    }


def plot_atr_filter(analysis: dict):
    """ATR 필터 차트."""
    sims = analysis["simulations"]
    if not sims:
        return

    # 1. ATR% 분포 히스토그램
    atr_vals = list(analysis["atr_distribution"].values())
    if atr_vals:
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.hist(atr_vals, bins=20, color="#2196F3", edgecolor="white", alpha=0.7)
        for thresh in [3, 4, 5]:
            ax.axvline(x=thresh, color="red", linestyle="--", alpha=0.5,
                       label=f"ATR {thresh}% 임계")
        ax.set_xlabel("ATR% (최신)")
        ax.set_ylabel("종목 수")
        ax.set_title("유니버스 ATR% 분포", fontsize=13, fontweight="bold")
        ax.legend()
        plt.tight_layout()
        plt.savefig(CHARTS_DIR / "atr_distribution.png", dpi=150, bbox_inches="tight")
        plt.close()
        print("  -> atr_distribution.png")

    # 2. 임계값별 PF + 거래수 변화
    fig, ax1 = plt.subplots(figsize=(8, 5))
    thresholds = [f"{s['threshold']*100:.0f}%" for s in sims]
    pfs = [min(s["pf"], 10) for s in sims]
    trade_counts = [s["trades"] for s in sims]

    x = range(len(thresholds))
    bars = ax1.bar(x, pfs, color="#4CAF50", alpha=0.7, width=0.4, label="PF")
    ax1.set_ylabel("Profit Factor")
    ax1.axhline(y=1.0, color="red", linestyle="--", alpha=0.4)

    ax2 = ax1.twinx()
    ax2.plot(x, trade_counts, "ro-", markersize=8, label="거래 수")
    ax2.set_ylabel("거래 수")

    ax1.set_xticks(x)
    ax1.set_xticklabels(thresholds)
    ax1.set_xlabel("ATR% 최소 임계값")
    ax1.set_title("ATR 필터 강화 시뮬레이션", fontsize=13, fontweight="bold")

    for bar, pf_val, tc in zip(bars, pfs, trade_counts):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.05,
                 f"PF={pf_val:.2f}\nn={tc}", ha="center", va="bottom", fontsize=9)

    ax1.legend(loc="upper left")
    ax2.legend(loc="upper right")
    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "atr_filter_simulation.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("  -> atr_filter_simulation.png")


# ======================================================================
# 5. 보고서 생성
# ======================================================================

def generate_report(
    occupancy: dict,
    regime: dict,
    ticker_pf: dict,
    atr_filter: dict,
    total_trades: int,
) -> str:
    """Markdown 보고서 생성."""
    lines = []
    a = lines.append

    a("# Baseline 종합 분석 보고서")
    a("")
    a(f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    a(f"> 총 거래: {total_trades}건 / PF: {ticker_pf['overall_pf']:.2f}")
    a("")
    a("---")
    a("")

    # ── 1. 자리 점유 ──
    a("## 1. 자리 점유 분석")
    a("")
    a(f"**가설**: forced_close {occupancy['total_forced']}건 중 상당수가 60분+ 보유하며 +0.5% 미달 → 자리만 차지")
    a("")
    a("### 보유시간 분포")
    a("")
    a("| 구간 | 거래수 | 비율 | 평균 PnL% |")
    a("|------|--------|------|-----------|")
    total_f = occupancy["total_forced"]
    for label, stats in occupancy["bin_stats"].items():
        ratio = stats["count"] / total_f * 100 if total_f else 0
        a(f"| {label} | {stats['count']} | {ratio:.1f}% | {stats['avg_pnl_pct']*100:+.3f}% |")

    a("")
    a("![보유시간 분포](charts/occupancy_distribution.png)")
    a("")

    a("### 자리 점유 비율")
    a("")
    a(f"- forced_close 중 PnL < +0.5%: **{occupancy['occupy_count']}건 / {occupancy['total_forced']}건 ({occupancy['occupy_ratio']*100:.1f}%)**")
    a(f"- 해당 거래 평균 PnL: {occupancy['occupy_avg_pnl']:+.1f}원")
    a(f"- 참고: 진입 09:05~12:00, 청산 15:10 → 전체 forced_close 보유시간 190~370분")
    a("")

    a("### 시점별 time_stop 시뮬레이션")
    a("")
    a("| 시점 | 조회 | 컷이 유리 | 컷이 불리 | 평균 PnL% 차이 | 놓친 수익 비율 |")
    a("|------|------|----------|----------|---------------|--------------|")
    for mins in [60, 120, 180]:
        cp = occupancy["checkpoint_analysis"][mins]
        a(f"| {mins}분 | {cp['valid']}건 | {cp['better']}건 | {cp['worse']}건 | {cp['avg_diff']*100:+.4f}% | {cp['profitable_then_lost']}건 ({cp['profitable_then_lost_ratio']*100:.1f}%) |")
    a("")
    a("- **놓친 수익**: 해당 시점에 PnL≥+0.5%였지만 결국 forced_close로 하락한 거래 비율")
    a("- **평균 차이 > 0**: 해당 시점 컷이 평균적으로 유리, **< 0**: 끝까지 보유가 유리")
    a("")
    a("![time_stop 비교](charts/time_stop_scatter.png)")
    a("")

    # 판정: 자리 점유 비율 + time_stop 실제 효과 복합 판단
    occupy_pct = occupancy["occupy_ratio"] * 100
    cp60 = occupancy["checkpoint_analysis"].get(60, {})
    cp120 = occupancy["checkpoint_analysis"].get(120, {})
    avg_diff_60 = cp60.get("avg_diff", 0)
    avg_diff_120 = cp120.get("avg_diff", 0)

    if occupy_pct > 50 and avg_diff_60 > 0:
        verdict = "**참** — 자리 점유 52%+ & 60분 컷이 평균 유리 → time_stop 도입 권장"
    elif occupy_pct > 50 and avg_diff_60 <= 0:
        verdict = f"**모호** — 자리 점유 {occupy_pct:.0f}%이나 60분 컷은 평균 불리({avg_diff_60*100:+.3f}%). 120분 컷 효과: {avg_diff_120*100:+.3f}%"
    elif occupy_pct > 30:
        verdict = "**모호** — 자리 점유가 존재하나 time_stop 효과가 제한적"
    else:
        verdict = "**거짓** — 자리 점유 비율이 낮아 현행 유지 적절"
    a(f"**가설 판정**: {verdict}")
    a("")
    a("---")
    a("")

    # ── 2. 시장 국면 ──
    a("## 2. 시장 국면별 PF 분해")
    a("")
    a("**가설**: 강세장에서만 수익이 나고 약세장에서는 손실 → 강세장 편향 시스템")
    a("")

    a("### 월별 집계")
    a("")
    a("| 월 | 국면 | 거래수 | PF | PnL | KOSPI | KOSDAQ |")
    a("|-----|------|--------|-----|------|-------|--------|")
    for ms in regime["monthly"]:
        pf_str = f"{ms['pf']:.2f}" if ms["pf"] != float("inf") else "∞"
        a(f"| {ms['month']} | {ms['regime']} | {ms['trades']} | {pf_str} | {ms['pnl']:+,.0f} | {ms['kospi_ret']*100:+.1f}% | {ms['kosdaq_ret']*100:+.1f}% |")
    a("")
    a("![월별 PF](charts/market_regime_monthly.png)")
    a("")

    a("### 분기별 집계")
    a("")
    a("| 분기 | 국면 | 거래수 | PF | PnL |")
    a("|------|------|--------|-----|------|")
    for qs in regime["quarterly"]:
        pf_str = f"{qs['pf']:.2f}" if qs["pf"] != float("inf") else "∞"
        a(f"| {qs['quarter']} | {qs['regime']} | {qs['trades']} | {pf_str} | {qs['pnl']:+,.0f} |")
    a("")
    a("![분기별 PF](charts/market_regime_quarterly.png)")
    a("")

    a("### 국면별 요약")
    a("")
    a("| 국면 | 거래수 | PF | 총 PnL |")
    a("|------|--------|-----|--------|")
    for r in ["강세", "횡보", "약세"]:
        if r in regime["regime_summary"]:
            s = regime["regime_summary"][r]
            pf_str = f"{s['pf']:.2f}" if s["pf"] != float("inf") else "∞"
            a(f"| {r} | {s['trades']} | {pf_str} | {s['pnl']:+,.0f} |")
    a("")

    # 판정
    regime_summary = regime["regime_summary"]
    bull_pf = regime_summary.get("강세", {}).get("pf", 0)
    bear_pf = regime_summary.get("약세", {}).get("pf", 0)
    bear_trades = regime_summary.get("약세", {}).get("trades", 0)
    sideways_pf = regime_summary.get("횡보", {}).get("pf", 0)
    sideways_trades = regime_summary.get("횡보", {}).get("trades", 0)

    if bear_trades == 0:
        verdict2 = f"**데이터 부족** — 약세장(월간 -5%↓) 구간이 1개월뿐. 강세 PF={bull_pf:.2f}, 횡보 PF={sideways_pf:.2f}"
    elif bear_pf < 1.0 and bull_pf > 2.0:
        verdict2 = f"**참** — 약세 PF={bear_pf:.2f} / 강세 PF={bull_pf:.2f}, 강세장 편향"
    elif bear_pf >= 1.0:
        verdict2 = f"**거짓** — 약세 PF={bear_pf:.2f}≥1, 전천후"
    else:
        verdict2 = f"**모호** — 약세 PF={bear_pf:.2f}, 데이터 추가 필요"
    a(f"**가설 판정**: {verdict2}")
    a("")
    a("---")
    a("")

    # ── 3. 종목별 PF ──
    a("## 3. 종목별 PF 편차")
    a("")
    a("**가설**: PF<1 종목이 전체 수익률을 끌어내림")
    a("")
    a(f"- 전체 PF: **{ticker_pf['overall_pf']:.2f}**")
    a(f"- PF>1 종목: **{ticker_pf['pf_above_1_count']}개**")
    a(f"- PF<1 종목: **{ticker_pf['pf_below_1_count']}개**")
    a(f"- PF<0.5 종목: **{ticker_pf['pf_below_05_count']}개**")
    a(f"- PF<1 종목 제외 시 PF: **{ticker_pf['pf_without_losers']:.2f}** ({ticker_pf['trades_without_losers']}건)")
    a("")
    a("### ATR% vs 수익성")
    a("")
    a(f"- PF>1 종목 평균 ATR%: **{ticker_pf['avg_atr_pf_above']:.2f}%**")
    a(f"- PF<1 종목 평균 ATR%: **{ticker_pf['avg_atr_pf_below']:.2f}%**")
    atr_diff = ticker_pf["avg_atr_pf_above"] - ticker_pf["avg_atr_pf_below"]
    if abs(atr_diff) > 0.5:
        a(f"- 차이: **{atr_diff:+.2f}%p** → ATR이 높은 종목이 {'수익성 우수' if atr_diff > 0 else '수익성 불량'}")
    else:
        a(f"- 차이: **{atr_diff:+.2f}%p** → ATR과 수익성 간 유의미한 차이 없음")
    a("")
    a("![종목별 PF](charts/ticker_pf_distribution.png)")
    a("")
    a("![ATR vs PF](charts/atr_vs_pf_scatter.png)")
    a("")

    # 상위/하위 종목
    a("### 상위 10 종목 (PF 기준)")
    a("")
    a("| 종목 | 거래수 | PF | PnL | ATR% |")
    a("|------|--------|-----|------|------|")
    for t in ticker_pf["ticker_list"][:10]:
        pf_str = f"{t['pf']:.2f}" if t["pf"] != float("inf") else "∞"
        atr_str = f"{t['atr_pct']:.1f}%" if t["atr_pct"] else "N/A"
        a(f"| {t['ticker']} | {t['trades']} | {pf_str} | {t['pnl']:+,.0f} | {atr_str} |")
    a("")

    a("### 하위 10 종목 (PF 기준)")
    a("")
    a("| 종목 | 거래수 | PF | PnL | ATR% |")
    a("|------|--------|-----|------|------|")
    for t in ticker_pf["ticker_list"][-10:]:
        pf_str = f"{t['pf']:.2f}" if t["pf"] != float("inf") else "∞"
        atr_str = f"{t['atr_pct']:.1f}%" if t["atr_pct"] else "N/A"
        a(f"| {t['ticker']} | {t['trades']} | {pf_str} | {t['pnl']:+,.0f} | {atr_str} |")
    a("")

    pf_improvement = ticker_pf["pf_without_losers"] - ticker_pf["overall_pf"]
    if pf_improvement > 0.5:
        verdict3 = f"**참** — PF<1 종목 제거 시 PF +{pf_improvement:.2f} 개선, 유니버스 정제 효과 있음"
    else:
        verdict3 = f"**모호** — PF<1 종목 제거 효과 +{pf_improvement:.2f}로 제한적"
    a(f"**가설 판정**: {verdict3}")
    a("")
    a("---")
    a("")

    # ── 4. ATR 필터 ──
    a("## 4. ATR 필터 적정성")
    a("")
    a("**가설**: ATR 필터를 강화하면 저변동성 종목 제거로 PF 개선")
    a("")
    a("> **한계**: 시점별 ATR 변동이 있으나 최신 ATR%로 일괄 적용하여 시뮬. 과거 시점의 ATR%는 현재와 다를 수 있음.")
    a("")

    a("| ATR 임계 | 남은 종목 | 거래수 | PF | 총 PnL | 거래당 PnL |")
    a("|----------|----------|--------|-----|--------|-----------|")
    for s in atr_filter["simulations"]:
        pf_str = f"{s['pf']:.2f}" if s["pf"] != float("inf") else "∞"
        a(f"| ≥{s['threshold']*100:.0f}% | {s['surviving_tickers']}/{s['total_tickers']} | {s['trades']} | {pf_str} | {s['pnl']:+,.0f} | {s['per_trade']:+,.0f} |")
    a("")
    a("![ATR 분포](charts/atr_distribution.png)")
    a("")
    a("![ATR 필터 시뮬](charts/atr_filter_simulation.png)")
    a("")

    # 최적 임계값 판정
    best_sim = max(atr_filter["simulations"], key=lambda s: s["per_trade"] if s["trades"] > 20 else -999)
    a(f"**최적 임계값**: ATR ≥ {best_sim['threshold']*100:.0f}% (거래당 PnL 기준, 거래수 {best_sim['trades']}건)")
    a("")
    a("---")
    a("")

    # ── 종합 ──
    a("## 종합 권장 사항")
    a("")
    a("| 가설 | 판정 | 권장 액션 |")
    a("|------|------|----------|")

    # 자리 점유
    if occupy_pct > 50 and avg_diff_60 > 0:
        action1 = "time_stop 60~90분 그리드 서치 (60/75/90분 × 임계 0.3%/0.5%/0.7%)"
    elif occupy_pct > 50:
        action1 = "time_stop 120~180분 구간 테스트 (60분은 역효과)"
    elif occupy_pct > 30:
        action1 = "time_stop 90~120분 소규모 테스트"
    else:
        action1 = "현행 유지"
    a(f"| 자리 점유 | {occupy_pct:.0f}% | {action1} |")

    # 강세장 편향
    if bear_trades == 0:
        action2 = "약세장 데이터 부족, 추가 기간 수집 후 재검증"
    elif bear_pf < 1.0:
        action2 = "약세장 방어 필터 강화 (시장 MA 10일 등)"
    else:
        action2 = "현행 유지"
    bear_label = f"bear PF={bear_pf:.1f}({bear_trades}건)" if bear_trades > 0 else f"bear 데이터 부족"
    a(f"| 강세장 편향 | bull PF={bull_pf:.1f} / {bear_label} | {action2} |")

    # 종목 PF
    if pf_improvement > 0.5:
        action3 = f"PF<1 하위 {ticker_pf['pf_below_1_count']}종목 검토, 유니버스 정제"
    else:
        action3 = "종목 필터 효과 제한적, 유니버스 유지"
    a(f"| 종목 PF 편차 | ΔPF={pf_improvement:+.2f} | {action3} |")

    # ATR 필터
    a(f"| ATR 필터 | 최적 {best_sim['threshold']*100:.0f}% | 해당 임계값으로 backtest 재검증 |")
    a("")

    report = "\n".join(lines)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"\n[REPORT] {REPORT_PATH} 저장 완료")

    return report


# ======================================================================
# main
# ======================================================================

async def main():
    parser = argparse.ArgumentParser(description="Baseline 백테스트 종합 분석")
    parser.add_argument("--start", default="2025-04-01")
    parser.add_argument("--end", default="2026-04-10")
    args = parser.parse_args()

    CHARTS_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 64)
    print(" Baseline 종합 분석 (Phase C)")
    print("=" * 64)
    print(f"  기간: {args.start} ~ {args.end}")
    print()

    # 0. trades 수집
    trades = await collect_all_trades(args.start, args.end)
    if not trades:
        print("ERROR: 거래 없음. 백테스트 실패.")
        return

    total_trades = len(trades)
    total_pnl = sum(t["pnl"] for t in trades)
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    overall_pf = gp / gl if gl > 0 else float("inf")
    exit_dist = Counter(t.get("exit_reason", "?") for t in trades)

    print(f"  총 거래: {total_trades}건")
    print(f"  PF: {overall_pf:.2f}")
    print(f"  PnL: {total_pnl:+,.0f}")
    print(f"  청산: {dict(exit_dist)}")
    print()

    # 1. 자리 점유
    occupancy = analyze_position_occupancy(trades, DB_PATH)
    plot_occupancy(occupancy)
    print()

    # 2. 시장 국면
    regime = analyze_market_regime(trades, DB_PATH)
    plot_market_regime(regime)
    print()

    # 3. 종목별 PF
    ticker_pf_result = analyze_ticker_pf(trades, DB_PATH)
    plot_ticker_pf(ticker_pf_result)
    print()

    # 4. ATR 필터
    atr_result = analyze_atr_filter(trades, DB_PATH)
    plot_atr_filter(atr_result)
    print()

    # 5. 보고서
    generate_report(occupancy, regime, ticker_pf_result, atr_result, total_trades)

    print("\n" + "=" * 64)
    print(" 완료")
    print("=" * 64)


if __name__ == "__main__":
    asyncio.run(main())
