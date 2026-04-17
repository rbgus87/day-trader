"""scripts/grid_combo_be3_nts.py — BE3 + NTS 조합 그리드.

시나리오 7종:
  D0             : control
  BE3            : +3% → stop=entry+1% (단독)
  BE3+NTS60_2    : BE3 + 60분·ret<-2% 청산
  BE3+NTS60_3    : BE3 + 60분·ret<-3% 청산
  BE3+NTS90_2    : BE3 + 90분·ret<-2% 청산
  BE3+NTS90_3    : BE3 + 90분·ret<-3% 청산
  BE3+TS120      : BE3 + 120분·ret<0% 청산

BE와 NTS는 독립 작동 (BE3 발동 = peak_return≥3%, NTS 발동 = 수익 없이 경과).

추가 분석:
  NTS 발동 건수 + 발동 거래의 D0 최종 PnL 매칭 → 반등 놓침 건수/비율
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

# (name, be_on, be_trigger, be_offset, nts_on, nts_min, nts_thr)
SCENARIOS = [
    ("D0",            False, 0.00, 0.00, False, 0,   0.00),
    ("BE3_ONLY",      True,  0.03, 0.01, False, 0,   0.00),
    ("BE3+NTS60_2",   True,  0.03, 0.01, True,  60, -0.02),
    ("BE3+NTS60_3",   True,  0.03, 0.01, True,  60, -0.03),
    ("BE3+NTS90_2",   True,  0.03, 0.01, True,  90, -0.02),
    ("BE3+NTS90_3",   True,  0.03, 0.01, True,  90, -0.03),
    ("BE3+NTS120_3",  True,  0.03, 0.01, True,  120, -0.03),
]


def _elapsed_min(entry_ts, current_ts) -> float:
    try:
        return (pd.to_datetime(current_ts) - pd.to_datetime(entry_ts)).total_seconds() / 60.0
    except Exception:
        return 0.0


def _entry_key(ticker: str, entry_ts) -> str:
    """D0 역매칭용 키."""
    try:
        ts = pd.to_datetime(entry_ts).isoformat()
    except Exception:
        ts = str(entry_ts)
    return f"{ticker}|{ts}"


def run_f_day(day_candles, strategy, costs, ticker,
              be_on, be_trig, be_off, nts_on, nts_min, nts_thr):
    """1종목 1일 — BE + NTS 동시 평가."""
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
                sl_fixed = ep * 0.92
                position = {
                    "entry_ts": row["ts"], "entry_price": ep, "net_entry": ne,
                    "stop_loss": sl_fixed, "highest_price": float(row["high"]),
                    "peak_return": 0.0, "be_applied": False,
                }
                position["stop_loss"] = max(sl_fixed, calc_trail(position["highest_price"]))
            continue

        low = float(row["low"])
        high = float(row["high"])
        close = float(row["close"])
        is_last = idx == len(candles) - 1

        # 최고가·peak_return 갱신
        if high > position["highest_price"]:
            position["highest_price"] = high
            position["stop_loss"] = max(position["stop_loss"], calc_trail(high))
        peak_ret = (position["highest_price"] - position["entry_price"]) / position["entry_price"]
        if peak_ret > position["peak_return"]:
            position["peak_return"] = peak_ret

        elapsed = _elapsed_min(position["entry_ts"], row["ts"])
        current_ret = (close - position["entry_price"]) / position["entry_price"]

        # BE: peak_return이 trigger 도달 → stop 상향 (1회만, NTS와 독립)
        if be_on and not position["be_applied"] and position["peak_return"] >= be_trig:
            new_sl = position["entry_price"] * (1.0 + be_off)
            position["stop_loss"] = max(position["stop_loss"], new_sl)
            position["be_applied"] = True

        # 1) stop 터치 (저가)
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
                "entry_key": _entry_key(ticker, position["entry_ts"]),
            })
            position = None
            strategy.on_exit()
            continue

        # 2) NTS / TS (경과 N분 + 수익률 < threshold)
        if nts_on and elapsed >= nts_min and current_ret < nts_thr:
            # BE 발동 상태라면 신규 stop이 더 높으니 이미 stop 터치로 청산됐을 것.
            # 그래도 보수적으로 BE 미발동 상태에서만 NTS 작동 (독립성 명확화)
            if not position["be_applied"]:
                ep_s, ne_s = apply_sell_costs(close, costs)
                pnl = ne_s - position["net_entry"]
                reason = "time_stop" if nts_thr == 0.0 else "neg_time_stop"
                trades.append({
                    "entry_ts": position["entry_ts"], "exit_ts": row["ts"],
                    "entry_price": position["entry_price"], "exit_price": ep_s,
                    "pnl": pnl, "pnl_pct": pnl / position["net_entry"],
                    "exit_reason": reason, "ticker": ticker,
                    "holding_min": elapsed,
                    "entry_key": _entry_key(ticker, position["entry_ts"]),
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
                "entry_key": _entry_key(ticker, position["entry_ts"]),
            })
            position = None
            strategy.on_exit()
    strategy.set_backtest_time(None)
    return trades


def run_multi(ticker, all_candles, tcfg, bcfg, tkm, mmap,
              be_on, be_trig, be_off, nts_on, nts_min, nts_thr):
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
        dt = run_f_day(dd, strat, costs, ticker,
                       be_on, be_trig, be_off, nts_on, nts_min, nts_thr)
        all_t.extend(dt)
        dpnl[date] = sum(t.get("pnl", 0) for t in dt)
        prev = dd
    return all_t


def _worker(args):
    (tk, tkm, cp, tcfg_dict, bcfg, mm,
     be_on, be_trig, be_off, nts_on, nts_min, nts_thr) = args
    tcfg = TradingConfig(**tcfg_dict)
    trades = run_multi(
        tk, pickle.loads(cp), tcfg, bcfg, tkm, mm,
        be_on, be_trig, be_off, nts_on, nts_min, nts_thr,
    )
    return tk, trades


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


def summarize(trades, regime_map):
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

    # Max DD
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

    return {
        "n": n, "pf": pf, "pnl": float(pnl_arr.sum()),
        "per_trade": per_trade, "ed": dict(ed),
        "wins": wins, "win_rate": wins / n if n else 0,
        "avg_hold": avg_hold, "fc_ratio": fc_ratio,
        "max_dd": max_dd, "rpf": rpf, "trades": trades,
    }


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
    d0_trades_map: dict[str, float] = {}

    for name, be_on, be_t, be_o, nts_on, nts_m, nts_t in SCENARIOS:
        print(f"=== {name} (BE={be_on} NTS={nts_on} {nts_m}min<{nts_t:+.1%}) ===")
        tasks = [
            (tk, tkm.get(tk, "?"), cc[tk], base_dict, bcfg, mm,
             be_on, be_t, be_o, nts_on, nts_m, nts_t)
            for tk in cc
        ]
        trades = []
        with ProcessPoolExecutor(max_workers=w) as ex:
            for _, t in ex.map(_worker, tasks):
                trades.extend(t)
        if not trades:
            print("  거래 없음")
            results[name] = None
            continue

        stats = summarize(trades, regime_map)
        results[name] = stats

        # D0 entry_key → final pnl_pct 맵
        if name == "D0":
            for t in trades:
                d0_trades_map[t["entry_key"]] = t["pnl_pct"]

        print(f"  n={stats['n']}, PF={stats['pf']:.2f}, PnL={stats['pnl']:+,.0f}, "
              f"per={stats['per_trade']:+,.0f}, 승률={stats['win_rate']:.1%}, "
              f"보유={stats['avg_hold']:.0f}분, FC={stats['fc_ratio']:.1f}%")

    # NTS 발동 분석: exit_reason이 neg_time_stop / time_stop인 trade의 D0 매칭
    nts_analysis = {}
    for name, _, _, _, nts_on, _, _ in SCENARIOS:
        if not nts_on or name == "D0":
            nts_analysis[name] = None
            continue
        r = results.get(name)
        if not r:
            continue
        nts_trades = [
            t for t in r["trades"]
            if t.get("exit_reason") in ("neg_time_stop", "time_stop")
        ]
        total_nts = len(nts_trades)
        if total_nts == 0:
            nts_analysis[name] = {
                "total": 0, "cut_pnl": 0.0, "d0_reb": 0, "d0_loss": 0,
                "d0_match": 0, "reb_ratio": 0.0,
            }
            continue
        cut_pnl = sum(t["pnl"] for t in nts_trades)
        d0_reb = 0
        d0_loss = 0
        d0_match = 0
        for t in nts_trades:
            d0_pnl_pct = d0_trades_map.get(t["entry_key"])
            if d0_pnl_pct is None:
                continue
            d0_match += 1
            if d0_pnl_pct > 0:
                d0_reb += 1
            else:
                d0_loss += 1
        reb_ratio = d0_reb / d0_match * 100 if d0_match else 0.0
        nts_analysis[name] = {
            "total": total_nts, "cut_pnl": cut_pnl,
            "d0_reb": d0_reb, "d0_loss": d0_loss,
            "d0_match": d0_match, "reb_ratio": reb_ratio,
        }

    # 리포트
    report = [
        "# Combo: BE3 + NTS Grid",
        "",
        f"기간: 2025-04-01 ~ 2026-04-15, Universe 41종목",
        f"고정: min_breakout_pct={cfg.min_breakout_pct:.1%}, max_positions={cfg.max_positions}, "
        f"stop_loss=-8%, atr_trail x1.0 (2%/10%)",
        "",
        "BE는 peak_return ≥ 3% 시 stop=entry+1% 상향 (1회).",
        "NTS는 BE 미발동 상태에서만 작동 (BE/NTS 독립 보장).",
        "",
        "## 시나리오별 결과",
        "",
        "| 시나리오 | 거래수 | PF | 총 PnL | 거래당 | 승률 | 보유(분) | forced% | Max DD |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, *_ in SCENARIOS:
        r = results.get(name)
        if not r:
            continue
        report.append(
            f"| {name} | {r['n']} | {r['pf']:.2f} | {r['pnl']:+,.0f} | "
            f"{r['per_trade']:+,.0f} | {r['win_rate']:.1%} | "
            f"{r['avg_hold']:.0f} | {r['fc_ratio']:.1f}% | {r['max_dd']:,.0f} |"
        )

    report += [
        "",
        "## 청산 사유 분포 (%)",
        "",
        "| 시나리오 | forced_close | stop_loss | trailing | breakeven | neg_time_stop | time_stop |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, *_ in SCENARIOS:
        r = results.get(name)
        if not r:
            continue
        ed = r["ed"]
        tot = sum(ed.values()) or 1
        def pct(k): return ed.get(k, 0) / tot * 100
        report.append(
            f"| {name} | {pct('forced_close'):.1f} | {pct('stop_loss'):.1f} | "
            f"{pct('trailing_stop'):.1f} | {pct('breakeven_stop'):.1f} | "
            f"{pct('neg_time_stop'):.1f} | {pct('time_stop'):.1f} |"
        )

    report += [
        "",
        "## 시장 국면별 PF",
        "",
        "| 시나리오 | 강세 | 횡보 | 약세 |",
        "|---|---|---|---|",
    ]
    for name, *_ in SCENARIOS:
        r = results.get(name)
        if not r:
            continue
        rpf = r["rpf"]
        report.append(
            f"| {name} | {rpf.get('강세',0):.2f} | {rpf.get('횡보',0):.2f} | {rpf.get('약세',0):.2f} |"
        )

    report += [
        "",
        "## NTS 발동 상세 (D0 최종 수익률 매칭 역산)",
        "",
        "| 시나리오 | NTS 발동 건수 | 잘린 거래 총PnL | D0매칭 | D0 반등 | D0 손실 | **반등 비율** |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, *_ in SCENARIOS:
        a = nts_analysis.get(name)
        if a is None:
            continue
        if a["total"] == 0:
            report.append(f"| {name} | 0 | — | — | — | — | — |")
            continue
        report.append(
            f"| {name} | {a['total']} | {a['cut_pnl']:+,.0f} | "
            f"{a['d0_match']} | {a['d0_reb']} | {a['d0_loss']} | "
            f"**{a['reb_ratio']:.1f}%** |"
        )

    # 단독 BE3 대비 조합 델타
    be3 = results.get("BE3_ONLY")
    if be3:
        report += [
            "",
            "## BE3 단독 대비 조합 델타",
            "",
            "| 시나리오 | ΔPF | Δ거래수 | Δ총PnL | Δforced% | Δ보유 | Δ약세PF |",
            "|---|---|---|---|---|---|---|",
        ]
        b_pf = be3["pf"]; b_n = be3["n"]; b_pnl = be3["pnl"]
        b_fc = be3["fc_ratio"]; b_hold = be3["avg_hold"]
        b_bear = be3["rpf"].get("약세", 0)
        for name, *_ in SCENARIOS:
            if name in ("D0", "BE3_ONLY"):
                continue
            r = results.get(name)
            if not r:
                continue
            report.append(
                f"| {name} | {r['pf']-b_pf:+.2f} | {r['n']-b_n:+d} | "
                f"{r['pnl']-b_pnl:+,.0f} | {r['fc_ratio']-b_fc:+.1f} | "
                f"{r['avg_hold']-b_hold:+.0f} | {r['rpf'].get('약세',0)-b_bear:+.2f} |"
            )

    Path("reports").mkdir(exist_ok=True)
    with open("reports/combo_be3_nts_grid.md", "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print("\nreport: reports/combo_be3_nts_grid.md")


if __name__ == "__main__":
    asyncio.run(main())
