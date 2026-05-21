"""scripts/analyze_pullback.py — 눌림목 그리드 신뢰성 검증.

1. 수익 분포 분석 (상위 3조합)
2. 슬리피지 민감도 (5레벨 × 3조합)
3. sl > pullback_depth 조합 성과 재확인

사용:
    python -u scripts/analyze_pullback.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")

# ---------------------------------------------------------------------------
# 날짜 구간 (OLD 기준 — 보고서와 동일)
# ---------------------------------------------------------------------------

OLD_START = "2025-04-01"
OLD_END   = "2026-04-10"

# ---------------------------------------------------------------------------
# 분석 대상 조합
# ---------------------------------------------------------------------------

TOP3 = [
    {"label": "surge10/pb3/sl3/tp1/11:00",
     "surge_pct": 0.10, "pullback_depth": 0.030, "sl_from_high_pct": 0.03,
     "tp_above_high_pct": 0.01, "entry_end": "11:00"},
    {"label": "surge7/pb3/sl3/tp0/11:00",
     "surge_pct": 0.07, "pullback_depth": 0.030, "sl_from_high_pct": 0.03,
     "tp_above_high_pct": 0.00, "entry_end": "11:00"},
    {"label": "surge5/pb3/sl3/tp0/11:00",
     "surge_pct": 0.05, "pullback_depth": 0.030, "sl_from_high_pct": 0.03,
     "tp_above_high_pct": 0.00, "entry_end": "11:00"},
]

# sl > pullback_depth 의미 있는 조합
SL_GT_PB = [
    {"label": "surge10/pb3/sl5/tp1/11:00",
     "surge_pct": 0.10, "pullback_depth": 0.030, "sl_from_high_pct": 0.05,
     "tp_above_high_pct": 0.01, "entry_end": "11:00"},
    {"label": "surge10/pb3/sl7/tp1/11:00",
     "surge_pct": 0.10, "pullback_depth": 0.030, "sl_from_high_pct": 0.07,
     "tp_above_high_pct": 0.01, "entry_end": "11:00"},
    {"label": "surge7/pb3/sl5/tp0/11:00",
     "surge_pct": 0.07, "pullback_depth": 0.030, "sl_from_high_pct": 0.05,
     "tp_above_high_pct": 0.00, "entry_end": "11:00"},
    {"label": "surge7/pb3/sl7/tp0/11:00",
     "surge_pct": 0.07, "pullback_depth": 0.030, "sl_from_high_pct": 0.07,
     "tp_above_high_pct": 0.00, "entry_end": "11:00"},
    {"label": "surge10/pb2/sl5/tp1/11:00",
     "surge_pct": 0.10, "pullback_depth": 0.020, "sl_from_high_pct": 0.05,
     "tp_above_high_pct": 0.01, "entry_end": "11:00"},
    {"label": "surge10/pb2/sl7/tp2/11:00",
     "surge_pct": 0.10, "pullback_depth": 0.020, "sl_from_high_pct": 0.07,
     "tp_above_high_pct": 0.02, "entry_end": "11:00"},
]

SLIPPAGE_LEVELS = [0.000, 0.0005, 0.001, 0.002, 0.003]


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _pb_config(params: dict, base_config, bt_config) -> tuple:
    cfg = dataclasses.replace(
        base_config,
        pb_enabled=True,
        pb_surge_pct=params["surge_pct"],
        pb_pullback_depth=params["pullback_depth"],
        pb_sl_from_high_pct=params["sl_from_high_pct"],
        pb_tp_above_high_pct=params["tp_above_high_pct"],
        pb_entry_start="09:30",
        pb_entry_end=params["entry_end"],
        pb_min_above_close_pct=0.01,
        pb_min_volume=50000,
        market_filter_enabled=False,
        intraday_market_filter_enabled=False,
        blacklist_enabled=False,
        consecutive_loss_rest_enabled=False,
        volatility_sizing_enabled=False,
        max_trades_per_day=1,
        cooldown_minutes=999,
        adx_enabled=False,
    )
    return cfg, bt_config


async def _run_combo(params: dict, base_config, bt_config, candles_cache, ticker_to_market, market_map):
    from backtest.backtester_fast import PullbackFastBacktester
    from strategy.pullback_strategy import PullbackStrategy

    cfg, _ = _pb_config(params, base_config, bt_config)
    all_trades = []
    for tk, df in candles_cache.items():
        market = ticker_to_market.get(tk, "unknown")
        bt = PullbackFastBacktester(
            db=None, config=cfg, backtest_config=bt_config,
            ticker_market=market, market_strong_by_date=market_map,
        )
        strat = PullbackStrategy(cfg)
        result = await bt.run_multi_day_cached(tk, df, strat)
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)
    return all_trades


async def _run_combo_slip(params: dict, base_config, slip: float, candles_cache, ticker_to_market, market_map):
    from backtest.backtester_fast import PullbackFastBacktester
    from backtest.backtester import Backtester
    from config.settings import BacktestConfig
    from strategy.pullback_strategy import PullbackStrategy
    from core.cost_model import TradeCosts

    cfg, _ = _pb_config(params, base_config, None)

    all_trades = []
    for tk, df in candles_cache.items():
        market = ticker_to_market.get(tk, "unknown")
        bt_local = PullbackFastBacktester.__new__(PullbackFastBacktester)
        # 직접 초기화 (슬리피지 override)
        from backtest.backtester import Backtester as _BT
        _BT.__init__(bt_local, db=None, config=cfg,
                     backtest_config=BacktestConfig(slippage=slip),
                     ticker_market=market, market_strong_by_date=market_map)
        strat = PullbackStrategy(cfg)
        result = await bt_local.run_multi_day_cached(tk, df, strat)
        for t in result.get("trades", []):
            t["ticker"] = tk
            all_trades.append(t)
    return all_trades


def _stats(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {"pf": 0, "pnl": 0, "trades": 0, "win_rate": 0,
                "avg_win": 0, "avg_loss": 0, "median_pnl": 0,
                "pct_tiny": 0, "avg_hold": 0, "exit_counts": {}}
    wins  = [t["pnl"] for t in trades if t["pnl"] > 0]
    loss  = [t["pnl"] for t in trades if t["pnl"] <= 0]
    gp    = sum(wins) if wins else 0
    gl    = abs(sum(loss)) if loss else 0
    pnl   = sum(t["pnl"] for t in trades)
    exits = Counter(t.get("exit_reason", "?") for t in trades)
    pnls  = sorted(t["pnl"] for t in trades)
    median_pnl = float(np.median(pnls)) if pnls else 0.0
    tiny  = sum(1 for t in trades if abs(t["pnl"]) < 100)
    hold_mins = []
    for t in trades:
        e, x = t.get("entry_ts"), t.get("exit_ts")
        if e and x:
            hold_mins.append((x - e).total_seconds() / 60.0)
    avg_hold = sum(hold_mins) / len(hold_mins) if hold_mins else 0.0
    return {
        "pf":        round(gp / gl, 3) if gl > 0 else float("inf"),
        "pnl":       int(pnl),
        "trades":    n,
        "win_rate":  round(len(wins) / n, 3),
        "avg_win":   int(gp / len(wins)) if wins else 0,
        "avg_loss":  int(-gl / len(loss)) if loss else 0,
        "median_pnl": int(median_pnl),
        "pct_tiny":  round(tiny / n * 100, 1),
        "avg_hold":  round(avg_hold, 1),
        "exit_counts": dict(exits),
    }


def _hist(trades: list[dict], *, bins: int = 20, width: int = 50) -> str:
    """텍스트 히스토그램 — 건당 PnL (원)."""
    pnls = [t["pnl"] for t in trades]
    if not pnls:
        return "  (거래 없음)"
    lo, hi = min(pnls), max(pnls)
    if lo == hi:
        return f"  모든 거래 PnL = {lo:,}원"
    step = (hi - lo) / bins
    counts = [0] * bins
    for p in pnls:
        idx = min(int((p - lo) / step), bins - 1)
        counts[idx] += 1
    lines = []
    for i, c in enumerate(counts):
        lo_b = lo + i * step
        hi_b = lo_b + step
        bar = "#" * int(c / max(counts) * width)
        lines.append(f"  {lo_b:>+10,.0f}~{hi_b:>+10,.0f} |{bar} {c}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 메인 분석
# ---------------------------------------------------------------------------

async def main() -> None:
    from utils.grid_runner import load_candle_cache

    print("캔들 캐시 로드 중...", flush=True)
    cache = await load_candle_cache(OLD_START, OLD_END)
    print(f"  {len(cache.candles)}종목 OLD 구간 로드 완료\n", flush=True)

    base_config   = cache.base_config
    bt_config     = cache.bt_config
    candles_cache = cache.candles
    ticker_to_market = cache.ticker_to_market
    market_map    = cache.market_map

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 1. 수익 분포 분석
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("=" * 72)
    print("§1. 수익 분포 분석 (상위 3조합)")
    print("=" * 72)

    top3_trades: dict[str, list] = {}
    for p in TOP3:
        trades = await _run_combo(p, base_config, bt_config, candles_cache, ticker_to_market, market_map)
        top3_trades[p["label"]] = trades
        s = _stats(trades)
        print(f"\n  [{p['label']}]")
        print(f"  PF={s['pf']:.3f}  PnL={s['pnl']:+,}  거래={s['trades']}  승률={s['win_rate']:.1%}")
        print(f"  평균수익={s['avg_win']:+,}원  평균손실={s['avg_loss']:+,}원  중앙값={s['median_pnl']:+,}원")
        print(f"  |PnL|<100원 비율={s['pct_tiny']:.1f}%  평균보유={s['avg_hold']:.1f}분")
        print(f"  청산분포: {s['exit_counts']}")
        print(f"\n  PnL 히스토그램 (건당, 원):")
        print(_hist(trades, bins=20, width=40))

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 2. 슬리피지 민감도
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("\n" + "=" * 72)
    print("§2. 슬리피지 민감도 (5레벨 × 3조합)")
    print("=" * 72)

    slip_labels = [f"{s*100:.2f}%" for s in SLIPPAGE_LEVELS]
    # 헤더
    col = 22
    print(f"\n  {'조합':<30} | " + " | ".join(f"{l:>7}" for l in slip_labels))
    print("  " + "-" * (30 + 3 + len(slip_labels) * 10))

    for p in TOP3:
        row_pf = []
        row_pnl = []
        for slip in SLIPPAGE_LEVELS:
            trades = await _run_combo_slip(p, base_config, slip, candles_cache, ticker_to_market, market_map)
            s = _stats(trades)
            row_pf.append(s["pf"])
            row_pnl.append(s["pnl"])
        pf_str   = " | ".join(f"{v:>7.3f}" for v in row_pf)
        pnl_str  = " | ".join(f"{v:>+7,}"[:7] for v in row_pnl)
        print(f"  {p['label']:<30} | {pf_str}   (PF)")
        print(f"  {'':30}   {pnl_str}   (PnL)")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 3. sl > pullback_depth 조합
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("\n" + "=" * 72)
    print("§3. sl_from_high > pullback_depth 조합 성과")
    print("    (진입가 < 손절가 → 정상적인 리스크 구조)")
    print("=" * 72)

    hdr = f"  {'조합':<32} {'PF':>6} {'PnL':>10} {'거래#':>6} {'승률':>6} {'평균수익':>8} {'평균손실':>8} {'보유(분)':>8} {'청산분포'}"
    print(hdr)
    print("  " + "-" * 100)

    for p in SL_GT_PB:
        trades = await _run_combo(p, base_config, bt_config, candles_cache, ticker_to_market, market_map)
        s = _stats(trades)
        gap = p["sl_from_high_pct"] - p["pullback_depth"]
        print(
            f"  {p['label']:<32} "
            f"{s['pf']:>6.3f} {s['pnl']:>+10,} "
            f"{s['trades']:>6} {s['win_rate']:>6.1%} "
            f"{s['avg_win']:>+8,} {s['avg_loss']:>+8,} "
            f"{s['avg_hold']:>8.1f}   "
            f"tp={s['exit_counts'].get('tp_exit',0)} sl={s['exit_counts'].get('stop_loss',0)} fc={s['exit_counts'].get('forced_close',0)}"
            f"  [sl-pb={gap*100:.1f}%]"
        )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 4. 종합 판단
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("\n" + "=" * 72)
    print("§4. 종합 판단")
    print("=" * 72)
    print("""
  [sl = pullback_depth 조합 구조]
  - 진입 조건: close ≤ day_high × (1 - pb_depth)
  - 손절가   : day_high × (1 - sl_from_high)
  - pb_depth = sl_from_high → 진입가 ≤ 손절가 (손절이 항상 이익)
  - 손절 발동 = 다음 캔들 LOW가 손절가 아래 진입 → 손절가(= 진입가 수준)에서 매도
  - 실전 의미 : 눌림 이후 미반등 시 손절가가 진입가 위에 있으므로
                다음 캔들의 변동만으로도 "이익 확정" → PF 인위적 과대
  - 슬리피지 0.1% 이상에서 PF 급락 여부가 핵심 지표

  [sl > pullback_depth 조합]
  - 진입가 > 손절가 → 실제 손절 발동 시 손실 발생 (정상 구조)
  - 이 조합군의 PF/PnL이 실전 활용 가능성의 실제 지표
""")


if __name__ == "__main__":
    asyncio.run(main())
