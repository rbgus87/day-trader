"""scripts/per_ticker_analysis.py — 종목별 성과 분석.

현재 config (min_breakout_pct=3%, BE3 ON) 기준 41종목 개별 백테스트.
PF 상위/하위 비교, 가격대/시장별 통계, 상관 분석.

출력: reports/per_ticker_analysis.md
"""
import asyncio
import os
import pickle
import sqlite3
import sys
from collections import Counter
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
from data.db_manager import DbManager

DB_PATH = "daytrader.db"


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


def load_meta(db_path: str, tickers: list[str]) -> dict[str, dict]:
    """ticker별 메타 (ATR, 현재가, 일평균 거래대금)."""
    meta = {}
    conn = sqlite3.connect(db_path)
    try:
        # ATR
        atr_rows = conn.execute(
            "SELECT ticker, atr_pct FROM ticker_atr t "
            "WHERE dt = (SELECT MAX(dt) FROM ticker_atr WHERE ticker=t.ticker)"
        ).fetchall()
        atr_map = {t: pct for t, pct in atr_rows if pct is not None}

        for tk in tickers:
            meta[tk] = {"atr_pct": atr_map.get(tk, 0.0)}
            # 현재가 (가장 최근 close)
            row = conn.execute(
                "SELECT close FROM intraday_candles WHERE ticker=? AND tf='1m' "
                "ORDER BY ts DESC LIMIT 1",
                (tk,),
            ).fetchone()
            meta[tk]["current_price"] = float(row[0]) if row else 0.0

            # 일평균 거래대금 (억): 최근 30일
            row2 = conn.execute(
                "SELECT AVG(d.vol_amt) FROM ("
                "  SELECT substr(ts,1,10) AS dt, SUM(volume * close) AS vol_amt "
                "  FROM intraday_candles WHERE ticker=? AND tf='1m' "
                "  GROUP BY substr(ts,1,10) "
                "  ORDER BY dt DESC LIMIT 30"
                ") d",
                (tk,),
            ).fetchone()
            meta[tk]["daily_amount"] = (
                float(row2[0]) / 1e8 if row2 and row2[0] else 0.0
            )
    finally:
        conn.close()
    return meta


def analyze_trades(trades: list[dict]) -> dict:
    """trade 리스트에서 KPI 집계."""
    if not trades:
        return {
            "n": 0, "pf": 0.0, "total_pnl": 0.0, "per_trade": 0.0,
            "win_rate": 0.0, "exit_dist": {},
        }
    pnl = np.array([t["pnl"] for t in trades])
    gp = float(pnl[pnl > 0].sum())
    gl = float(abs(pnl[pnl < 0].sum()))
    pf = gp / gl if gl > 0 else float("inf")
    n = len(trades)
    wins = int((pnl > 0).sum())
    ed = Counter(t.get("exit_reason", "?") for t in trades)
    exit_dist = {k: v / n * 100 for k, v in ed.items()}
    return {
        "n": n, "pf": pf,
        "total_pnl": float(pnl.sum()),
        "per_trade": float(pnl.sum()) / n if n else 0.0,
        "win_rate": wins / n * 100 if n else 0.0,
        "exit_dist": exit_dist,
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
    name_map = {s["ticker"]: s.get("name", "") for s in stocks}
    market_map = {s["ticker"]: s.get("market", "?") for s in stocks}

    db = DbManager(app.db_path)
    await db.init()
    loader = Backtester(db=db, config=cfg, backtest_config=bcfg)

    print(f"Universe: {len(stocks)}종목, min_breakout_pct={cfg.min_breakout_pct:.1%}, "
          f"breakeven_enabled={cfg.breakeven_enabled}")

    cc = {}
    for s in stocks:
        c = await loader.load_candles(s["ticker"], "2025-04-01", "2026-04-15 23:59:59")
        if not c.empty:
            cc[s["ticker"]] = pickle.dumps(c)
    await db.close()

    mm = build_market_strong_by_date(app.db_path, ma_length=cfg.market_ma_length)
    w = max(2, (os.cpu_count() or 2) - 1)
    base_dict = asdict(cfg)

    # 메타 로드
    meta = load_meta(app.db_path, list(cc.keys()))

    print(f"loaded {len(cc)}/{len(stocks)} → 백테스트 시작 (워커 {w})\n")

    tasks = [
        (tk, market_map.get(tk, "?"), cc[tk], base_dict, bcfg, mm)
        for tk in cc
    ]
    per_ticker = {}
    with ProcessPoolExecutor(max_workers=w) as ex:
        for tk, kpi in ex.map(_worker, tasks):
            trades = kpi.get("trades", [])
            stats = analyze_trades(trades)
            # BE3 발동률: breakeven_stop 비율 + 실제 발동(청산 전 포함) 근사
            be_cut = stats["exit_dist"].get("breakeven_stop", 0.0)
            per_ticker[tk] = {
                "ticker": tk,
                "name": name_map.get(tk, ""),
                "market": market_map.get(tk, "?").upper(),
                "atr_pct": meta[tk]["atr_pct"],
                "current_price": meta[tk]["current_price"],
                "daily_amount": meta[tk]["daily_amount"],
                **stats,
                "be_cut_ratio": be_cut,
            }

    # PF 내림차순 정렬
    rows = sorted(
        per_ticker.values(),
        key=lambda x: (x["pf"] if x["pf"] != float("inf") else 99), reverse=True,
    )

    # 리포트 작성
    report = [
        "# Per-Ticker Analysis (BE3 + min_breakout 3%)",
        "",
        f"기간: 2025-04-01 ~ 2026-04-15, Universe {len(cc)}종목",
        f"config: min_breakout_pct={cfg.min_breakout_pct:.1%}, "
        f"breakeven_enabled={cfg.breakeven_enabled}, "
        f"stop_loss=-8%, atr_trail x1.0 (2%/10%)",
        "",
        "## 전체 종목 테이블 (PF 내림차순)",
        "",
        "| # | 티커 | 종목명 | 시장 | ATR% | 가격 | 거래대금억 | 거래수 | PF | 총PnL | 거래당 | 승률 | FC% | SL% | BE% |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, 1):
        ed = r["exit_dist"]
        pf_str = "∞" if r["pf"] == float("inf") else f"{r['pf']:.2f}"
        report.append(
            f"| {i} | {r['ticker']} | {r['name'][:10]} | {r['market']} | "
            f"{r['atr_pct']:.1f} | {r['current_price']:,.0f} | "
            f"{r['daily_amount']:,.0f} | {r['n']} | {pf_str} | "
            f"{r['total_pnl']:+,.0f} | {r['per_trade']:+,.0f} | "
            f"{r['win_rate']:.0f}% | {ed.get('forced_close',0):.0f} | "
            f"{ed.get('stop_loss',0):.0f} | {ed.get('breakeven_stop',0):.0f} |"
        )

    # 상위 10 vs 하위 10 (inf 제외)
    finite = [r for r in rows if r["pf"] != float("inf") and r["n"] > 0]
    top10 = finite[:10]
    bot10 = finite[-10:]

    def avg(lst, key):
        vals = [r[key] for r in lst if r[key] > 0 or key == "total_pnl"]
        return np.mean(vals) if vals else 0.0

    report += [
        "",
        "## 상위 10 vs 하위 10 비교",
        "",
        "| 특성 | 상위 10 평균 | 하위 10 평균 | 차이 |",
        "|---|---|---|---|",
    ]
    for key, label in [
        ("atr_pct", "ATR%"),
        ("daily_amount", "일거래대금(억)"),
        ("current_price", "가격"),
        ("n", "거래수"),
        ("pf", "PF"),
        ("per_trade", "거래당 PnL"),
        ("win_rate", "승률%"),
        ("be_cut_ratio", "BE 청산%"),
    ]:
        top_avg = np.mean([r[key] for r in top10])
        bot_avg = np.mean([r[key] for r in bot10])
        diff = top_avg - bot_avg
        fmt = ",.1f" if key in ("atr_pct", "pf", "win_rate", "be_cut_ratio") else ",.0f"
        report.append(
            f"| {label} | {top_avg:{fmt}} | {bot_avg:{fmt}} | {diff:+{fmt}} |"
        )

    # 시장 비율
    top_kospi = sum(1 for r in top10 if r["market"] == "KOSPI")
    bot_kospi = sum(1 for r in bot10 if r["market"] == "KOSPI")
    report.append(
        f"| 시장 KOSPI:KOSDAQ | {top_kospi}:{10-top_kospi} | "
        f"{bot_kospi}:{10-bot_kospi} | — |"
    )

    # forced/stop 청산 비율
    top_fc = np.mean([r["exit_dist"].get("forced_close", 0) for r in top10])
    bot_fc = np.mean([r["exit_dist"].get("forced_close", 0) for r in bot10])
    top_sl = np.mean([r["exit_dist"].get("stop_loss", 0) for r in top10])
    bot_sl = np.mean([r["exit_dist"].get("stop_loss", 0) for r in bot10])
    report.append(f"| forced_close% | {top_fc:.1f} | {bot_fc:.1f} | {top_fc - bot_fc:+.1f} |")
    report.append(f"| stop_loss% | {top_sl:.1f} | {bot_sl:.1f} | {top_sl - bot_sl:+.1f} |")

    # PF < 1 상세
    pf_lt_1 = [r for r in finite if r["pf"] < 1.0]
    report += [
        "",
        f"## PF < 1 종목 상세 ({len(pf_lt_1)}개)",
        "",
        "| 티커 | 종목명 | PF | 거래수 | 총PnL | 거래당 | 승률 | ATR% | 가격 | 시장 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in pf_lt_1:
        pf_str = f"{r['pf']:.2f}"
        report.append(
            f"| {r['ticker']} | {r['name'][:10]} | {pf_str} | {r['n']} | "
            f"{r['total_pnl']:+,.0f} | {r['per_trade']:+,.0f} | "
            f"{r['win_rate']:.0f}% | {r['atr_pct']:.1f} | "
            f"{r['current_price']:,.0f} | {r['market']} |"
        )

    # 상관 분석
    arr_atr = np.array([r["atr_pct"] for r in finite])
    arr_amt = np.array([r["daily_amount"] for r in finite])
    arr_prc = np.array([r["current_price"] for r in finite])
    arr_n = np.array([r["n"] for r in finite])
    arr_be = np.array([r["be_cut_ratio"] for r in finite])
    arr_pf = np.array([r["pf"] for r in finite])

    def corr(x):
        # NaN 방어
        if x.std() == 0 or arr_pf.std() == 0:
            return 0.0
        return float(np.corrcoef(x, arr_pf)[0, 1])

    report += [
        "",
        "## 특성 vs PF 상관계수 (Pearson)",
        "",
        "| 특성 | r | 방향 |",
        "|---|---|---|",
        f"| ATR% | {corr(arr_atr):+.3f} | {'높을수록 PF ↑' if corr(arr_atr) > 0.1 else '약함' if abs(corr(arr_atr)) < 0.1 else '높을수록 PF ↓'} |",
        f"| 일거래대금 | {corr(arr_amt):+.3f} | {'높을수록 PF ↑' if corr(arr_amt) > 0.1 else '약함' if abs(corr(arr_amt)) < 0.1 else '높을수록 PF ↓'} |",
        f"| 가격대 | {corr(arr_prc):+.3f} | {'높을수록 PF ↑' if corr(arr_prc) > 0.1 else '약함' if abs(corr(arr_prc)) < 0.1 else '높을수록 PF ↓'} |",
        f"| 거래수 | {corr(arr_n):+.3f} | {'많을수록 PF ↑' if corr(arr_n) > 0.1 else '약함' if abs(corr(arr_n)) < 0.1 else '많을수록 PF ↓'} |",
        f"| BE 청산% | {corr(arr_be):+.3f} | {'높을수록 PF ↑' if corr(arr_be) > 0.1 else '약함' if abs(corr(arr_be)) < 0.1 else '높을수록 PF ↓'} |",
    ]

    # 가격대별 PF
    bins = [(0, 10_000), (10_000, 50_000), (50_000, 100_000), (100_000, 200_000), (200_000, 10_000_000)]
    report += [
        "",
        "## 가격대별 PF",
        "",
        "| 가격대 | 종목수 | 평균 PF | 총 PnL | 총 거래수 |",
        "|---|---|---|---|---|",
    ]
    for lo, hi in bins:
        bucket = [r for r in finite if lo <= r["current_price"] < hi]
        if not bucket:
            report.append(f"| {lo:,} ~ {hi:,} | 0 | — | — | — |")
            continue
        pfs = [r["pf"] for r in bucket]
        tot_pnl = sum(r["total_pnl"] for r in bucket)
        tot_n = sum(r["n"] for r in bucket)
        report.append(
            f"| {lo:,} ~ {hi:,} | {len(bucket)} | {np.mean(pfs):.2f} | "
            f"{tot_pnl:+,.0f} | {tot_n} |"
        )

    # KOSPI vs KOSDAQ
    ksp = [r for r in finite if r["market"] == "KOSPI"]
    ksd = [r for r in finite if r["market"] == "KOSDAQ"]
    report += [
        "",
        "## KOSPI vs KOSDAQ",
        "",
        "| 시장 | 종목수 | 평균 PF | 거래수 | 총 PnL |",
        "|---|---|---|---|---|",
    ]
    for label, grp in [("KOSPI", ksp), ("KOSDAQ", ksd)]:
        if grp:
            avg_pf = np.mean([r["pf"] for r in grp])
            tot_n = sum(r["n"] for r in grp)
            tot_pnl = sum(r["total_pnl"] for r in grp)
            report.append(
                f"| {label} | {len(grp)} | {avg_pf:.2f} | {tot_n} | {tot_pnl:+,.0f} |"
            )

    Path("reports").mkdir(exist_ok=True)
    with open("reports/per_ticker_analysis.md", "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print("report: reports/per_ticker_analysis.md")

    # 콘솔 요약
    total_pnl = sum(r["total_pnl"] for r in finite)
    total_n = sum(r["n"] for r in finite)
    print(f"\n전체: PnL {total_pnl:+,.0f}, 거래 {total_n}건, PF<1 {len(pf_lt_1)}개")


if __name__ == "__main__":
    asyncio.run(main())
