"""scripts/backtest_single.py — 단일 설정 백테스트 (시장필터/블랙리스트/휴식 전부 적용).

run_multi_day_cached 경로 + ProcessPool 병렬. 실시간 엔진과 동일한
필터 로직으로 측정해 실제 운영 예상치에 가장 근접한 결과를 낸다.

사용:
    python scripts/backtest_single.py                       # 기본 기간
    python scripts/backtest_single.py --start 2025-04-01 --end 2026-04-10
"""

import argparse
import asyncio
import os
import pickle
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import yaml
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.backtester import Backtester, build_market_strong_by_date
from config.settings import AppConfig, BacktestConfig
from data.db_manager import DbManager

logger.remove()


def simulate_one(args: tuple) -> dict:
    """워커: 단일 종목 multi-day 백테스트 (시장필터 포함)."""
    (
        ticker, ticker_market, candles_pickle,
        trading_config, backtest_config, market_map,
    ) = args

    import asyncio as _asyncio
    from backtest.backtester import Backtester as _Backtester
    from strategy.momentum_strategy import MomentumStrategy

    candles = pickle.loads(candles_pickle)
    strategy = MomentumStrategy(trading_config)
    bt = _Backtester(
        db=None,
        config=trading_config,
        backtest_config=backtest_config,
        ticker_market=ticker_market,
        market_strong_by_date=market_map,
    )
    return _asyncio.run(bt.run_multi_day_cached(ticker, candles, strategy))


async def main(start: str, end: str) -> int:
    app_config = AppConfig.from_yaml()
    base_config = app_config.trading

    bt_cfg_raw = yaml.safe_load(
        open("config.yaml", encoding="utf-8")
    ).get("backtest", {})
    backtest_config = BacktestConfig(
        commission=bt_cfg_raw.get("commission", 0.00015),
        tax=bt_cfg_raw.get("tax", 0.0018),
        slippage=bt_cfg_raw.get("slippage", 0.0003),
    )

    uni = yaml.safe_load(open("config/universe.yaml", encoding="utf-8"))
    stocks = uni.get("stocks", [])
    ticker_to_market = {s["ticker"]: s.get("market", "unknown") for s in stocks}

    db = DbManager(app_config.db_path)
    await db.init()
    bt_loader = Backtester(db=db, config=base_config, backtest_config=backtest_config)

    print("=" * 64)
    print(" 단일 백테스트 (run_multi_day_cached, 시장필터 적용)")
    print("=" * 64)
    print(f"  Universe  : {len(stocks)}종목")
    print(f"  기간      : {start} ~ {end}")
    print(f"  필터      : ADX={base_config.adx_enabled}({base_config.adx_min}) "
          f"market={base_config.market_filter_enabled} "
          f"buy_end={base_config.buy_time_end if base_config.buy_time_limit_enabled else 'off'}")
    stop_status = f"fixed {base_config.momentum_stop_loss_pct*100:.0f}%" if not base_config.atr_stop_enabled else f"ATR x{base_config.atr_stop_multiplier}"
    tp_status = "off" if not getattr(base_config, "atr_tp_enabled", False) else f"{getattr(base_config, 'atr_tp_multiplier', '?')}"
    print(f"  청산      : stop={stop_status} tp={tp_status} trail=x{base_config.atr_trail_multiplier}")
    print(f"  비용      : 수수료 {backtest_config.commission*100:.3f}% / "
          f"세금 {backtest_config.tax*100:.2f}% / "
          f"슬리피지 {backtest_config.slippage*100:.3f}%")
    print()

    print("[LOAD] 캔들 로딩...")
    candles_cache: dict[str, bytes] = {}
    for s in stocks:
        tk = s["ticker"]
        candles = await bt_loader.load_candles(tk, start, f"{end} 23:59:59")
        if not candles.empty:
            candles_cache[tk] = pickle.dumps(candles)
    print(f"  로드 {len(candles_cache)}/{len(stocks)}")
    await db.close()

    market_map = build_market_strong_by_date(
        app_config.db_path, ma_length=base_config.market_ma_length
    )

    workers = max(2, (os.cpu_count() or 2) - 1)
    print(f"[RUN] 워커 {workers}\n")

    tasks = [
        (
            tk, ticker_to_market.get(tk, "unknown"),
            candles_cache[tk], base_config, backtest_config, market_map,
        )
        for tk in candles_cache
    ]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        kpis = list(executor.map(simulate_one, tasks))

    # 집계
    total_trades = sum(k["total_trades"] for k in kpis if k)
    total_pnl = sum(k["total_pnl"] for k in kpis if k)
    gp = gl = 0.0
    exit_counter: Counter = Counter()
    for k in kpis:
        if not k:
            continue
        for t in k.get("trades", []):
            p = t.get("pnl", 0.0)
            if p > 0:
                gp += p
            elif p < 0:
                gl += abs(p)
            exit_counter[t.get("exit_reason", "unknown")] += 1
    pf = (gp / gl) if gl > 0 else float("inf")
    pf_above_1 = sum(
        1 for k in kpis
        if k and k["total_trades"] > 0 and k["profit_factor"] > 1.0
    )
    per_trade = total_pnl / total_trades if total_trades else 0.0

    print("=" * 64)
    print(" 결과")
    print("=" * 64)
    pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
    print(f"  거래수     : {total_trades}")
    print(f"  PF         : {pf_str}")
    print(f"  총 PnL     : {total_pnl:+,.0f}")
    print(f"  거래당 PnL : {per_trade:+,.0f}")
    print(f"  PF>1 종목  : {pf_above_1}/{len(candles_cache)}")
    print()
    print(" 청산 분포")
    print("-" * 64)
    total = sum(exit_counter.values()) or 1
    for reason, cnt in exit_counter.most_common():
        pct = cnt / total * 100
        print(f"  {reason:<18} {cnt:>4} ({pct:.1f}%)")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-04-01")
    parser.add_argument("--end", default="2026-04-10")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.start, args.end)))
