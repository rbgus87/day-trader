"""scripts/grid_breakout_threshold.py — 돌파 폭 하한 그리드.

6 시나리오: B00(0%), B03(0.3%), B05(0.5%), B10(1.0%), B20(2.0%), B30(3.0%)

전일 고가 × (1 + pct)를 MomentumStrategy.set_prev_day_data에 주입하여
조건 7(price > prev_high)과 조건 10(last_close > prev_high)을 동시에 통제.
raw prev_high는 별도 속성으로 보존하여 각 trade의 실제 돌파 폭을 기록.

사용:
    python scripts/grid_breakout_threshold.py
"""
import asyncio
import os
import pickle
import sqlite3
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from loguru import logger

logger.remove()

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig, TradingConfig
from core.cost_model import TradeCosts, apply_buy_costs, apply_sell_costs
from core.indicators import calculate_atr_trailing_stop, get_latest_atr
from data.db_manager import DbManager
from strategy.momentum_strategy import MomentumStrategy

DB_PATH = "daytrader.db"


class BreakoutMomentumStrategy(MomentumStrategy):
    """전일 고가를 (1 + min_breakout_pct) 배 부풀려 돌파 체크.

    상위 generate_signal의 조건 7(tick.price > prev_high)과
    조건 10(last_close > prev_high)이 부풀린 값을 참조하므로
    자동으로 "돌파 폭 ≥ min_breakout_pct" 필터가 된다.
    원본 값은 _raw_prev_high로 보존해 trade별 실제 돌파 폭 계산에 사용.
    """

    def __init__(self, config, min_breakout_pct: float = 0.0):
        super().__init__(config)
        self._min_breakout_pct = min_breakout_pct
        self._raw_prev_high: float = 0.0

    def set_prev_day_data(self, high: float, volume: int) -> None:
        self._raw_prev_high = float(high)
        effective = high * (1.0 + self._min_breakout_pct)
        super().set_prev_day_data(effective, volume)


def run_f_day(day_candles, strategy, costs, ticker):
    """1종목 1일 Pure trailing 백테스트."""
    candles = day_candles.reset_index(drop=True)
    if candles.empty:
        return []
    trades, position = [], None
    as_of = None
    try:
        as_of = pd.to_datetime(candles["ts"].iloc[0]).strftime("%Y-%m-%d")
    except Exception:
        pass
    atr_pct = get_latest_atr(DB_PATH, ticker, as_of)

    def calc_trail(peak):
        if atr_pct is not None:
            return calculate_atr_trailing_stop(peak, atr_pct, 1.0, 0.02, 0.10)
        return peak * 0.995

    for idx, row in candles.iterrows():
        ts = row["ts"]
        if hasattr(ts, "time"):
            strategy.set_backtest_time(ts.time())
        tick = {
            "ticker": "BT", "price": float(row["close"]),
            "time": ts.strftime("%H%M") if hasattr(ts, "strftime") else "0000",
            "volume": int(row.get("volume", 0)),
        }
        if position is None:
            sig = strategy.generate_signal(candles.iloc[: idx + 1], tick)
            if sig and sig.side == "buy":
                strategy.on_entry()
                ep, ne = apply_buy_costs(float(row["close"]), costs)
                sl = ep * 0.92
                raw_prev = getattr(strategy, "_raw_prev_high", 0.0)
                bp = (ep - raw_prev) / raw_prev if raw_prev > 0 else 0.0
                position = {
                    "entry_ts": row["ts"], "entry_price": ep, "net_entry": ne,
                    "stop_loss": sl, "highest_price": float(row["high"]),
                    "breakout_pct": bp, "prev_day_high": raw_prev,
                }
                position["stop_loss"] = max(sl, calc_trail(position["highest_price"]))
            continue
        low, high, close = float(row["low"]), float(row["high"]), float(row["close"])
        is_last = idx == len(candles) - 1
        if high > position["highest_price"]:
            position["highest_price"] = high
            position["stop_loss"] = max(position["stop_loss"], calc_trail(high))
        if low <= position["stop_loss"]:
            ep_s, ne_s = apply_sell_costs(position["stop_loss"], costs)
            pnl = ne_s - position["net_entry"]
            reason = "trailing_stop" if position["stop_loss"] > position["entry_price"] * 0.975 else "stop_loss"
            trades.append({
                "entry_ts": position["entry_ts"], "exit_ts": row["ts"],
                "entry_price": position["entry_price"], "exit_price": ep_s,
                "pnl": pnl, "pnl_pct": pnl / position["net_entry"],
                "exit_reason": reason, "ticker": ticker,
                "breakout_pct": position["breakout_pct"],
                "prev_day_high": position["prev_day_high"],
            })
            position = None
            strategy.on_exit()
            continue
        if is_last:
            ep_s, ne_s = apply_sell_costs(close, costs)
            pnl = ne_s - position["net_entry"]
            trades.append({
                "entry_ts": position["entry_ts"], "exit_ts": row["ts"],
                "entry_price": position["entry_price"], "exit_price": ep_s,
                "pnl": pnl, "pnl_pct": pnl / position["net_entry"],
                "exit_reason": "forced_close", "ticker": ticker,
                "breakout_pct": position["breakout_pct"],
                "prev_day_high": position["prev_day_high"],
            })
            position = None
            strategy.on_exit()
    strategy.set_backtest_time(None)
    return trades


def run_multi(ticker, all_candles, tcfg, bcfg, tkm, mmap, min_bp):
    costs = TradeCosts(commission_rate=bcfg.commission, slippage_rate=bcfg.slippage, tax_rate=bcfg.tax)
    strat = BreakoutMomentumStrategy(tcfg, min_breakout_pct=min_bp)
    if all_candles.empty:
        return []
    df = all_candles.copy()
    if "date" not in df.columns:
        df["date"] = df["ts"].dt.date
    all_t, prev, dpnl = [], None, {}
    mf = getattr(tcfg, "market_filter_enabled", False)
    bl = getattr(tcfg, "blacklist_enabled", False)
    bl_l = getattr(tcfg, "blacklist_lookback_days", 5)
    bl_th = getattr(tcfg, "blacklist_loss_threshold", 3)
    rest = getattr(tcfg, "consecutive_loss_rest_enabled", False)
    rest_th = getattr(tcfg, "consecutive_loss_threshold", 3)
    for date, dc in df.groupby("date"):
        dd = dc.drop(columns=["date"]).reset_index(drop=True)
        skip = False
        if mf and tkm in ("kospi", "kosdaq"):
            s = mmap.get(date.strftime("%Y%m%d"))
            if s is not None and not s.get(tkm, True):
                skip = True
        if not skip and bl:
            from datetime import timedelta
            cut = date - timedelta(days=bl_l)
            ls = sum(
                1 for t in all_t
                if t.get("pnl", 0) < 0 and t.get("exit_ts") is not None
                and hasattr(t["exit_ts"], "date") and cut <= t["exit_ts"].date() < date
            )
            if ls >= bl_th:
                skip = True
        if not skip and rest:
            ps = sorted((d for d in dpnl if d < date), reverse=True)
            c = 0
            for d in ps:
                if dpnl[d] < 0:
                    c += 1
                else:
                    break
            if c >= rest_th:
                skip = True
        if skip:
            prev = dd
            continue
        strat.reset()
        if hasattr(strat, "set_ticker"):
            strat.set_ticker(ticker)
        if hasattr(strat, "set_prev_day_data") and prev is not None:
            strat.set_prev_day_data(float(prev["high"].max()), int(prev["volume"].sum()))
        dt = run_f_day(dd, strat, costs, ticker)
        all_t.extend(dt)
        dpnl[date] = sum(t.get("pnl", 0) for t in dt)
        prev = dd
    return all_t


def _worker(args):
    tk, tkm, cp, tcfg_dict, bcfg, mm, thr = args
    tcfg = TradingConfig(**tcfg_dict)
    return tk, run_multi(tk, pickle.loads(cp), tcfg, bcfg, tkm, mm, thr)


def build_regime_map(db_path):
    conn = sqlite3.connect(db_path)
    rm = {}
    for code, mkt in (("001", "kospi"), ("101", "kosdaq")):
        cur = conn.execute(
            "SELECT dt, close FROM index_candles WHERE index_code=? ORDER BY dt",
            (code,),
        )
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


async def main():
    app = AppConfig.from_yaml()
    cfg = app.trading
    bt_raw = yaml.safe_load(open("config.yaml", encoding="utf-8")).get("backtest", {})
    bcfg = BacktestConfig(
        commission=bt_raw.get("commission", 0.00015),
        tax=bt_raw.get("tax", 0.0015),
        slippage=bt_raw.get("slippage", 0.0003),
    )
    uni = yaml.safe_load(open("config/universe.yaml", encoding="utf-8"))
    stocks = uni["stocks"]
    tkm = {s["ticker"]: s.get("market", "?") for s in stocks}

    db = DbManager(app.db_path)
    await db.init()
    loader = Backtester(db=db, config=cfg, backtest_config=bcfg)
    cc = {}
    for s in stocks:
        c = await loader.load_candles(s["ticker"], "2025-04-01", "2026-04-10 23:59:59")
        if not c.empty:
            cc[s["ticker"]] = pickle.dumps(c)
    await db.close()
    mm = build_market_strong_by_date(app.db_path, ma_length=cfg.market_ma_length)
    regime_map = build_regime_map(DB_PATH)
    w = max(2, (os.cpu_count() or 2) - 1)
    base_dict = asdict(cfg)

    print(f"universe 로드: {len(cc)}/{len(stocks)}종목")
    print()

    scenarios = [
        ("B00", 0.000),
        ("B03", 0.003),
        ("B05", 0.005),
        ("B10", 0.010),
        ("B20", 0.020),
        ("B30", 0.030),
    ]

    results = {}
    for name, thr in scenarios:
        print(f"=== {name} (min_breakout={thr:.1%}) ===")
        tasks = [(tk, tkm.get(tk, "?"), cc[tk], base_dict, bcfg, mm, thr) for tk in cc]
        trades = []
        with ProcessPoolExecutor(max_workers=w) as ex:
            for _, t in ex.map(_worker, tasks):
                trades.extend(t)
        if not trades:
            print("  거래 없음")
            results[name] = None
            continue
        pnl_arr = np.array([t["pnl"] for t in trades])
        gp = float(pnl_arr[pnl_arr > 0].sum())
        gl = float(abs(pnl_arr[pnl_arr < 0].sum()))
        pf = gp / gl if gl > 0 else 0.0
        n = len(trades)
        ed = Counter(t.get("exit_reason", "?") for t in trades)
        wins = int((pnl_arr > 0).sum())
        per_trade = float(pnl_arr.sum()) / n if n else 0.0
        avg_bp = float(np.mean([t["breakout_pct"] for t in trades]))

        # 국면별 PF
        rt = defaultdict(list)
        for t in trades:
            try:
                m = pd.to_datetime(t["entry_ts"]).strftime("%Y-%m")
                rt[regime_map.get(m, "?")].append(t)
            except Exception:
                pass
        rpf = {}
        for r, tl in rt.items():
            rg = sum(t["pnl"] for t in tl if t["pnl"] > 0)
            rl = abs(sum(t["pnl"] for t in tl if t["pnl"] < 0))
            rpf[r] = rg / rl if rl > 0 else 0.0

        results[name] = {
            "thr": thr, "n": n, "pf": pf, "pnl": float(pnl_arr.sum()),
            "per_trade": per_trade, "ed": dict(ed),
            "wins": wins, "win_rate": wins / n if n else 0,
            "avg_breakout_pct": avg_bp, "trades": trades, "rpf": rpf,
        }
        print(f"  거래={n}, PF={pf:.2f}, PnL={pnl_arr.sum():+,.0f}, "
              f"per_trade={per_trade:+,.0f}, 승률={wins / n:.1%}, 평균돌파폭={avg_bp:.3%}")

    # 미세 돌파 PnL 버킷 (B00 기준)
    base = results.get("B00")
    bin_stats = []
    if base:
        bins = [(0.000, 0.003), (0.003, 0.005), (0.005, 0.010),
                (0.010, 0.020), (0.020, 0.030), (0.030, 1.0)]
        for lo, hi in bins:
            bucket = [t for t in base["trades"] if lo <= t["breakout_pct"] < hi]
            if not bucket:
                bin_stats.append((lo, hi, 0, 0.0, 0.0, 0.0))
                continue
            bn = len(bucket)
            bpnl = sum(t["pnl"] for t in bucket)
            bper = bpnl / bn
            bwr = sum(1 for t in bucket if t["pnl"] > 0) / bn
            bin_stats.append((lo, hi, bn, bpnl, bper, bwr))

    # 리포트 작성
    report = [
        "# Breakout Threshold Grid",
        "",
        "기간: 2025-04-01 ~ 2026-04-10",
        f"Universe: {len(cc)}종목",
        f"자본: 3,000,000원 (1주 가중 PF)",
        "",
        "## 시나리오별 결과",
        "",
        "| 시나리오 | 최소 돌파 | 거래수 | 거래감소 | PF | 총 PnL | 거래당 PnL | 승률 | 평균 돌파폭 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    base_n = results["B00"]["n"] if results.get("B00") else 1
    for name, thr in scenarios:
        r = results.get(name)
        if not r:
            continue
        drop = (r["n"] - base_n) / base_n * 100 if base_n else 0
        report.append(
            f"| {name} | {thr:.1%} | {r['n']} | {drop:+.1f}% | {r['pf']:.2f} | "
            f"{r['pnl']:+,.0f} | {r['per_trade']:+,.0f} | "
            f"{r['win_rate']:.1%} | {r['avg_breakout_pct']:.2%} |"
        )

    report += [
        "",
        "## B00 기준 돌파 폭 버킷별 PnL 분포",
        "",
        "| 돌파 폭 범위 | 거래수 | 총 PnL | 거래당 PnL | 승률 |",
        "|---|---|---|---|---|",
    ]
    for lo, hi, bn, bpnl, bper, bwr in bin_stats:
        report.append(f"| {lo:.1%} ~ {hi:.1%} | {bn} | {bpnl:+,.0f} | {bper:+,.0f} | {bwr:.1%} |")

    report += [
        "",
        "## 청산 분포 (%)",
        "",
        "| 시나리오 | forced_close | stop_loss | trailing_stop |",
        "|---|---|---|---|",
    ]
    for name, thr in scenarios:
        r = results.get(name)
        if not r:
            continue
        ed = r["ed"]
        tot = sum(ed.values()) or 1
        fc = ed.get("forced_close", 0) / tot * 100
        sl = ed.get("stop_loss", 0) / tot * 100
        ts = ed.get("trailing_stop", 0) / tot * 100
        report.append(f"| {name} | {fc:.1f} | {sl:.1f} | {ts:.1f} |")

    report += [
        "",
        "## 시장 국면별 PF (1주 가중)",
        "",
        "| 시나리오 | 강세 | 횡보 | 약세 |",
        "|---|---|---|---|",
    ]
    for name, thr in scenarios:
        r = results.get(name)
        if not r:
            continue
        rpf = r["rpf"]
        report.append(
            f"| {name} | {rpf.get('강세', 0):.2f} | {rpf.get('횡보', 0):.2f} | {rpf.get('약세', 0):.2f} |"
        )

    Path("reports").mkdir(exist_ok=True)
    with open("reports/breakout_threshold_grid.md", "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print("\nreport: reports/breakout_threshold_grid.md")


if __name__ == "__main__":
    asyncio.run(main())
