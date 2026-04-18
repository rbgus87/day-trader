"""scripts/grid_cooldown_maxtrades.py — 쿨다운 × 일일 최대거래 그리드.

현재 baseline (cooldown 120, max_trades 2)에서 재진입이 사실상 0에 가까움.
쿨다운을 30/60/90으로 줄이고 max_trades 2/3 조합으로 재진입 효과 측정.

시나리오 7종 (control 포함):
  CD120_MT2 : 현재 (control)
  CD90_MT2 / CD60_MT2 / CD30_MT2
  CD90_MT3 / CD60_MT3 / CD30_MT3

기타 파라미터 (min_breakout 3%, BE3, ADX 20 등) 모두 현재 config 그대로.

출력: reports/grid_cooldown_maxtrades.md
"""
import asyncio
import os
import pickle
import sqlite3
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from loguru import logger

logger.remove()

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig, TradingConfig
from data.db_manager import DbManager

SCENARIOS = [
    # (name, cooldown_min, max_trades)
    ("CD120_MT2", 120, 2),
    ("CD90_MT2",   90, 2),
    ("CD60_MT2",   60, 2),
    ("CD30_MT2",   30, 2),
    ("CD90_MT3",   90, 3),
    ("CD60_MT3",   60, 3),
    ("CD30_MT3",   30, 3),
]


def _worker(args):
    tk, tkm, cp, tcfg_dict, bcfg, mm = args
    tcfg = TradingConfig(**tcfg_dict)
    from backtest.backtester import Backtester as _BT
    from strategy.momentum_strategy import MomentumStrategy

    candles = pickle.loads(cp)
    strategy = MomentumStrategy(tcfg)
    bt = _BT(
        db=None, config=tcfg, backtest_config=bcfg,
        ticker_market=tkm, market_strong_by_date=mm,
    )
    kpi = asyncio.run(bt.run_multi_day_cached(tk, candles, strategy))
    return tk, kpi


def summarize(trades, regime_map):
    if not trades:
        return None
    pnl = np.array([t["pnl"] for t in trades])
    gp = float(pnl[pnl > 0].sum())
    gl = float(abs(pnl[pnl < 0].sum()))
    pf = gp / gl if gl > 0 else 0.0
    n = len(trades)
    ed = Counter(t.get("exit_reason", "?") for t in trades)
    wins = int((pnl > 0).sum())

    # 재진입 건수: (ticker, date) 기준 같은 날 2회+ 진입 카운트
    # trades 리스트에는 ticker / entry_ts 있음. 당일 진입 수 = 같은 (ticker, date)의 trade 수
    same_day = defaultdict(int)
    for t in trades:
        try:
            tk = t.get("ticker", "?")
            d = pd.to_datetime(t["entry_ts"]).date()
            same_day[(tk, d)] += 1
        except Exception:
            pass
    reentry_count = sum(cnt - 1 for cnt in same_day.values() if cnt >= 2)
    reentry_events = sum(1 for cnt in same_day.values() if cnt >= 2)

    # 시장 국면별 PF
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
        "n": n, "pf": pf, "pnl": float(pnl.sum()),
        "per_trade": float(pnl.sum()) / n if n else 0.0,
        "wins": wins, "win_rate": wins / n * 100 if n else 0.0,
        "ed": dict(ed), "rpf": rpf,
        "reentry_events": reentry_events, "reentry_count": reentry_count,
    }


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
    base_cfg = app.trading
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
    loader = Backtester(db=db, config=base_cfg, backtest_config=bcfg)
    cc = {}
    for s in stocks:
        c = await loader.load_candles(s["ticker"], "2025-04-01", "2026-04-15 23:59:59")
        if not c.empty:
            cc[s["ticker"]] = pickle.dumps(c)
    await db.close()
    mm = build_market_strong_by_date(app.db_path, ma_length=base_cfg.market_ma_length)
    regime_map = build_regime_map(app.db_path)
    w = max(2, (os.cpu_count() or 2) - 1)

    print(f"universe 로드: {len(cc)}/{len(stocks)}종목")
    print(f"base: cooldown={base_cfg.cooldown_minutes}, "
          f"max_trades_per_day={base_cfg.max_trades_per_day}")
    print()

    results = {}
    # 트레이드 메타는 종목 워커에서 ticker 미포함 상태로 올 수 있어 보강 필요
    for name, cd, mt in SCENARIOS:
        print(f"=== {name} (cooldown={cd}min, max_trades={mt}) ===")
        tcfg = replace(base_cfg, cooldown_minutes=cd, max_trades_per_day=mt)
        tcfg_dict = asdict(tcfg)
        tasks = [(tk, tkm.get(tk, "?"), cc[tk], tcfg_dict, bcfg, mm) for tk in cc]
        all_trades = []
        with ProcessPoolExecutor(max_workers=w) as ex:
            for tk, kpi in ex.map(_worker, tasks):
                for t in kpi.get("trades", []):
                    t["ticker"] = tk  # 재진입 카운트용
                    all_trades.append(t)
        stats = summarize(all_trades, regime_map)
        if stats is None:
            print("  거래 없음")
            results[name] = None
            continue
        results[name] = stats
        ed = stats["ed"]
        tot = sum(ed.values()) or 1
        fc = ed.get("forced_close", 0) / tot * 100
        be = ed.get("breakeven_stop", 0) / tot * 100
        print(f"  n={stats['n']}, PF={stats['pf']:.2f}, PnL={stats['pnl']:+,.0f}, "
              f"per={stats['per_trade']:+,.0f}, 승률={stats['win_rate']:.1f}%, "
              f"FC={fc:.1f}%, BE={be:.1f}%, 재진입={stats['reentry_count']}건")

    # 리포트
    control = results.get("CD120_MT2")
    report = [
        "# Cooldown × Max Trades Grid",
        "",
        f"기간: 2025-04-01 ~ 2026-04-15, Universe {len(cc)}종목",
        f"고정: min_breakout_pct=3%, BE3 on, ADX 20, buy_end 12:00, stop -8%",
        "",
        "## 시나리오별 결과",
        "",
        "| 시나리오 | cooldown | max_trades | 거래수 | PF | 총 PnL | 거래당 | 승률 | FC% | BE% | **재진입** |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, cd, mt in SCENARIOS:
        r = results.get(name)
        if not r:
            continue
        ed = r["ed"]
        tot = sum(ed.values()) or 1
        fc = ed.get("forced_close", 0) / tot * 100
        be = ed.get("breakeven_stop", 0) / tot * 100
        report.append(
            f"| {name} | {cd} | {mt} | {r['n']} | {r['pf']:.2f} | "
            f"{r['pnl']:+,.0f} | {r['per_trade']:+,.0f} | "
            f"{r['win_rate']:.1f}% | {fc:.1f} | {be:.1f} | "
            f"**{r['reentry_count']}** ({r['reentry_events']}회) |"
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
            f"| {name} | {rpf.get('강세', 0):.2f} | "
            f"{rpf.get('횡보', 0):.2f} | {rpf.get('약세', 0):.2f} |"
        )

    if control:
        report += [
            "",
            "## Control (CD120_MT2) 대비 델타",
            "",
            "| 시나리오 | ΔPF | Δ거래수 | Δ총PnL | Δ재진입 |",
            "|---|---|---|---|---|",
        ]
        c_pf = control["pf"]; c_n = control["n"]; c_pnl = control["pnl"]
        c_re = control["reentry_count"]
        for name, _, _ in SCENARIOS:
            if name == "CD120_MT2":
                continue
            r = results.get(name)
            if not r:
                continue
            report.append(
                f"| {name} | {r['pf']-c_pf:+.2f} | {r['n']-c_n:+d} | "
                f"{r['pnl']-c_pnl:+,.0f} | {r['reentry_count']-c_re:+d} |"
            )

    Path("reports").mkdir(exist_ok=True)
    with open("reports/grid_cooldown_maxtrades.md", "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print("\nreport: reports/grid_cooldown_maxtrades.md")


if __name__ == "__main__":
    asyncio.run(main())
