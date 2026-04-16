"""scripts/capital_comparison.py — 자본별 전체 지표 비교 (max_positions=3 고정).

1주 가중 backtester 결과에 자본 사이징을 후적용하여 자본 가중 지표 산출.
"""
import asyncio, os, pickle, sqlite3, sys, yaml
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np, pandas as pd
from loguru import logger
logger.remove()

sys.path.insert(0, str(Path(__file__).parent.parent))
from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig
from data.db_manager import DbManager

DB_PATH = "daytrader.db"
MAX_POS = 3
CAPITALS = [1_000_000, 2_000_000, 3_000_000, 5_000_000]


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


def simulate_one(args):
    ticker, ticker_market, candles_pickle, trading_config, backtest_config, market_map = args
    import asyncio as _asyncio
    from backtest.backtester import Backtester as _Bt
    from strategy.momentum_strategy import MomentumStrategy
    candles = pickle.loads(candles_pickle)
    strategy = MomentumStrategy(trading_config)
    bt = _Bt(db=None, config=trading_config, backtest_config=backtest_config,
             ticker_market=ticker_market, market_strong_by_date=market_map)
    result = _asyncio.run(bt.run_multi_day_cached(ticker, candles, strategy))
    for t in result.get("trades", []):
        t["ticker"] = ticker
    return result


async def main():
    app = AppConfig.from_yaml()
    cfg = app.trading
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

    # 1주 가중 백테스트 (실제 backtester)
    print("실제 backtester로 1주 가중 백테스트...")
    tasks_list = [
        (tk, tkm.get(tk,"?"), cc[tk], cfg, bcfg, mm)
        for tk in cc
    ]
    all_trades = []
    with ProcessPoolExecutor(max_workers=w) as ex:
        for result in ex.map(simulate_one, tasks_list):
            all_trades.extend(result.get("trades", []))

    print(f"  전체 trades: {len(all_trades)}건 (1주 가중)")
    print()

    # 1주 가중 기본 메트릭
    pnl1_list = [t["pnl"] for t in all_trades]
    gp1 = sum(p for p in pnl1_list if p > 0)
    gl1 = abs(sum(p for p in pnl1_list if p < 0))
    pf1 = gp1/gl1 if gl1 else 0
    wins = sum(1 for p in pnl1_list if p > 0)
    total = len(all_trades)
    win_rate = wins / total if total else 0

    ed = Counter(t.get("exit_reason","?") for t in all_trades)

    print(f"1주 가중: PF={pf1:.2f}, n={total}, PnL={sum(pnl1_list):+,.0f}, WR={win_rate:.1%}")
    print(f"  fc={ed.get('forced_close',0)} sl={ed.get('stop_loss',0)} ts={ed.get('trailing_stop',0)}")
    print()

    # 자본별 후처리
    print("=" * 90)
    hdr = (f"{'지표':<20} | {'C100':>12} | {'C200':>12} | {'C300':>12} | {'C500':>12}")
    print(hdr)
    print("-" * 90)

    cap_results = {}

    for cap in CAPITALS:
        per_pos = cap / MAX_POS
        cap_trades = []
        for t in all_trades:
            qty = int(per_pos / t["entry_price"])
            if qty <= 0:
                continue
            pnl_cap = t["pnl"] * qty
            cap_trades.append({**t, "qty": qty, "pnl_cap": pnl_cap,
                               "buy_amount": t["entry_price"] * qty})

        n = len(cap_trades)
        pnl_cap_list = [t["pnl_cap"] for t in cap_trades]
        gpc = sum(p for p in pnl_cap_list if p > 0)
        glc = abs(sum(p for p in pnl_cap_list if p < 0))
        pfc = gpc/glc if glc else 0
        total_pnl_cap = sum(pnl_cap_list)

        # 1주 가중 (같은 거래 기준)
        pnl1_sub = [t["pnl"] for t in cap_trades]
        gp1s = sum(p for p in pnl1_sub if p > 0)
        gl1s = abs(sum(p for p in pnl1_sub if p < 0))
        pf1s = gp1s/gl1s if gl1s else 0

        wins_cap = sum(1 for t in cap_trades if t["pnl_cap"] > 0)
        wr = wins_cap / n if n else 0

        avg_qty = np.mean([t["qty"] for t in cap_trades])
        avg_buy = np.mean([t["buy_amount"] for t in cap_trades])
        util = avg_buy / per_pos if per_pos else 0

        # Max DD
        cum = peak = max_dd = 0.0
        for p in pnl_cap_list:
            cum += p
            if cum > peak: peak = cum
            dd = peak - cum
            if dd > max_dd: max_dd = dd
        dd_pct = max_dd / cap * 100 if cap else 0

        # 연속 손실
        max_consec = consec = 0
        for p in pnl_cap_list:
            if p < 0:
                consec += 1
                max_consec = max(max_consec, consec)
            else:
                consec = 0

        # 일별 PnL 변동성
        daily = defaultdict(float)
        for t in cap_trades:
            try:
                d = pd.to_datetime(t["entry_ts"]).strftime("%Y-%m-%d")
                daily[d] += t["pnl_cap"]
            except: pass
        daily_std = np.std(list(daily.values())) if daily else 0

        # 청산 분포
        ed_cap = Counter(t.get("exit_reason","?") for t in cap_trades)

        # 국면별 PF
        rt = defaultdict(list)
        for t in cap_trades:
            try:
                m = pd.to_datetime(t["entry_ts"]).strftime("%Y-%m")
                rt[regime_map.get(m,"?")].append(t)
            except: pass
        rpf = {}
        for r, tlist in rt.items():
            rg = sum(t["pnl"] for t in tlist if t["pnl"]>0)
            rl = abs(sum(t["pnl"] for t in tlist if t["pnl"]<0))
            rpf[r] = rg/rl if rl>0 else 0

        cap_results[cap] = {
            "n": n, "pf1": pf1s, "pfc": pfc,
            "pnl_cap": total_pnl_cap, "pnl1": sum(pnl1_sub),
            "per_cap": total_pnl_cap / n if n else 0,
            "per1": sum(pnl1_sub) / n if n else 0,
            "wr": wr, "avg_qty": avg_qty, "util": util,
            "max_dd": max_dd, "dd_pct": dd_pct,
            "max_consec": max_consec, "daily_std": daily_std,
            "ed": dict(ed_cap), "rpf": rpf,
            "ret_pct": total_pnl_cap / cap * 100,
            "monthly_ret": total_pnl_cap / 12,
            "daily_avg": total_pnl_cap / 254,  # ~254 거래일
        }

    # 출력
    def row(label, fmt, key):
        vals = []
        for cap in CAPITALS:
            r = cap_results[cap]
            if callable(key):
                vals.append(fmt.format(key(r)))
            else:
                vals.append(fmt.format(r[key]))
        print(f"{label:<20} | {vals[0]:>12} | {vals[1]:>12} | {vals[2]:>12} | {vals[3]:>12}")

    row("PF (1주 가중)", "{:.2f}", "pf1")
    row("PF (자본 가중)", "{:.2f}", "pfc")
    row("거래수", "{:d}", "n")
    row("총 PnL (1주)", "{:+,.0f}", "pnl1")
    row("총 PnL (자본)", "{:+,.0f}", "pnl_cap")
    row("거래당 PnL (1주)", "{:+,.0f}", "per1")
    row("거래당 PnL (자본)", "{:+,.0f}", "per_cap")
    row("승률", "{:.1%}", "wr")
    print("-" * 90)
    row("평균 qty", "{:.1f}", "avg_qty")
    row("자본 활용률", "{:.0%}", "util")
    row("연 수익률", "{:+.1f}%", "ret_pct")
    row("월평균 PnL", "{:+,.0f}", "monthly_ret")
    row("일평균 PnL", "{:+,.0f}", "daily_avg")
    print("-" * 90)
    row("Max DD (절대)", "{:,.0f}", "max_dd")
    row("Max DD (%자본)", "{:.1f}%", "dd_pct")
    row("최대 연속 손실", "{:d}건", "max_consec")
    row("일별 PnL 변동성", "{:,.0f}", "daily_std")
    print("-" * 90)
    row("forced_close", "{:d}", lambda r: r["ed"].get("forced_close", 0))
    row("stop_loss", "{:d}", lambda r: r["ed"].get("stop_loss", 0))
    row("trailing_stop", "{:d}", lambda r: r["ed"].get("trailing_stop", 0))
    print("-" * 90)
    row("강세 PF", "{:.2f}", lambda r: r["rpf"].get("강세", 0))
    row("횡보 PF", "{:.2f}", lambda r: r["rpf"].get("횡보", 0))
    row("약세 PF", "{:.2f}", lambda r: r["rpf"].get("약세", 0))


if __name__ == "__main__":
    asyncio.run(main())
