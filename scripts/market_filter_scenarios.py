"""scripts/market_filter_scenarios.py — 시장 필터 전략 3가지 시나리오 비교.

A) 완전 차단: market_filter_enabled=True, market_regime_reduce_enabled=False
B) 비활성화: market_filter_enabled=False
C) 사이즈 축소: market_filter_enabled=True, market_regime_reduce_enabled=True (50%)
"""

import asyncio
import dataclasses
import sys
from collections import Counter
from datetime import date as date_type
from pathlib import Path

import yaml
from loguru import logger

logger.remove()
logger.add(sys.stderr, level="WARNING")

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig
from data.db_manager import DbManager
from strategy.momentum_strategy import MomentumStrategy

CUT_DATE = "2026-04-11"
REPORT_PATH = Path("reports/market_filter_strategy.md")


# ---------------------------------------------------------------------------
# 공통 로더
# ---------------------------------------------------------------------------

async def load_candles_and_market(start: str, end: str):
    app_config = AppConfig.from_yaml()
    base_config = app_config.trading

    bt_cfg_raw = yaml.safe_load(
        open("config.yaml", encoding="utf-8")
    ).get("backtest", {})
    backtest_config = BacktestConfig(
        commission=bt_cfg_raw.get("commission", 0.00015),
        tax=bt_cfg_raw.get("tax", 0.0020),
        slippage=bt_cfg_raw.get("slippage", 0.0003),
    )

    uni = yaml.safe_load(open("config/universe_backtest.yaml", encoding="utf-8"))
    stocks = uni.get("stocks", [])
    ticker_to_market = {s["ticker"]: s.get("market", "unknown") for s in stocks}

    db = DbManager(app_config.db_path)
    await db.init()
    bt_loader = Backtester(db=db, config=base_config, backtest_config=backtest_config)

    print(f"[LOAD] candles ({len(stocks)} stocks, {start}~{end})")
    candles_cache: dict = {}
    for i, s in enumerate(stocks, 1):
        tk = s["ticker"]
        candles = await bt_loader.load_candles(tk, start, f"{end} 23:59:59")
        if not candles.empty:
            candles_cache[tk] = candles
        if i % 10 == 0:
            print(f"  loaded {i}/{len(stocks)}")
    print(f"[LOAD] done {len(candles_cache)}/{len(stocks)}")
    await db.close()

    market_map = build_market_strong_by_date(
        app_config.db_path, ma_length=base_config.market_ma_length
    )

    return base_config, backtest_config, candles_cache, ticker_to_market, market_map, app_config.db_path


# ---------------------------------------------------------------------------
# 시나리오 실행
# ---------------------------------------------------------------------------

async def run_scenario(
    label: str,
    base_config,
    backtest_config,
    candles_cache: dict,
    ticker_to_market: dict,
    market_map: dict,
    *,
    market_filter_enabled: bool,
    market_regime_reduce_enabled: bool,
) -> list[dict]:
    cfg = dataclasses.replace(
        base_config,
        market_filter_enabled=market_filter_enabled,
        market_regime_reduce_enabled=market_regime_reduce_enabled,
    )
    print(f"[{label}] filter={market_filter_enabled}, reduce={market_regime_reduce_enabled}")
    all_trades = []
    for i, (tk, candles) in enumerate(candles_cache.items(), 1):
        market = ticker_to_market.get(tk, "unknown")
        bt = Backtester(
            db=None,
            config=cfg,
            backtest_config=backtest_config,
            ticker_market=market,
            market_strong_by_date=market_map,
        )
        strategy = MomentumStrategy(cfg)
        result = await bt.run_multi_day_cached(tk, candles, strategy)
        for t in result.get("trades", []):
            t["ticker"] = tk
            t["ticker_market"] = market
            all_trades.append(t)
        if i % 10 == 0:
            print(f"  [{label}] {i}/{len(candles_cache)} tickers, trades={len(all_trades)}")
    print(f"  [{label}] done -- total trades={len(all_trades)}")
    return all_trades


# ---------------------------------------------------------------------------
# 통계 계산
# ---------------------------------------------------------------------------

def compute_stats(trades: list[dict], period_label: str, cut: str | None = None) -> dict:
    """전체 / old(~cut) / new(cut~) 기간 통계."""
    def _stats(subset: list[dict]) -> dict:
        if not subset:
            return {"trades": 0, "pf": 0.0, "pnl": 0, "pnl_per_trade": 0.0,
                    "forced_close_pct": 0.0, "mdd": 0.0}
        n = len(subset)
        pnl = sum(t["pnl"] for t in subset)
        gp = sum(t["pnl"] for t in subset if t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in subset if t["pnl"] < 0))
        pf = gp / gl if gl > 0 else float("inf")
        fc = sum(1 for t in subset if t.get("exit_reason") == "forced_close")
        # MDD: 누적 PnL 곡선 기준
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        # exit_ts 기준 정렬
        sorted_t = sorted(subset, key=lambda x: x.get("exit_ts") or "")
        for t in sorted_t:
            cumulative += t["pnl"]
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd
        return {
            "trades": n,
            "pf": pf,
            "pnl": int(pnl),
            "pnl_per_trade": pnl / n,
            "forced_close_pct": fc / n * 100,
            "mdd": int(max_dd),
        }

    all_stats = _stats(trades)
    result = {"all": all_stats}

    if cut:
        old = [t for t in trades if _exit_date_str(t) < cut]
        new = [t for t in trades if _exit_date_str(t) >= cut]
        result["old"] = _stats(old)
        result["new"] = _stats(new)

        # 약세 시장 거래 vs 정상 시장 거래 통계
        weak = [t for t in trades if t.get("size_factor", 1.0) != 1.0]
        normal = [t for t in trades if t.get("size_factor", 1.0) == 1.0]
        result["weak_market"] = _stats(weak)
        result["normal_market"] = _stats(normal)

    return result


def _exit_date_str(t: dict) -> str:
    exit_ts = t.get("exit_ts")
    if exit_ts is None:
        return ""
    try:
        if hasattr(exit_ts, "strftime"):
            return exit_ts.strftime("%Y-%m-%d")
        return str(exit_ts)[:10]
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 리포트 생성
# ---------------------------------------------------------------------------

def format_row(label: str, s: dict) -> str:
    pf_str = f"{s['pf']:.3f}" if s['pf'] != float("inf") else "inf"
    return (
        f"| {label:<14} | {s['trades']:>6} | {pf_str:>8} | "
        f"{s['pnl']:>+12,} | {s['pnl_per_trade']:>+10.1f} | "
        f"{s['forced_close_pct']:>8.1f}% | {s['mdd']:>12,} |"
    )


def write_report(results: dict[str, list[dict]], cut: str):
    REPORT_PATH.parent.mkdir(exist_ok=True)

    labels = {
        "A": "A) 완전 차단",
        "B": "B) 비활성화",
        "C": "C) 사이즈 축소(50%)",
    }
    header = (
        "| 시나리오       | 거래수 |       PF |          PnL |   PnL/건 | forced_% |          MDD |"
    )
    sep = (
        "|:--------------|-------:|---------:|-------------:|---------:|---------:|-------------:|"
    )

    lines = [
        "# 시장 필터 전략 비교 (3 Scenarios)",
        "",
        f"> 기간: 2025-04-01 ~ 2026-05-12 / 41종목 / CUT={cut}",
        f"> 비용: commission 0.015% + tax 0.20% + slippage 0.03%",
        "",
    ]

    for key in ["A", "B", "C"]:
        trades = results[key]
        stats = compute_stats(trades, labels[key], cut)
        exit_dist = Counter(t.get("exit_reason", "?") for t in trades)

        lines += [
            f"## {labels[key]}",
            "",
            "### 전체 기간",
            header, sep,
            format_row("전체", stats["all"]),
            format_row("  기존(~04-10)", stats["old"]),
            format_row("  확장(04-11~)", stats["new"]),
            "",
        ]

        if key == "C":
            lines += [
                "### 시장 상태별 (C 시나리오만)",
                header, sep,
                format_row("  정상 시장", stats["normal_market"]),
                format_row("  약세 시장(×0.5)", stats["weak_market"]),
                "",
            ]

        lines += [
            "### 청산 분포",
            "```",
        ]
        for reason, cnt in exit_dist.most_common():
            ratio = cnt / len(trades) * 100 if trades else 0
            lines.append(f"  {reason:<22} {cnt:>4} ({ratio:>5.1f}%)")
        lines += ["```", ""]

    # 3-way 비교 테이블
    lines += [
        "---",
        "",
        "## 3-way 비교 요약",
        "",
        header, sep,
    ]
    for key, label in labels.items():
        trades = results[key]
        stats = compute_stats(trades, label, cut)
        short = {"A": "A) 차단", "B": "B) 비활성", "C": "C) 축소"}[key]
        lines += [
            f"**{short} - 전체**",
            format_row("전체", stats["all"]),
            format_row("  기존", stats["old"]),
            format_row("  확장", stats["new"]),
            "",
        ]

    lines += [
        "---",
        "",
        "## 결론",
        "",
        "*(수동 기입)*",
        "",
    ]

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"[REPORT] {REPORT_PATH}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

async def main():
    START = "2025-04-01"
    END = "2026-05-12"
    CUT = CUT_DATE

    print("=" * 64)
    print(f" 시장 필터 전략 3-Scenario 비교 ({START} ~ {END})")
    print("=" * 64)

    (base_config, backtest_config, candles_cache,
     ticker_to_market, market_map, _) = await load_candles_and_market(START, END)

    results = {}

    # Scenario A: 완전 차단 (현재)
    results["A"] = await run_scenario(
        "A", base_config, backtest_config, candles_cache, ticker_to_market, market_map,
        market_filter_enabled=True,
        market_regime_reduce_enabled=False,
    )

    # Scenario B: 비활성화
    results["B"] = await run_scenario(
        "B", base_config, backtest_config, candles_cache, ticker_to_market, market_map,
        market_filter_enabled=False,
        market_regime_reduce_enabled=False,
    )

    # Scenario C: 사이즈 축소 50%
    results["C"] = await run_scenario(
        "C", base_config, backtest_config, candles_cache, ticker_to_market, market_map,
        market_filter_enabled=True,
        market_regime_reduce_enabled=True,
    )

    # 콘솔 요약 출력
    print()
    print("=" * 64)
    print(" 결과 요약 (전체 기간)")
    print("=" * 64)
    for key, label in [("A", "A) 완전 차단"), ("B", "B) 비활성"), ("C", "C) 50%축소")]:
        trades = results[key]
        if not trades:
            print(f"  [{key}] no trades")
            continue
        gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
        pf = gp / gl if gl > 0 else float("inf")
        pnl = sum(t["pnl"] for t in trades)
        old = [t for t in trades if _exit_date_str(t) < CUT]
        new = [t for t in trades if _exit_date_str(t) >= CUT]
        pf_old = (
            sum(t["pnl"] for t in old if t["pnl"] > 0) /
            abs(sum(t["pnl"] for t in old if t["pnl"] < 0))
            if any(t["pnl"] < 0 for t in old) else float("inf")
        )
        pf_new = (
            sum(t["pnl"] for t in new if t["pnl"] > 0) /
            abs(sum(t["pnl"] for t in new if t["pnl"] < 0))
            if any(t["pnl"] < 0 for t in new) else float("inf")
        )
        print(
            f"  [{key}] {label:<18} "
            f"trades={len(trades):>4} PF={pf:.3f} PnL={pnl:>+12,.0f} "
            f"| old PF={pf_old:.3f} new PF={pf_new:.3f}({len(new)}건)"
        )

    write_report(results, CUT)
    print()
    print(f"리포트 저장: {REPORT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
