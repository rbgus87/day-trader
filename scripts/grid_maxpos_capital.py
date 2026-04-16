"""scripts/grid_maxpos_capital.py — max_positions × 자본 그리드.

backtester는 종목별 독립 실행이므로 max_positions를 시뮬하려면
전 종목 trades를 시간순 정렬 후 동시 포지션 수 제한을 적용해야 함.

접근:
1. wrapper로 전종목 trades 수집 (제한 없이)
2. 시간순 정렬 후 max_positions 필터 적용 (후처리)
3. 자본 사이징 적용
"""
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


def run_f_day(day_candles, strategy, costs, ticker):
    """1종목 1일 Pure trailing 백테스트 (제한 없이)."""
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
                           "pnl": pnl, "pnl_pct": pnl / position["net_entry"],
                           "exit_reason": reason, "ticker": ticker})
            position = None; strategy.on_exit(); continue
        if is_last:
            ep_s, ne_s = apply_sell_costs(close, costs)
            pnl = ne_s - position["net_entry"]
            trades.append({"entry_ts": position["entry_ts"], "exit_ts": row["ts"],
                           "entry_price": position["entry_price"], "exit_price": ep_s,
                           "pnl": pnl, "pnl_pct": pnl / position["net_entry"],
                           "exit_reason": "forced_close", "ticker": ticker})
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
        all_t.extend(dt)
        dpnl[date]=sum(t.get("pnl",0) for t in dt)
        prev=dd
    return all_t


def _worker(args):
    tk, tkm, cp, tcfg_dict, bcfg, mm = args
    tcfg = TradingConfig(**tcfg_dict)
    return tk, run_multi(tk, pickle.loads(cp), tcfg, bcfg, tkm, mm)


def apply_max_positions(all_trades, max_pos, capital, max_pos_budget_each):
    """시간순 정렬 후 max_positions 제한 + 자본 사이징 적용.

    진입 시간 기준 정렬. 동시 보유 >= max_pos이면 진입 거부(skip).
    자본 사이징: qty = floor(capital / max_pos / entry_price).
    """
    sorted_trades = sorted(all_trades, key=lambda t: str(t["entry_ts"]))

    accepted = []
    # 활성 포지션 추적: {ticker: exit_ts}
    active = {}

    for t in sorted_trades:
        entry_ts = str(t["entry_ts"])
        exit_ts = str(t["exit_ts"])
        ticker = t["ticker"]

        # 만료된 포지션 제거
        active = {tk: ets for tk, ets in active.items() if ets > entry_ts}

        # max_positions 체크
        if len(active) >= max_pos:
            continue  # skip

        # 같은 종목 이미 보유 중이면 skip
        if ticker in active:
            continue

        # 자본 사이징
        per_pos = capital / max_pos
        qty = int(per_pos / t["entry_price"])
        if qty <= 0:
            continue

        # PnL 재계산 (자본 가중)
        pnl_capital = t["pnl"] * qty  # 1주 pnl × qty

        accepted.append({
            **t,
            "pnl_capital": pnl_capital,
            "qty": qty,
            "buy_amount": t["entry_price"] * qty,
        })
        active[ticker] = exit_ts

    return accepted


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

    # 1. 전종목 trades 수집 (제한 없이)
    print("Phase 1: 전종목 trades 수집 (제한 없이)")
    tasks = [(tk, tkm.get(tk,"?"), cc[tk], base_dict, bcfg, mm) for tk in cc]
    raw_trades = []
    with ProcessPoolExecutor(max_workers=w) as ex:
        for _, t in ex.map(_worker, tasks):
            raw_trades.extend(t)
    print(f"  전체 trades: {len(raw_trades)}건")
    print()

    # 2. max_positions × 자본 매트릭스
    max_positions_list = [3, 5, 7]
    capitals = [1_000_000, 2_000_000, 3_000_000, 5_000_000]

    print("Phase 2: max_positions x capital 매트릭스")
    print()

    # PF 매트릭스 (1주 가중)
    print("=== PF (1주 가중) ===")
    print(f"{'':>8}", end="")
    for cap in capitals:
        print(f"  C{cap//1_000_000}M", end="")
    print()

    results = {}

    for mp in max_positions_list:
        print(f"max {mp}:", end="")
        for cap in capitals:
            filtered = apply_max_positions(raw_trades, mp, cap, cap // mp)
            total = len(filtered)
            if total == 0:
                print(f"  {'N/A':>5}", end="")
                continue

            # 1주 가중 PF
            pnl1 = [t["pnl"] for t in filtered]
            gp1 = sum(p for p in pnl1 if p > 0)
            gl1 = abs(sum(p for p in pnl1 if p < 0))
            pf1 = gp1/gl1 if gl1 > 0 else 0

            # 자본 가중 PF
            pnl_c = [t["pnl_capital"] for t in filtered]
            gpc = sum(p for p in pnl_c if p > 0)
            glc = abs(sum(p for p in pnl_c if p < 0))
            pfc = gpc/glc if glc > 0 else 0

            # 기타 메트릭
            ed = Counter(t.get("exit_reason","?") for t in filtered)
            fc_t = [t for t in filtered if t.get("exit_reason")=="forced_close"]
            occ = sum(1 for t in fc_t if abs(t.get("pnl_pct",0))<0.005)

            cum = peak = max_dd = 0.0
            for p in pnl_c:
                cum += p
                if cum > peak: peak = cum
                dd = peak - cum
                if dd > max_dd: max_dd = dd

            avg_qty = np.mean([t.get("qty",1) for t in filtered])
            total_buy = sum(t.get("buy_amount",0) for t in filtered)
            util = total_buy / (cap * len(filtered)) if filtered else 0

            # 국면별 PF
            rt = defaultdict(list)
            for t in filtered:
                try:
                    m = pd.to_datetime(t["entry_ts"]).strftime("%Y-%m")
                    rt[regime_map.get(m,"?")].append(t)
                except Exception: pass
            rpf = {}
            for r, tlist in rt.items():
                rg = sum(t["pnl"] for t in tlist if t["pnl"]>0)
                rl = abs(sum(t["pnl"] for t in tlist if t["pnl"]<0))
                rpf[r] = rg/rl if rl>0 else 0

            key = f"M{mp}_C{cap//1_000_000}"
            results[key] = {
                "mp": mp, "cap": cap, "n": total,
                "pf1": pf1, "pfc": pfc,
                "pnl1": sum(pnl1), "pnlc": sum(pnl_c),
                "per1": sum(pnl1)/total, "perc": sum(pnl_c)/total,
                "ed": dict(ed), "occ": len(occ) if isinstance(occ, list) else occ,
                "fc_n": len(fc_t),
                "max_dd": max_dd, "avg_qty": avg_qty, "util": util,
                "rpf": rpf,
                "pnl_pct_cap": sum(pnl_c) / cap * 100,
            }

            print(f"  {pf1:>5.2f}", end="")
        print()

    print()
    print("=== PF (자본 가중) ===")
    print(f"{'':>8}", end="")
    for cap in capitals:
        print(f"  C{cap//1_000_000}M", end="")
    print()
    for mp in max_positions_list:
        print(f"max {mp}:", end="")
        for cap in capitals:
            key = f"M{mp}_C{cap//1_000_000}"
            if key in results:
                print(f"  {results[key]['pfc']:>5.2f}", end="")
            else:
                print(f"  {'N/A':>5}", end="")
        print()

    print()
    print("=== 거래수 ===")
    print(f"{'':>8}", end="")
    for cap in capitals:
        print(f"  C{cap//1_000_000}M", end="")
    print()
    for mp in max_positions_list:
        print(f"max {mp}:", end="")
        for cap in capitals:
            key = f"M{mp}_C{cap//1_000_000}"
            if key in results:
                print(f"  {results[key]['n']:>5}", end="")
            else:
                print(f"  {'N/A':>5}", end="")
        print()

    print()
    print("=== 자본 수익률 ===")
    print(f"{'':>8}", end="")
    for cap in capitals:
        print(f"  C{cap//1_000_000}M", end="")
    print()
    for mp in max_positions_list:
        print(f"max {mp}:", end="")
        for cap in capitals:
            key = f"M{mp}_C{cap//1_000_000}"
            if key in results:
                print(f" {results[key]['pnl_pct_cap']:>5.0f}%", end="")
            else:
                print(f"  {'N/A':>5}", end="")
        print()

    print()
    print("=== 상세 (1주 PF, 자본 PF, 거래수, DD, 수익률, 강세/횡보/약세) ===")
    hdr = f"{'key':<8} | {'pf1':>5} {'pfc':>5} {'n':>4} {'DD':>10} {'ret':>6} | {'bull':>5} {'side':>5} {'bear':>5}"
    print(hdr)
    print("-"*len(hdr))
    for mp in max_positions_list:
        for cap in capitals:
            key = f"M{mp}_C{cap//1_000_000}"
            if key not in results: continue
            r = results[key]
            rpf = r["rpf"]
            def _f(regime): return f"{rpf.get(regime,0):.2f}"
            print(f"{key:<8} | {r['pf1']:>5.2f} {r['pfc']:>5.2f} {r['n']:>4} {r['max_dd']:>10,.0f} {r['pnl_pct_cap']:>5.0f}% | {_f('강세'):>5} {_f('횡보'):>5} {_f('약세'):>5}")


if __name__ == "__main__":
    asyncio.run(main())
