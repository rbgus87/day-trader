"""scripts/grid_volume_ratio.py — 거래량 비율(volume_ratio) 그리드."""
import asyncio, os, pickle, sqlite3, sys, yaml
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path
import numpy as np, pandas as pd
from loguru import logger
logger.remove()

sys.path.insert(0, str(Path(__file__).parent.parent))
from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig, TradingConfig
from core.cost_model import TradeCosts, apply_buy_costs, apply_sell_costs
from core.indicators import calculate_atr_trailing_stop, get_latest_atr
from data.db_manager import DbManager

DB_PATH = "daytrader.db"

SCENARIOS = [
    ("V10",  "ratio 1.0 (비활성)", 1.0),
    ("V15",  "ratio 1.5",          1.5),
    ("V20",  "ratio 2.0 (현재)",   2.0),
    ("V25",  "ratio 2.5",          2.5),
    ("V30",  "ratio 3.0",          3.0),
]

def run_f_day(day_candles, strategy, costs, ticker):
    candles = day_candles.reset_index(drop=True)
    if candles.empty: return []
    trades, position = [], None
    as_of = None
    try: as_of = pd.to_datetime(candles["ts"].iloc[0]).strftime("%Y-%m-%d")
    except Exception: pass
    atr_pct = get_latest_atr(DB_PATH, ticker, as_of)
    def calc_trail(peak):
        if atr_pct is not None:
            return calculate_atr_trailing_stop(peak, atr_pct, 1.0, 0.02, 0.10)
        return peak * 0.995
    for idx, row in candles.iterrows():
        ts = row["ts"]
        if hasattr(ts, "time"): strategy.set_backtest_time(ts.time())
        tick = {"ticker": "BT", "price": float(row["close"]),
                "time": ts.strftime("%H%M") if hasattr(ts, "strftime") else "0000",
                "volume": int(row.get("volume", 0))}
        if position is None:
            sig = strategy.generate_signal(candles.iloc[:idx+1], tick)
            if sig and sig.side == "buy":
                strategy.on_entry()
                ep, ne = apply_buy_costs(float(row["close"]), costs)
                sl = ep * 0.92
                position = {"entry_ts": row["ts"], "entry_price": ep, "net_entry": ne,
                            "stop_loss": sl, "highest_price": float(row["high"])}
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
            trades.append({"entry_ts": position["entry_ts"], "exit_ts": row["ts"],
                           "entry_price": position["entry_price"], "exit_price": ep_s,
                           "pnl": pnl, "pnl_pct": pnl / position["net_entry"], "exit_reason": reason})
            position = None; strategy.on_exit(); continue
        if is_last:
            ep_s, ne_s = apply_sell_costs(close, costs)
            pnl = ne_s - position["net_entry"]
            trades.append({"entry_ts": position["entry_ts"], "exit_ts": row["ts"],
                           "entry_price": position["entry_price"], "exit_price": ep_s,
                           "pnl": pnl, "pnl_pct": pnl / position["net_entry"], "exit_reason": "forced_close"})
            position = None; strategy.on_exit()
    strategy.set_backtest_time(None)
    return trades

def run_multi(ticker, all_candles, tcfg, bcfg, tkm, mmap):
    from strategy.momentum_strategy import MomentumStrategy
    costs = TradeCosts(commission_rate=bcfg.commission, slippage_rate=bcfg.slippage, tax_rate=bcfg.tax)
    strat = MomentumStrategy(tcfg)
    if all_candles.empty: return []
    df = all_candles.copy()
    if "date" not in df.columns: df["date"] = df["ts"].dt.date
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
        if mf and tkm in ("kospi","kosdaq"):
            s = mmap.get(date.strftime("%Y%m%d"))
            if s is not None and not s.get(tkm, True): skip = True
        if not skip and bl:
            from datetime import timedelta
            cut = date - timedelta(days=bl_l)
            ls = sum(1 for t in all_t if t.get("pnl",0)<0 and t.get("exit_ts") is not None
                     and hasattr(t["exit_ts"],"date") and cut<=t["exit_ts"].date()<date)
            if ls >= bl_th: skip = True
        if not skip and rest:
            ps = sorted((d for d in dpnl if d<date), reverse=True)
            c = 0
            for d in ps:
                if dpnl[d]<0: c+=1
                else: break
            if c >= rest_th: skip = True
        if skip: prev=dd; continue
        strat.reset()
        if hasattr(strat,"set_ticker"): strat.set_ticker(ticker)
        if hasattr(strat,"set_prev_day_data") and prev is not None:
            strat.set_prev_day_data(float(prev["high"].max()), int(prev["volume"].sum()))
        dt = run_f_day(dd, strat, costs, ticker)
        for t in dt: t["ticker"]=ticker
        all_t.extend(dt)
        dpnl[date]=sum(t.get("pnl",0) for t in dt)
        prev=dd
    return all_t

def _worker(args):
    tk, tkm, cp, tcfg_dict, bcfg, mm = args
    tcfg = TradingConfig(**tcfg_dict)
    return tk, run_multi(tk, pickle.loads(cp), tcfg, bcfg, tkm, mm)

def build_regime_map(db_path):
    conn = sqlite3.connect(db_path)
    rm = {}
    for code in [("001","kospi"),("101","kosdaq")]:
        cur = conn.execute("SELECT dt, close FROM index_candles WHERE index_code=? ORDER BY dt", (code[0],))
        rows = cur.fetchall()
        monthly = {}
        for dt, close in rows:
            ym = dt[:4]+"-"+dt[4:6]
            if ym not in monthly: monthly[ym] = {"first": close, "last": close}
            monthly[ym]["last"] = close
        for ym, v in monthly.items():
            ret = (v["last"]-v["first"])/v["first"]
            rm.setdefault(ym,[]).append(ret)
    conn.close()
    result = {}
    for ym, rets in rm.items():
        avg = np.mean(rets)
        result[ym] = "강세" if avg>=0.05 else ("약세" if avg<=-0.05 else "횡보")
    return result

async def main():
    app = AppConfig.from_yaml(); cfg = app.trading
    bt_raw = yaml.safe_load(open("config.yaml", encoding="utf-8")).get("backtest", {})
    bcfg = BacktestConfig(commission=bt_raw.get("commission",0.00015),
                          tax=bt_raw.get("tax",0.0015), slippage=bt_raw.get("slippage",0.0003))
    uni = yaml.safe_load(open("config/universe.yaml", encoding="utf-8"))
    stocks = uni["stocks"]
    tkm = {s["ticker"]: s.get("market","?") for s in stocks}
    db = DbManager(app.db_path); await db.init()
    loader = Backtester(db=db, config=cfg, backtest_config=bcfg)
    cc = {}
    for s in stocks:
        c = await loader.load_candles(s["ticker"], "2025-04-01", "2026-04-15 23:59:59")
        if not c.empty: cc[s["ticker"]] = pickle.dumps(c)
    await db.close()
    mm = build_market_strong_by_date(app.db_path, ma_length=cfg.market_ma_length)
    regime_map = build_regime_map(DB_PATH)
    w = max(2, (os.cpu_count() or 2)-1)

    base_dict = asdict(cfg)

    print(f"Volume ratio grid")
    print(f"Tickers: {len(cc)}, Workers: {w}")
    print()
    hdr = f"{'name':<6} {'desc':<22} | {'n':>4} {'PF':>6} {'PnL':>12} {'per':>8} | {'sl':>4} {'ts':>4} {'fc':>4} | {'occ':>4} {'DD':>10} | {'bull':>5} {'side':>5} {'bear':>5}"
    print(hdr)
    print("-"*len(hdr))

    for name, desc, ratio in SCENARIOS:
        d = dict(base_dict)
        d["momentum_volume_ratio"] = ratio
        tasks = [(tk, tkm.get(tk,"?"), cc[tk], d, bcfg, mm) for tk in cc]
        trades = []
        with ProcessPoolExecutor(max_workers=w) as ex:
            for _, t in ex.map(_worker, tasks):
                trades.extend(t)
        total = len(trades)
        if total == 0:
            print(f"{name:<6} {desc:<22} | NO TRADES"); continue
        pnl_list = [t["pnl"] for t in trades]
        gp = sum(p for p in pnl_list if p>0)
        gl = abs(sum(p for p in pnl_list if p<0))
        pf = gp/gl if gl>0 else 0
        per_trade = sum(pnl_list)/total
        ed = Counter(t.get("exit_reason","?") for t in trades)
        cum = peak = max_dd = 0.0
        for p in pnl_list:
            cum += p
            if cum > peak: peak = cum
            dd = peak - cum
            if dd > max_dd: max_dd = dd
        fc_t = [t for t in trades if t.get("exit_reason")=="forced_close"]
        occ = sum(1 for t in fc_t if abs(t.get("pnl_pct",0))<0.005)
        occ_pct = occ/len(fc_t)*100 if fc_t else 0
        rt = defaultdict(list)
        for t in trades:
            try:
                m = pd.to_datetime(t["entry_ts"]).strftime("%Y-%m")
                rt[regime_map.get(m,"?")].append(t)
            except Exception: pass
        rpf = {}
        for r, tlist in rt.items():
            rg = sum(t["pnl"] for t in tlist if t["pnl"]>0)
            rl = abs(sum(t["pnl"] for t in tlist if t["pnl"]<0))
            rpf[r] = rg/rl if rl>0 else 0
        def _f(r): return f"{rpf.get(r,0):.2f}"
        print(f"{name:<6} {desc:<22} | {total:>4} {pf:>6.2f} {sum(pnl_list):>+12,.0f} {per_trade:>+8,.0f} | "
              f"{ed.get('stop_loss',0):>4} {ed.get('trailing_stop',0):>4} {ed.get('forced_close',0):>4} | "
              f"{occ_pct:>3.0f}% {max_dd:>10,.0f} | {_f('강세'):>5} {_f('횡보'):>5} {_f('약세'):>5}")

if __name__ == "__main__":
    asyncio.run(main())
