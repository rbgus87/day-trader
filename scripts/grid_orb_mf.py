"""scripts/grid_orb_mf.py — ORB + 시장 필터 MA 기간 그리드.

기존 ORB 최선 조합(sl=1.5/tp=3.0/dl=09:30/buf=0.0/rvol=1.5)에서
market_filter: [MA5, MA20, off] × OLD/NEW = 6 백테스트.

사용:
    python scripts/grid_orb_mf.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import yaml
from loguru import logger

logger.remove()

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd

from backtest.backtester import build_market_strong_by_date
from backtest.backtester_fast import ORBFastBacktester
from config.settings import BacktestConfig, TradingConfig
from data.db_manager import DbManager
from strategy.orb_strategy import ORBStrategy

OLD_START = "2025-04-01"
OLD_END   = "2026-04-10"
NEW_START = "2026-04-11"
NEW_END   = "2026-05-12"

# (label, market_filter_enabled, ma_length)
SCENARIOS = [
    ("MA5",  True,  5),
    ("MA20", True,  20),
    ("off",  False, 5),
]


def _orb_base_config(mf_enabled: bool, ma_length: int) -> TradingConfig:
    return TradingConfig(
        market_filter_enabled=mf_enabled,
        market_ma_length=ma_length,
        intraday_market_filter_enabled=False,
        blacklist_enabled=False,
        consecutive_loss_rest_enabled=False,
        adx_enabled=False,
        orb_enabled=True,
        orb_range_minutes=5,
        orb_sl_ratio=1.5,
        orb_tp_ratio=3.0,
        orb_entry_deadline="09:30",
        orb_breakout_buffer=0.0,
        orb_use_volume_filter=True,
        orb_rvol_min=1.5,
        orb_min_range_pct=0.005,
        orb_max_range_pct=0.10,
        max_trades_per_day=1,
        cooldown_minutes=999,
    )


async def load_candles(db: DbManager, tickers: list[str], start: str, end: str, ticker_to_market: dict) -> dict:
    result: dict[str, pd.DataFrame] = {}
    loader_cfg = TradingConfig()
    from backtest.backtester import Backtester
    loader = Backtester(db=db, config=loader_cfg)
    for ticker in tickers:
        df = await loader.load_candles(ticker, start, f"{end} 23:59:59")
        if not df.empty:
            result[ticker] = df
    return result


async def run_scenario(
    candle_map: dict[str, pd.DataFrame],
    ticker_to_market: dict,
    mf_enabled: bool,
    ma_length: int,
    db_path: str,
) -> list[dict]:
    cfg = _orb_base_config(mf_enabled, ma_length)
    bt_cfg = BacktestConfig()

    market_map: dict = {}
    if mf_enabled:
        market_map = build_market_strong_by_date(db_path, ma_length=ma_length)

    all_trades: list[dict] = []
    for ticker, df in candle_map.items():
        market = ticker_to_market.get(ticker, "unknown")
        bt = ORBFastBacktester(
            db=None, config=cfg, backtest_config=bt_cfg,
            ticker_market=market, market_strong_by_date=market_map,
        )
        strategy = ORBStrategy(cfg)
        result = await bt.run_multi_day_cached(ticker, df, strategy)
        for t in result.get("trades", []):
            t["ticker"] = ticker
            t["ticker_market"] = market
        all_trades.extend(result.get("trades", []))
    return all_trades


def compute_stats(trades: list[dict]) -> dict:
    if not trades:
        return dict(n=0, pf=0.0, pnl=0, win_rate=0.0)
    pnls = [t["pnl"] for t in trades]
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p < 0))
    pf = gp / gl if gl > 0 else float("inf")
    wins = sum(1 for p in pnls if p > 0)
    return dict(n=len(trades), pf=round(pf, 3), pnl=int(sum(pnls)), win_rate=round(wins / len(trades) * 100, 1))


async def main() -> None:
    uni = yaml.safe_load(open("config/universe_backtest.yaml", encoding="utf-8")) or {}
    stocks = uni.get("stocks", [])
    tickers = [s["ticker"] for s in stocks]
    ticker_to_market = {s["ticker"]: s.get("market", "unknown") for s in stocks}
    db_path = "daytrader.db"

    db = DbManager(db_path)
    await db.init()

    print("분봉 로드 중 (OLD)...", flush=True)
    old_map = await load_candles(db, tickers, OLD_START, OLD_END, ticker_to_market)
    print(f"  {len(old_map)}종목")

    print("분봉 로드 중 (NEW)...", flush=True)
    new_map = await load_candles(db, tickers, NEW_START, NEW_END, ticker_to_market)
    print(f"  {len(new_map)}종목")

    await db.close()

    # ORBFastBacktester는 market_strong_by_date를 run_multi_day_cached에서 직접 쓰지 않음.
    # 시장 필터는 부모 Backtester.run_multi_day_cached에서 처리됨.
    # ORB용으로 부모 경로를 쓰려면 ORBStrategy를 momentum 파이프라인으로 실행해야 하므로
    # 여기서는 ORBFastBacktester에 market_filter 패치를 직접 적용한다.
    # → 아래 _run_with_mf_patch() 사용

    print("\n" + "=" * 68)
    print("ORB + 시장 필터 그리드 (sl=1.5/tp=3.0/dl=09:30/buf=0.0/rvol=1.5)")
    print(f"OLD: {OLD_START}~{OLD_END}  /  NEW: {NEW_START}~{NEW_END}")
    print("=" * 68)

    off_old_n: int | None = None
    off_new_n: int | None = None
    rows = []

    for label, mf_enabled, ma_len in SCENARIOS:
        print(f"[RUN] {label} ...", flush=True)
        old_t = await _run_with_mf(old_map, ticker_to_market, mf_enabled, ma_len, db_path)
        new_t = await _run_with_mf(new_map, ticker_to_market, mf_enabled, ma_len, db_path)
        os_ = compute_stats(old_t)
        ns_ = compute_stats(new_t)
        if label == "off":
            off_old_n = os_["n"]
            off_new_n = ns_["n"]
        rows.append((label, os_, ns_))

    print()
    print(f"{'시나리오':<8} | {'OLD N':>6} {'차단':>5} {'OLD PF':>7} {'OLD PnL':>10} | {'NEW N':>6} {'차단':>5} {'NEW PF':>7} {'NEW PnL':>10}")
    print("-" * 70)
    for label, os_, ns_ in rows:
        ob = (off_old_n - os_["n"]) if (off_old_n is not None and label != "off") else 0
        nb = (off_new_n - ns_["n"]) if (off_new_n is not None and label != "off") else 0
        pf_old_mark = " *" if os_["pf"] > 1.0 else "  "
        pf_new_mark = " *" if ns_["pf"] > 1.0 else "  "
        print(
            f"  {label:<6}  | {os_['n']:>6} {ob:>5} {os_['pf']:>6.3f}{pf_old_mark} {os_['pnl']:>+10,} | "
            f"{ns_['n']:>6} {nb:>5} {ns_['pf']:>6.3f}{pf_new_mark} {ns_['pnl']:>+10,}"
        )
    print()
    print("* PF > 1.0")

    # 결론 판단
    ma5_row = next((r for r in rows if r[0] == "MA5"), None)
    off_row = next((r for r in rows if r[0] == "off"), None)
    if ma5_row:
        os_, ns_ = ma5_row[1], ma5_row[2]
        if os_["pf"] > 1.0 and ns_["pf"] > 1.0:
            print("\n[결론] MA5 + ORB: OLD/NEW 모두 PF > 1.0 -> market_filter_enabled: true (MA5)")
        elif off_row:
            oos, ons = off_row[1], off_row[2]
            if oos["pf"] > os_["pf"] and ons["pf"] > ns_["pf"]:
                print("\n[결론] off가 양쪽에서 MA5보다 우수 -> market_filter_enabled: false")
            else:
                print("\n[결론] MA5 OLD PF <= 1.0 또는 NEW PF <= 1.0 -> 추가 검토 필요")


async def _run_with_mf(
    candle_map: dict,
    ticker_to_market: dict,
    mf_enabled: bool,
    ma_len: int,
    db_path: str,
) -> list[dict]:
    """ORBFastBacktester에 시장 필터를 패치해서 실행."""
    cfg = _orb_base_config(mf_enabled, ma_len)
    bt_cfg = BacktestConfig()

    market_map: dict = {}
    if mf_enabled:
        market_map = build_market_strong_by_date(db_path, ma_length=ma_len)

    all_trades: list[dict] = []
    for ticker, df in candle_map.items():
        market = ticker_to_market.get(ticker, "unknown")
        trades = await _run_orb_with_mf(ticker, df, market, cfg, bt_cfg, market_map)
        all_trades.extend(trades)
    return all_trades


async def _run_orb_with_mf(
    ticker: str,
    all_candles: pd.DataFrame,
    ticker_market: str,
    cfg: TradingConfig,
    bt_cfg: BacktestConfig,
    market_map: dict,
) -> list[dict]:
    """ORBFastBacktester.run_multi_day_cached와 동일하되 market_filter 체크 삽입."""
    from backtest.backtester_fast import ORBFastBacktester, _parse_hhmm
    from core.cost_model import TradeCosts, apply_buy_costs, apply_sell_costs

    if all_candles.empty:
        return []

    bt = ORBFastBacktester(
        db=None, config=cfg, backtest_config=bt_cfg,
        ticker_market=ticker_market, market_strong_by_date=market_map,
    )

    ts_pd = pd.DatetimeIndex(all_candles["ts"])
    all_closes  = all_candles["close"].values.astype(np.float64)
    all_highs   = all_candles["high"].values.astype(np.float64)
    all_lows    = all_candles["low"].values.astype(np.float64)
    all_volumes = all_candles["volume"].values.astype(np.float64)
    all_minutes = (ts_pd.hour * 60 + ts_pd.minute).values.astype(np.int32)

    day_ord     = (ts_pd.asi8 // (86_400 * 10 ** 9)).astype(np.int64)
    day_starts  = np.where(np.diff(day_ord, prepend=day_ord[0] - 1) != 0)[0]
    day_ends    = np.append(day_starts[1:], len(day_ord))
    n_days      = len(day_starts)

    costs           = bt._costs
    range_minutes   = int(getattr(cfg, "orb_range_minutes", 5))
    min_range_pct   = float(getattr(cfg, "orb_min_range_pct", 0.005))
    max_range_pct   = float(getattr(cfg, "orb_max_range_pct", 0.10))
    breakout_buffer = float(getattr(cfg, "orb_breakout_buffer", 0.0))
    sl_ratio        = float(getattr(cfg, "orb_sl_ratio", 1.5))
    tp_ratio        = float(getattr(cfg, "orb_tp_ratio", 3.0))
    use_vol_filter  = bool(getattr(cfg, "orb_use_volume_filter", True))
    rvol_min        = float(getattr(cfg, "orb_rvol_min", 1.5))
    entry_deadline  = _parse_hhmm(str(getattr(cfg, "orb_entry_deadline", "09:30")), 570)
    signal_block    = _parse_hhmm(str(getattr(cfg, "signal_block_until", "09:05")), 545)
    max_trades_day  = int(getattr(cfg, "max_trades_per_day", 1))
    cooldown_cfg    = int(getattr(cfg, "cooldown_minutes", 999))
    range_start_min = 540
    range_end_min   = range_start_min + range_minutes - 1

    mf_on = bool(getattr(cfg, "market_filter_enabled", False))

    all_trades: list[dict] = []
    prev_close  = 0.0
    prev_volume = 0.0

    for di in range(n_days):
        s = int(day_starts[di])
        e = int(day_ends[di])

        closes  = all_closes[s:e]
        highs   = all_highs[s:e]
        lows    = all_lows[s:e]
        volumes = all_volumes[s:e]
        mins    = all_minutes[s:e]
        n       = e - s

        # 시장 필터 체크
        if mf_on and ticker_market in ("kospi", "kosdaq"):
            date_key = ts_pd[s].strftime("%Y%m%d")
            strong = market_map.get(date_key, {}).get(ticker_market, True)
            if not strong:
                if n > 0:
                    prev_close  = float(closes[-1])
                    prev_volume = float(np.sum(volumes))
                continue

        range_mask = (mins >= range_start_min) & (mins <= range_end_min)
        if not np.any(range_mask):
            if n > 0:
                prev_close  = float(closes[-1])
                prev_volume = float(np.sum(volumes))
            continue

        range_high = float(np.max(highs[range_mask]))
        range_low  = float(np.min(lows[range_mask]))
        range_size = range_high - range_low

        ref_price = prev_close if prev_close > 0 else float(closes[0])
        if ref_price <= 0:
            ref_price = range_high

        range_pct = range_size / ref_price if ref_price > 0 else 0.0
        if range_pct < min_range_pct or range_pct > max_range_pct:
            prev_close  = float(closes[-1])
            prev_volume = float(np.sum(volumes))
            continue

        breakout_threshold = range_high + range_size * breakout_buffer
        cum_vols = np.cumsum(volumes)

        position: dict | None = None
        trade_count    = 0
        last_exit_min: int | None = None

        for i in range(n):
            close_i = closes[i]
            high_i  = highs[i]
            low_i   = lows[i]
            min_i   = int(mins[i])

            if position is None:
                cooldown_ok = (
                    last_exit_min is None
                    or cooldown_cfg <= 0
                    or (min_i - last_exit_min) >= cooldown_cfg
                )
                if (
                    trade_count < max_trades_day
                    and cooldown_ok
                    and signal_block <= min_i <= entry_deadline
                    and close_i > breakout_threshold
                    and (
                        not use_vol_filter
                        or prev_volume <= 0
                        or cum_vols[i] >= prev_volume * rvol_min
                    )
                ):
                    trade_count += 1
                    entry_price, net_entry = apply_buy_costs(close_i, costs)
                    sl = entry_price - range_size * sl_ratio
                    stop_loss = max(sl, range_low * 0.99)
                    tp_price  = entry_price + range_size * tp_ratio
                    position = {
                        "entry_ts":    ts_pd[s + i].to_pydatetime(),
                        "entry_price": entry_price,
                        "net_entry":   net_entry,
                        "stop_loss":   stop_loss,
                        "tp_price":    tp_price,
                    }
            else:
                if high_i >= position["tp_price"]:
                    ep, net_exit = apply_sell_costs(position["tp_price"], costs)
                    all_trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_pd[s + i].to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep,
                        "pnl":         net_exit - position["net_entry"],
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "tp_exit",
                        "ticker":      ticker,
                    })
                    position = None
                    last_exit_min = min_i
                elif low_i <= position["stop_loss"]:
                    ep, net_exit = apply_sell_costs(position["stop_loss"], costs)
                    all_trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_pd[s + i].to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep,
                        "pnl":         net_exit - position["net_entry"],
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "stop_loss",
                        "ticker":      ticker,
                    })
                    position = None
                    last_exit_min = min_i
                elif i == n - 1:
                    ep, net_exit = apply_sell_costs(close_i, costs)
                    all_trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_pd[s + i].to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep,
                        "pnl":         net_exit - position["net_entry"],
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "forced_close",
                        "ticker":      ticker,
                    })
                    position = None
                    last_exit_min = min_i

        prev_close  = float(closes[-1])
        prev_volume = float(np.sum(volumes))

    return all_trades


if __name__ == "__main__":
    asyncio.run(main())
