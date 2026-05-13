"""scripts/grid_breakout_entry.py — max_entry_above_breakout_pct 그리드 측정.

값: [0.03, 0.05, 0.07, 0.10]
두 기간(기존/확장) 동시 측정.

사용:
    python -u scripts/grid_breakout_entry.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")

from backtest.backtester import Backtester
from strategy.momentum_strategy import MomentumStrategy
from utils.grid_runner import GridCache, load_candle_cache

OLD_START = "2025-04-01"
OLD_END   = "2026-04-10"
NEW_START = "2026-04-11"
NEW_END   = "2026-05-12"

GRID_VALUES = [0.03, 0.05, 0.07, 0.10]


async def run_period(
    pct: float,
    cache: GridCache,
    start_date: str,
    end_date: str,
) -> tuple[list[dict], int]:
    """단일 max_entry_pct × 단일 기간 → (trades, entry_too_high 차단수)."""
    import pandas as pd

    cfg = dataclasses.replace(cache.base_config, max_entry_above_breakout_pct=pct)
    sd = date.fromisoformat(start_date)
    ed = date.fromisoformat(end_date)

    all_trades: list[dict] = []
    total_blocks = 0

    for ticker, candles in cache.candles.items():
        mask = (candles["ts"].dt.date >= sd) & (candles["ts"].dt.date <= ed)
        c = candles[mask].copy()
        if c.empty:
            continue

        market = cache.ticker_to_market.get(ticker, "unknown")
        bt = Backtester(
            db=None, config=cfg, backtest_config=cache.bt_config,
            ticker_market=market, market_strong_by_date=cache.market_map,
        )
        strategy = MomentumStrategy(cfg)
        result = await bt.run_multi_day_cached(ticker, c, strategy)
        for t in result.get("trades", []):
            t["ticker"] = ticker
            all_trades.append(t)
        total_blocks += strategy.diag_counters.get("entry_too_high", 0)

    return all_trades, total_blocks


def calc_stats(trades: list[dict]) -> dict:
    import pandas as pd
    if not trades:
        return {"trades": 0, "pf": float("nan"), "pnl": 0, "fc_pct": 0.0}
    df = pd.DataFrame(trades)
    gp = df[df["pnl"] > 0]["pnl"].sum()
    gl = abs(df[df["pnl"] < 0]["pnl"].sum())
    pf = gp / gl if gl > 0 else float("inf")
    fc = (df.get("exit_reason", pd.Series()) == "forced_close").sum() / max(len(df), 1)
    return {
        "trades": len(df),
        "pf": round(pf, 3),
        "pnl": int(df["pnl"].sum()),
        "fc_pct": round(fc * 100, 1),
    }


async def main() -> None:
    # 캔들 로드 (전 기간, 1회)
    cache = await load_candle_cache("2025-04-01", "2026-05-12")

    print()
    print("=== 기존 구간 (2025-04-01 ~ 2026-04-10) ===", flush=True)
    print(f"{'max_pct':>10} {'trades':>7} {'PF':>6} {'PnL':>10} {'too_high':>10} {'fc%':>6}", flush=True)
    print("-" * 60, flush=True)

    old_rows = []
    for pct in GRID_VALUES:
        trades, blocks = await run_period(pct, cache, OLD_START, OLD_END)
        s = calc_stats(trades)
        old_rows.append({**s, "max_entry_pct": pct, "blocks": blocks})
        print(
            f"{pct:>10.0%} {s['trades']:>7} {s['pf']:>6.3f} "
            f"{s['pnl']:>10,} {blocks:>10} {s['fc_pct']:>6.1f}%",
            flush=True,
        )

    print()
    print("=== 확장 구간 (2026-04-11 ~ 2026-05-12) ===", flush=True)
    print(f"{'max_pct':>10} {'trades':>7} {'PF':>6} {'PnL':>10} {'too_high':>10} {'fc%':>6}", flush=True)
    print("-" * 60, flush=True)

    new_rows = []
    for pct in GRID_VALUES:
        trades, blocks = await run_period(pct, cache, NEW_START, NEW_END)
        s = calc_stats(trades)
        new_rows.append({**s, "max_entry_pct": pct, "blocks": blocks})
        print(
            f"{pct:>10.0%} {s['trades']:>7} {s['pf']:>6.3f} "
            f"{s['pnl']:>10,} {blocks:>10} {s['fc_pct']:>6.1f}%",
            flush=True,
        )

    _write_report(old_rows, new_rows)
    print("\n리포트: reports/grid_breakout_entry.md", flush=True)


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
