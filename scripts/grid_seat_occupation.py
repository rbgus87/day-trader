"""scripts/grid_seat_occupation.py — 자리 차지 해결 방안 비교.

시나리오 10종 (D0 control + A×3 + B×3 + C×3):
  D0         : control (현재 로직)
  TS60/90/120: 경과 N분 + 수익률 < 0% → time_stop
  NTS60_2    : 60분 + < -2% → neg_time_stop
  NTS60_3    : 60분 + < -3% → neg_time_stop
  NTS90_2    : 90분 + < -2% → neg_time_stop
  BE1/BE2/BE3: 고점 +N% 도달 시 stop을 진입가(또는 진입가+1%)로 이동

공통: universe 41종목, min_breakout_pct 0.03 (현 config 그대로),
       max_positions 3, ADR-010 Pure trailing, 고정 -8% 손절
기간: 2025-04-01 ~ 2026-04-15

D0 시나리오에서는 각 trade의 60/90/120분 시점 수익률을 기록 →
"N분 마이너스 → 최종 수익" 케이스 수 분석.
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

SCENARIOS = [
    # (name, type, params)
    ("D0",      "control", {}),
    ("TS60",    "time",    {"minutes": 60,  "threshold": 0.0}),
    ("TS90",    "time",    {"minutes": 90,  "threshold": 0.0}),
    ("TS120",   "time",    {"minutes": 120, "threshold": 0.0}),
    ("NTS60_2", "time",    {"minutes": 60,  "threshold": -0.02}),
    ("NTS60_3", "time",    {"minutes": 60,  "threshold": -0.03}),
    ("NTS90_2", "time",    {"minutes": 90,  "threshold": -0.02}),
    ("BE1",     "breakeven", {"trigger": 0.01, "offset": 0.0}),
    ("BE2",     "breakeven", {"trigger": 0.02, "offset": 0.0}),
    ("BE3",     "breakeven", {"trigger": 0.03, "offset": 0.01}),
]


def _elapsed_min(entry_ts, current_ts) -> float:
    try:
        return (pd.to_datetime(current_ts) - pd.to_datetime(entry_ts)).total_seconds() / 60.0
    except Exception:
        return 0.0


def run_f_day(day_candles, strategy, costs, ticker, scenario_name, stype, sparams):
    """1종목 1일 시뮬 — 시나리오별 청산 로직 적용.

    D0에서는 trade별 60/90/120분 시점 수익률 기록.
    """
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
                sl_fixed = ep * 0.92  # 고정 -8%
                position = {
                    "entry_ts": row["ts"], "entry_price": ep, "net_entry": ne,
                    "stop_loss": sl_fixed, "highest_price": float(row["high"]),
                    "peak_return": 0.0,
                    "ret_at_60": None, "ret_at_90": None, "ret_at_120": None,
                    "be_applied": False,
                }
                position["stop_loss"] = max(sl_fixed, calc_trail(position["highest_price"]))
            continue

        low = float(row["low"])
        high = float(row["high"])
        close = float(row["close"])
        is_last = idx == len(candles) - 1

        # 최고가/고점 수익률 갱신
        if high > position["highest_price"]:
            position["highest_price"] = high
            position["stop_loss"] = max(position["stop_loss"], calc_trail(high))
        current_ret_high = (high - position["entry_price"]) / position["entry_price"]
        if current_ret_high > position["peak_return"]:
            position["peak_return"] = current_ret_high

        # 보유 시간 / 현재 수익률
        elapsed = _elapsed_min(position["entry_ts"], row["ts"])
        current_ret = (close - position["entry_price"]) / position["entry_price"]

        # D0: 타임스탬프 분석용 수익률 기록
        if scenario_name == "D0":
            if position["ret_at_60"] is None and elapsed >= 60:
                position["ret_at_60"] = current_ret
            if position["ret_at_90"] is None and elapsed >= 90:
                position["ret_at_90"] = current_ret
            if position["ret_at_120"] is None and elapsed >= 120:
                position["ret_at_120"] = current_ret

        # Breakeven: 고점 수익률이 trigger 도달 → stop 상향 (1회만)
        if stype == "breakeven" and not position["be_applied"]:
            trigger = sparams["trigger"]
            offset = sparams["offset"]
            if position["peak_return"] >= trigger:
                new_sl = position["entry_price"] * (1.0 + offset)
                position["stop_loss"] = max(position["stop_loss"], new_sl)
                position["be_applied"] = True

        # 1) 기존 stop_loss / trailing 청산 (low가 stop 터치)
        if low <= position["stop_loss"]:
            ep_s, ne_s = apply_sell_costs(position["stop_loss"], costs)
            pnl = ne_s - position["net_entry"]
            if position["be_applied"] and position["stop_loss"] >= position["entry_price"]:
                reason = "breakeven_stop"
            elif position["stop_loss"] > position["entry_price"] * 0.975:
                reason = "trailing_stop"
            else:
                reason = "stop_loss"
            trades.append({
                "entry_ts": position["entry_ts"], "exit_ts": row["ts"],
                "entry_price": position["entry_price"], "exit_price": ep_s,
                "pnl": pnl, "pnl_pct": pnl / position["net_entry"],
                "exit_reason": reason, "ticker": ticker,
                "holding_min": elapsed,
                "peak_return": position["peak_return"],
                "ret_at_60": position["ret_at_60"],
                "ret_at_90": position["ret_at_90"],
                "ret_at_120": position["ret_at_120"],
            })
            position = None
            strategy.on_exit()
            continue

        # 2) Time Stop / Negative Time Stop (경과 N분 + 수익률 <= threshold)
        if stype == "time":
            N = sparams["minutes"]
            thr = sparams["threshold"]
            if elapsed >= N and current_ret < thr:
                ep_s, ne_s = apply_sell_costs(close, costs)
                pnl = ne_s - position["net_entry"]
                reason = "time_stop" if thr == 0.0 else "neg_time_stop"
                trades.append({
                    "entry_ts": position["entry_ts"], "exit_ts": row["ts"],
                    "entry_price": position["entry_price"], "exit_price": ep_s,
                    "pnl": pnl, "pnl_pct": pnl / position["net_entry"],
                    "exit_reason": reason, "ticker": ticker,
                    "holding_min": elapsed,
                    "peak_return": position["peak_return"],
                    "ret_at_60": position["ret_at_60"],
                    "ret_at_90": position["ret_at_90"],
                    "ret_at_120": position["ret_at_120"],
                })
                position = None
                strategy.on_exit()
                continue

        # 3) 마지막 캔들 강제 청산
        if is_last:
            ep_s, ne_s = apply_sell_costs(close, costs)
            pnl = ne_s - position["net_entry"]
            trades.append({
                "entry_ts": position["entry_ts"], "exit_ts": row["ts"],
                "entry_price": position["entry_price"], "exit_price": ep_s,
                "pnl": pnl, "pnl_pct": pnl / position["net_entry"],
                "exit_reason": "forced_close", "ticker": ticker,
                "holding_min": elapsed,
                "peak_return": position["peak_return"],
                "ret_at_60": position["ret_at_60"],
                "ret_at_90": position["ret_at_90"],
                "ret_at_120": position["ret_at_120"],
            })
            position = None
            strategy.on_exit()
    strategy.set_backtest_time(None)
    return trades


def run_multi(ticker, all_candles, tcfg, bcfg, tkm, mmap, scen_name, stype, sparams):
    costs = TradeCosts(commission_rate=bcfg.commission, slippage_rate=bcfg.slippage, tax_rate=bcfg.tax)
    strat = MomentumStrategy(tcfg)
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
        dt = run_f_day(dd, strat, costs, ticker, scen_name, stype, sparams)
        all_t.extend(dt)
        dpnl[date] = sum(t.get("pnl", 0) for t in dt)
        prev = dd
    return all_t


def _worker(args):
    tk, tkm, cp, tcfg_dict, bcfg, mm, scen_name, stype, sparams = args
    tcfg = TradingConfig(**tcfg_dict)
    return tk, run_multi(tk, pickle.loads(cp), tcfg, bcfg, tkm, mm, scen_name, stype, sparams)


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
        c = await loader.load_candles(s["ticker"], "2025-04-01", "2026-04-15 23:59:59")
        if not c.empty:
            cc[s["ticker"]] = pickle.dumps(c)
    await db.close()
    mm = build_market_strong_by_date(app.db_path, ma_length=cfg.market_ma_length)
    regime_map = build_regime_map(DB_PATH)
    w = max(2, (os.cpu_count() or 2) - 1)
    base_dict = asdict(cfg)

    print(f"universe 로드: {len(cc)}/{len(stocks)}종목")
    print(f"min_breakout_pct={cfg.min_breakout_pct:.1%}, max_positions={cfg.max_positions}")
    print()

    results = {}
    for name, stype, sparams in SCENARIOS:
        print(f"=== {name} ({stype}: {sparams}) ===")
        tasks = [(tk, tkm.get(tk, "?"), cc[tk], base_dict, bcfg, mm, name, stype, sparams) for tk in cc]
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
        avg_hold = float(np.mean([t["holding_min"] for t in trades]))
        fc_ratio = ed.get("forced_close", 0) / n * 100

        # Max DD (누적 PnL 기준, 정렬은 entry_ts)
        sorted_tr = sorted(trades, key=lambda t: str(t["entry_ts"]))
        cum = peak = max_dd = 0.0
        for t in sorted_tr:
            cum += t["pnl"]
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd

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
            "n": n, "pf": pf, "pnl": float(pnl_arr.sum()),
            "per_trade": per_trade, "ed": dict(ed),
            "wins": wins, "win_rate": wins / n if n else 0,
            "avg_hold": avg_hold, "fc_ratio": fc_ratio,
            "max_dd": max_dd, "rpf": rpf, "trades": trades,
        }
        print(f"  n={n}, PF={pf:.2f}, PnL={pnl_arr.sum():+,.0f}, "
              f"per={per_trade:+,.0f}, 승률={wins/n:.1%}, "
              f"보유={avg_hold:.0f}분, FC={fc_ratio:.1f}%")

    # D0 기준: 60/90/120분 마이너스 → 반등 케이스 분석
    d0 = results.get("D0")
    turnaround = {"60": {"neg_reb": 0, "neg_end_loss": 0, "neg_total": 0},
                  "90": {"neg_reb": 0, "neg_end_loss": 0, "neg_total": 0},
                  "120": {"neg_reb": 0, "neg_end_loss": 0, "neg_total": 0}}
    if d0:
        for t in d0["trades"]:
            final_ret = t["pnl_pct"]
            for k, key in (("60", "ret_at_60"), ("90", "ret_at_90"), ("120", "ret_at_120")):
                r = t.get(key)
                if r is None:
                    continue
                if r < 0:
                    turnaround[k]["neg_total"] += 1
                    if final_ret > 0:
                        turnaround[k]["neg_reb"] += 1
                    else:
                        turnaround[k]["neg_end_loss"] += 1

    # 리포트 작성
    report = [
        "# Seat Occupation Grid",
        "",
        f"기간: 2025-04-01 ~ 2026-04-15, Universe 41종목",
        f"고정 파라미터: min_breakout_pct={cfg.min_breakout_pct:.1%}, "
        f"max_positions={cfg.max_positions}, stop_loss=-8.0%, "
        f"atr_trail x1.0 (2%/10%)",
        "",
        "## 시나리오별 결과",
        "",
        "| 시나리오 | 설명 | 거래수 | PF | 총 PnL | 거래당 | 승률 | 보유(분) | forced% | Max DD |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    desc_map = {
        "D0": "현재 유지 (control)",
        "TS60": "60분 + ret<0%",
        "TS90": "90분 + ret<0%",
        "TS120": "120분 + ret<0%",
        "NTS60_2": "60분 + ret<-2%",
        "NTS60_3": "60분 + ret<-3%",
        "NTS90_2": "90분 + ret<-2%",
        "BE1": "+1% 도달 → BE",
        "BE2": "+2% 도달 → BE",
        "BE3": "+3% 도달 → 진입가+1%",
    }
    for name, _, _ in SCENARIOS:
        r = results.get(name)
        if not r:
            continue
        report.append(
            f"| {name} | {desc_map[name]} | {r['n']} | {r['pf']:.2f} | "
            f"{r['pnl']:+,.0f} | {r['per_trade']:+,.0f} | {r['win_rate']:.1%} | "
            f"{r['avg_hold']:.0f} | {r['fc_ratio']:.1f}% | {r['max_dd']:,.0f} |"
        )

    report += [
        "",
        "## 청산 사유 분포 (%)",
        "",
        "| 시나리오 | forced_close | stop_loss | trailing_stop | time_stop | neg_time_stop | breakeven_stop |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, _, _ in SCENARIOS:
        r = results.get(name)
        if not r:
            continue
        ed = r["ed"]
        tot = sum(ed.values()) or 1
        def pct(k): return ed.get(k, 0) / tot * 100
        report.append(
            f"| {name} | {pct('forced_close'):.1f} | {pct('stop_loss'):.1f} | "
            f"{pct('trailing_stop'):.1f} | {pct('time_stop'):.1f} | "
            f"{pct('neg_time_stop'):.1f} | {pct('breakeven_stop'):.1f} |"
        )

    report += [
        "",
        "## 시장 국면별 PF",
        "",
        "| 시나리오 | 강세 | 횡보 | 약세 |",
        "|---|---|---|---|",
    ]
    for name, _, _ in SCENARIOS:
        r = results.get(name)
        if not r:
            continue
        rpf = r["rpf"]
        report.append(
            f"| {name} | {rpf.get('강세',0):.2f} | {rpf.get('횡보',0):.2f} | {rpf.get('약세',0):.2f} |"
        )

    report += [
        "",
        "## D0 기준: N분 시점 마이너스 → 반등 분석",
        "",
        "| 시점 | N분 마이너스 건수 | 최종 수익 반등 | 최종 손실 유지 | 반등 비율 |",
        "|---|---|---|---|---|",
    ]
    for k in ("60", "90", "120"):
        s = turnaround[k]
        if s["neg_total"] == 0:
            report.append(f"| {k}분 | 0 | 0 | 0 | — |")
            continue
        rb = s["neg_reb"] / s["neg_total"] * 100
        report.append(f"| {k}분 | {s['neg_total']} | {s['neg_reb']} | {s['neg_end_loss']} | {rb:.1f}% |")

    # control 대비 요약
    if d0:
        report += [
            "",
            "## D0 control 대비 요약",
            "",
            "| 시나리오 | ΔPF | Δ거래수 | Δ총PnL | Δforced% | Δ보유분 | Δ약세PF |",
            "|---|---|---|---|---|---|---|",
        ]
        d0_pf = d0["pf"]; d0_n = d0["n"]; d0_pnl = d0["pnl"]
        d0_fc = d0["fc_ratio"]; d0_hold = d0["avg_hold"]
        d0_bear = d0["rpf"].get("약세", 0)
        for name, _, _ in SCENARIOS:
            if name == "D0":
                continue
            r = results.get(name)
            if not r:
                continue
            report.append(
                f"| {name} | {r['pf']-d0_pf:+.2f} | {r['n']-d0_n:+d} | "
                f"{r['pnl']-d0_pnl:+,.0f} | {r['fc_ratio']-d0_fc:+.1f} | "
                f"{r['avg_hold']-d0_hold:+.0f} | {r['rpf'].get('약세',0)-d0_bear:+.2f} |"
            )

    Path("reports").mkdir(exist_ok=True)
    with open("reports/seat_occupation_grid.md", "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print("\nreport: reports/seat_occupation_grid.md")


if __name__ == "__main__":
    asyncio.run(main())
