"""scripts/grid_breakout_entry.py

max_entry_above_breakout_pct 그리드 측정.
값: [0.03, 0.05, 0.07, 0.10]

실행:
    python -u scripts/grid_breakout_entry.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from loguru import logger

logger.remove()
logger.add(sys.stderr, level="WARNING")

from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig, TradingConfig
from core.exit_logic import TimeDecayPhase
from data.db_manager import DbManager
from strategy.momentum_strategy import MomentumStrategy

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
OLD_START = "2025-04-01"
OLD_END   = "2026-04-10"
NEW_START = "2026-04-11"
NEW_END   = "2026-05-12"

GRID_VALUES = [0.03, 0.05, 0.07, 0.10]


def make_config(max_entry_pct: float, base: TradingConfig) -> TradingConfig:
    import dataclasses
    return dataclasses.replace(base, max_entry_above_breakout_pct=max_entry_pct)


async def run_period(
    config: TradingConfig,
    candles_cache: dict,
    ticker_to_market: dict,
    market_map: dict,
    backtest_config: BacktestConfig,
    start_date: str,
    end_date: str,
) -> tuple[list[dict], int]:
    """단일 기간 백테스트 실행. (거래 목록, entry_too_high 차단 합계) 반환."""
    import pandas as pd

    all_trades: list[dict] = []
    total_blocks = 0
    sd = date.fromisoformat(start_date)
    ed = date.fromisoformat(end_date)

    for ticker, candles in candles_cache.items():
        mask = (candles["ts"].dt.date >= sd) & (candles["ts"].dt.date <= ed)
        c = candles[mask].copy()
        if c.empty:
            continue

        market = ticker_to_market.get(ticker, "unknown")
        bt = Backtester(
            db=None,
            config=config,
            backtest_config=backtest_config,
            ticker_market=market,
            market_strong_by_date=market_map,
        )
        strategy = MomentumStrategy(config)
        result = await bt.run_multi_day_cached(ticker, c, strategy)
        for t in result.get("trades", []):
            t["ticker"] = ticker
            all_trades.append(t)
        total_blocks += strategy.diag_counters.get("entry_too_high", 0)

    return all_trades, total_blocks


def calc_stats(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0, "pf": float("nan"), "pnl": 0, "fc_pct": 0.0}
    import pandas as pd
    df = pd.DataFrame(trades)
    pnl = df["pnl"].sum()
    gp = df[df["pnl"] > 0]["pnl"].sum()
    gl = abs(df[df["pnl"] < 0]["pnl"].sum())
    pf = gp / gl if gl > 0 else float("inf")
    fc = (df.get("exit_reason", pd.Series()) == "forced_close").sum() / max(len(df), 1)
    return {"trades": len(df), "pf": round(pf, 3), "pnl": int(pnl), "fc_pct": round(fc * 100, 1)}


async def main() -> None:
    app_config = AppConfig.from_yaml()
    base_config = app_config.trading

    bt_cfg_raw = yaml.safe_load(open("config.yaml", encoding="utf-8")).get("backtest", {})
    backtest_config = BacktestConfig(
        commission=bt_cfg_raw.get("commission", 0.00015),
        tax=bt_cfg_raw.get("tax", 0.0020),
        slippage=bt_cfg_raw.get("slippage", 0.0003),
    )

    uni = yaml.safe_load(open("config/universe_backtest.yaml", encoding="utf-8"))
    stocks = uni.get("stocks", [])
    ticker_to_market = {s["ticker"]: s.get("market", "unknown") for s in stocks}

    print(f"종목 수: {len(stocks)}")

    db = DbManager(app_config.db_path)
    await db.init()
    bt_loader = Backtester(db=db, config=base_config, backtest_config=backtest_config)

    print(f"[LOAD] candles (2025-04-01 ~ 2026-05-12) ...")
    candles_cache: dict = {}
    for i, s in enumerate(stocks, 1):
        tk = s["ticker"]
        c = await bt_loader.load_candles(tk, "2025-04-01", "2026-05-12 23:59:59")
        if not c.empty:
            candles_cache[tk] = c
        if i % 10 == 0:
            print(f"  loaded {i}/{len(stocks)}", flush=True)
    await db.close()

    print(f"[LOAD] done {len(candles_cache)}/{len(stocks)}")

    market_map = build_market_strong_by_date(app_config.db_path, ma_length=base_config.market_ma_length)

    # ---------------------------------------------------------------------------
    # 기존 구간
    # ---------------------------------------------------------------------------
    print()
    print("=== 기존 구간 (2025-04-01 ~ 2026-04-10) ===")
    print(f"{'max_pct':>10} {'trades':>7} {'PF':>6} {'PnL':>10} {'too_high':>10} {'fc%':>6}")
    print("-" * 60)

    old_rows = []
    for pct in GRID_VALUES:
        cfg = make_config(pct, base_config)
        trades, blocks = await run_period(cfg, candles_cache, ticker_to_market, market_map, backtest_config, OLD_START, OLD_END)
        s = calc_stats(trades)
        old_rows.append({**s, "max_entry_pct": pct, "blocks": blocks})
        print(f"{pct:>10.0%} {s['trades']:>7} {s['pf']:>6.3f} {s['pnl']:>10,} {blocks:>10} {s['fc_pct']:>6.1f}%", flush=True)

    # ---------------------------------------------------------------------------
    # 확장 구간
    # ---------------------------------------------------------------------------
    print()
    print("=== 확장 구간 (2026-04-11 ~ 2026-05-12) ===")
    print(f"{'max_pct':>10} {'trades':>7} {'PF':>6} {'PnL':>10} {'too_high':>10} {'fc%':>6}")
    print("-" * 60)

    new_rows = []
    for pct in GRID_VALUES:
        cfg = make_config(pct, base_config)
        trades, blocks = await run_period(cfg, candles_cache, ticker_to_market, market_map, backtest_config, NEW_START, NEW_END)
        s = calc_stats(trades)
        new_rows.append({**s, "max_entry_pct": pct, "blocks": blocks})
        print(f"{pct:>10.0%} {s['trades']:>7} {s['pf']:>6.3f} {s['pnl']:>10,} {blocks:>10} {s['fc_pct']:>6.1f}%", flush=True)

    _write_report(old_rows, new_rows)
    print()
    print("리포트: reports/grid_breakout_entry.md")


def _write_report(old: list[dict], new: list[dict]) -> None:
    from datetime import datetime
    lines = [
        "# Grid: max_entry_above_breakout_pct",
        "",
        f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "> 41종목 universe_backtest.yaml, 1주 단위 비율 시뮬",
        "",
        "## 기존 구간 (2025-04-01 ~ 2026-04-10)",
        "",
        "| max_entry_pct | 거래 | PF | PnL | entry_too_high 차단 | forced_close% |",
        "|---|---|---|---|---|---|",
    ]
    for r in old:
        lines.append(
            f"| {r['max_entry_pct']:.0%} | {r['trades']} | {r['pf']:.3f} | "
            f"{r['pnl']:,} | {r['blocks']} | {r['fc_pct']:.1f}% |"
        )
    lines += [
        "",
        "## 확장 구간 (2026-04-11 ~ 2026-05-12)",
        "",
        "| max_entry_pct | 거래 | PF | PnL | entry_too_high 차단 | forced_close% |",
        "|---|---|---|---|---|---|",
    ]
    for r in new:
        lines.append(
            f"| {r['max_entry_pct']:.0%} | {r['trades']} | {r['pf']:.3f} | "
            f"{r['pnl']:,} | {r['blocks']} | {r['fc_pct']:.1f}% |"
        )
    lines += [
        "",
        "## 판단 기준",
        "- 기존 구간 PF >= 3.5, PnL >= 250K 유지",
        "- entry_too_high 차단건수: 엄격할수록 PF 영향 확인",
    ]

    out = Path(__file__).parent.parent / "reports" / "grid_breakout_entry.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
