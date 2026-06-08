"""scripts/grid_split_entry.py — 모멘텀 분할 진입 백테스트 그리드.

설계:
  baseline (first_ratio=1.0): BREAKOUT + VOLUME 모두 충족 → 100% 진입 (현행)
  split (first_ratio<1.0):
    1차: BREAKOUT 충족 즉시 → first_ratio 진입 (거래량 무관)
    2차: VOLUME 충족 시 → (1-first_ratio) 추가 매수
    2차 미발생 시: 1차만 유지 (기존 SL/Trail 적용)

그리드: first_ratio = [0.3, 0.5, 0.7, 1.0(baseline)]

추가 측정 지표:
  - 1차만(vol 미충족) 비율
  - 1차 이후 SL 청산 비율 (허수돌파 잡힌 비율)
  - 진입 지연 단축 (BREAKOUT → 진입, median 분)

구간:
  OLD: 2025-04-01 ~ 2026-04-10
  NEW: 2026-04-11 ~ 2026-05-19

사용:
    python -u scripts/grid_split_entry.py
    python -u scripts/grid_split_entry.py --verify   # baseline만 검증
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import pickle
import sys
import time as _time
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")

from utils.grid_runner import GridCache, load_candle_cache

# ---------------------------------------------------------------------------
# 날짜 구간
# ---------------------------------------------------------------------------

OLD_START = "2025-04-01"
OLD_END   = "2026-04-10"
NEW_START = "2026-04-11"
NEW_END   = "2026-05-19"

BASELINE = {
    "tag": "baseline",
    "pf": 4.743, "pnl": 285_467, "trades": 228,
    "win_rate": 0.557, "fc_pct": 40.4,
}

VERIFY_TOLERANCE = {"pf": 0.10, "pnl": 10000}

# ---------------------------------------------------------------------------
# 헬퍼 함수 (backtester_fast 복제)
# ---------------------------------------------------------------------------

def _parse_hhmm(s: str, default: int) -> int:
    try:
        h, m = map(int, str(s).split(":"))
        return h * 60 + m
    except Exception:
        return default


def _get_decay(min_i: int, phases: tuple, enabled: bool) -> float:
    if not enabled or not phases:
        return 1.0
    mult = 1.0
    for ph in phases:
        ph_min = _parse_hhmm(str(ph.until), 9999)
        if min_i < ph_min:
            mult = float(ph.multiplier)
            break
        mult = float(ph.multiplier)
    return mult


def _wilder_adx_numpy(high, low, close, length=14):
    from backtest.backtester_fast import _wilder_adx_numpy as _adx
    return _adx(high, low, close, length)


# ---------------------------------------------------------------------------
# 핵심 시뮬레이션 — 종목 1개, 전 기간
# ---------------------------------------------------------------------------

def _run_split_entry(
    df_full,
    ticker: str,
    market: str,
    market_map: dict,
    cfg,
    costs,
    first_ratio: float,
) -> list[dict]:
    """종목 하나의 전 기간 split-entry 시뮬레이션.

    first_ratio=1.0 → baseline (breakout+volume → 100%)
    first_ratio<1.0 → 1차:breakout 즉시, 2차:volume 충족 시
    """
    from core.cost_model import apply_buy_costs, apply_sell_costs
    from backtest.backtester import build_intraday_blocked_by_date

    # pandas → numpy 변환
    import pandas as pd

    df_full = df_full.sort_values("ts").reset_index(drop=True)
    ts_pd   = pd.to_datetime(df_full["ts"])
    closes  = df_full["close"].to_numpy(dtype=np.float64)
    highs   = df_full["high"].to_numpy(dtype=np.float64)
    lows    = df_full["low"].to_numpy(dtype=np.float64)
    volumes = df_full["volume"].to_numpy(dtype=np.float64)
    opens   = df_full["open"].to_numpy(dtype=np.float64) if "open" in df_full.columns else closes.copy()
    minutes = ts_pd.dt.hour.to_numpy(dtype=np.int32) * 60 + ts_pd.dt.minute.to_numpy(dtype=np.int32)

    # 날짜별 인덱스
    dates = ts_pd.dt.date.to_numpy()
    unique_dates = sorted(set(dates))
    day_starts = []
    day_ends   = []
    prev_date  = None
    for i, d in enumerate(dates):
        if d != prev_date:
            if prev_date is not None:
                day_ends.append(i)
            day_starts.append(i)
            prev_date = d
    day_ends.append(len(dates))
    n_days = len(day_starts)

    # 설정 캐시
    min_bp         = float(getattr(cfg, "min_breakout_pct", 0.0))
    vol_ratio      = float(cfg.momentum_volume_ratio)
    adx_min        = float(cfg.adx_min)
    adx_len        = int(cfg.adx_length)
    min_bars_adx   = adx_len + 20
    trail_mult     = float(cfg.atr_trail_multiplier)
    trail_min_pct  = float(cfg.atr_trail_min_pct)
    trail_max_pct  = float(cfg.atr_trail_max_pct)
    td_enabled     = bool(cfg.time_decay_trailing_enabled)
    td_phases      = cfg.time_decay_phases
    td_floor       = float(cfg.time_decay_min_pct_floor)
    be_enabled     = bool(cfg.breakeven_enabled)
    be_trigger_pct = float(cfg.breakeven_trigger_pct)
    be_offset_pct  = float(cfg.breakeven_offset_pct)
    be3_atr_ratio  = float(getattr(cfg, "be3_atr_ratio", 0.4))
    be3_stop_atr   = float(getattr(cfg, "be3_stop_atr_ratio", 0.15))
    fade_enabled   = bool(cfg.momentum_fade_exit_enabled)
    fade_lookback  = int(cfg.momentum_fade_lookback)
    fade_threshold = float(cfg.momentum_fade_threshold)
    fade_min_sec   = int(cfg.momentum_fade_min_hold_min) * 60
    fade_min_profit= float(cfg.momentum_fade_min_profit)
    lu_enabled     = bool(getattr(cfg, "limit_up_exit_enabled", False))
    lu_pct         = float(getattr(cfg, "limit_up_pct", 0.30))
    atr_stop_on    = bool(getattr(cfg, "atr_stop_enabled", False))
    atr_trail_on   = bool(cfg.atr_trail_enabled)
    trail_fallback = float(cfg.trailing_stop_pct)
    signal_block_min = _parse_hhmm(cfg.signal_block_until, 545)
    buy_time_end_min = _parse_hhmm(cfg.buy_time_end, 720)
    buy_time_enabled = bool(getattr(cfg, "buy_time_limit_enabled", False))
    max_entry_gap  = float(getattr(cfg, "max_entry_above_breakout_pct", 0.10))
    max_close_pct  = float(getattr(cfg, "max_entry_above_close_pct", 999.0))
    max_trades_day = int(getattr(cfg, "max_trades_per_day", 5))
    cooldown_min_cfg = int(getattr(cfg, "cooldown_minutes", 0))
    mf_enabled     = bool(getattr(cfg, "market_filter_enabled", False))
    mf_ma_len      = int(getattr(cfg, "market_ma_length", 5))

    _adx_window = adx_len + 20

    # ATR 캐시 (DB 기반 — get_latest_atr 로 1회 조회 후 캐싱)
    from core.indicators import get_latest_atr
    atr_cache: dict[str, float | None] = {}

    def _get_atr(date_str: str) -> float | None:
        if date_str not in atr_cache:
            atr_cache[date_str] = get_latest_atr("daytrader.db", ticker, date_str)
        return atr_cache[date_str]

    def _calc_stop(entry_price: float, atr_pct: float | None) -> float:
        fallback = entry_price * (1.0 + cfg.momentum_stop_loss_pct)
        if not atr_stop_on or atr_pct is None:
            return fallback
        sl_pct = max(cfg.atr_stop_min_pct, min(cfg.atr_stop_max_pct, atr_pct * cfg.atr_stop_multiplier))
        return entry_price * (1.0 - sl_pct)

    all_trades: list[dict] = []
    prev_close_val  = 0.0
    prev_volume_val = 0.0
    prev_high_val   = 0.0

    for di in range(n_days):
        s = day_starts[di]
        e = day_ends[di]
        n  = e - s
        if n == 0:
            continue

        cl  = closes[s:e]
        hi  = highs[s:e]
        lo  = lows[s:e]
        vo  = volumes[s:e]
        mn  = minutes[s:e]
        ts_day = ts_pd[s:e]
        date_str = str(unique_dates[di])
        yyyymmdd = date_str.replace("-", "")

        cum_vols = np.cumsum(vo)
        vwap_arr = (np.cumsum(cl * vo) / np.maximum(cum_vols, 1e-9))

        # 시장 필터
        if mf_enabled and market_map:
            day_mf = market_map.get(yyyymmdd, {})
            mkt_key = "kospi" if market == "kospi" else "kosdaq"
            if not day_mf.get(mkt_key, True):
                prev_close_val  = float(cl[-1]) if n > 0 else prev_close_val
                prev_volume_val = float(np.sum(vo))
                prev_high_val   = float(np.max(hi)) if n > 0 else prev_high_val
                continue

        if prev_high_val <= 0:
            prev_close_val  = float(cl[-1]) if n > 0 else prev_close_val
            prev_volume_val = float(np.sum(vo))
            prev_high_val   = float(np.max(hi)) if n > 0 else prev_high_val
            continue

        breakout_level = prev_high_val * (1.0 + min_bp)
        required_vol   = prev_volume_val * vol_ratio
        atr_pct        = _get_atr(date_str)
        lu_price       = prev_close_val * (1.0 + lu_pct) if prev_close_val > 0 else 0.0

        position: dict | None = None
        breakout_price_day: float | None = None
        breakout_ts_day = None
        lot1_done  = False   # 1차 진입 완료
        lot2_done  = False   # 2차 진입 완료 (split 모드만)
        trade_count = 0
        last_exit_min: int | None = None

        for i in range(n):
            cl_i = cl[i]
            hi_i = hi[i]
            lo_i = lo[i]
            mn_i = int(mn[i])
            ts_i = ts_day.iloc[i]

            if position is None:
                # 돌파 최초 감지 추적
                if breakout_price_day is None and hi_i >= breakout_level:
                    breakout_price_day = breakout_level
                    breakout_ts_day    = ts_i

                cooldown_ok = (
                    last_exit_min is None
                    or cooldown_min_cfg <= 0
                    or (mn_i - last_exit_min) >= cooldown_min_cfg
                )
                close_chg_ok = (
                    max_close_pct >= 500.0
                    or prev_close_val <= 0
                    or (cl_i - prev_close_val) / prev_close_val * 100.0 <= max_close_pct
                )
                time_ok = (
                    mn_i >= signal_block_min
                    and (not buy_time_enabled or mn_i < buy_time_end_min)
                )

                if trade_count >= max_trades_day or not cooldown_ok:
                    pass

                elif first_ratio >= 1.0:
                    # ── baseline: breakout + volume → 100% ──────────────
                    vol_ok = cum_vols[i] >= required_vol
                    if (
                        time_ok
                        and prev_high_val > 0
                        and cl_i >= breakout_level
                        and vol_ok
                        and close_chg_ok
                    ):
                        bp = breakout_price_day if breakout_price_day is not None else breakout_level
                        entry_gap = (cl_i - bp) / bp if bp > 0 else 0.0
                        if entry_gap <= max_entry_gap:
                            adx_ok = True
                            if cfg.adx_enabled and i >= min_bars_adx - 1:
                                ws = max(0, i - _adx_window + 1)
                                a = _wilder_adx_numpy(hi[ws:i+1], lo[ws:i+1], cl[ws:i+1], adx_len)
                                av = a[-1] if len(a) > 0 else np.nan
                                adx_ok = not np.isnan(av) and av >= adx_min
                            elif cfg.adx_enabled:
                                adx_ok = False
                            last_bp_ok = (prev_high_val > 0 and (cl_i - prev_high_val) / prev_high_val >= min_bp)
                            if adx_ok and last_bp_ok:
                                trade_count += 1
                                ep_raw = cl_i
                                entry_price, net_entry = apply_buy_costs(ep_raw, costs)
                                stop_loss = _calc_stop(entry_price, atr_pct)
                                bt_min = (ts_i - breakout_ts_day).total_seconds() / 60.0 if breakout_ts_day else 0.0
                                position = {
                                    "entry_ts":         ts_i.to_pydatetime(),
                                    "breakout_ts":      breakout_ts_day.to_pydatetime() if breakout_ts_day else None,
                                    "entry_price":      entry_price,
                                    "net_entry":        net_entry,
                                    "stop_loss":        stop_loss,
                                    "highest_price":    float(hi_i),
                                    "breakeven_active": False,
                                    "lot1_only":        False,
                                    "vol_hit":          True,
                                    "delay_min":        bt_min,
                                }

                else:
                    # ── split mode ──────────────────────────────────────
                    # 1차: breakout만 충족 (vol 무관)
                    if (
                        not lot1_done
                        and time_ok
                        and prev_high_val > 0
                        and cl_i >= breakout_level
                        and close_chg_ok
                    ):
                        bp = breakout_price_day if breakout_price_day is not None else breakout_level
                        entry_gap = (cl_i - bp) / bp if bp > 0 else 0.0
                        if entry_gap <= max_entry_gap:
                            adx_ok = True
                            if cfg.adx_enabled and i >= min_bars_adx - 1:
                                ws = max(0, i - _adx_window + 1)
                                a = _wilder_adx_numpy(hi[ws:i+1], lo[ws:i+1], cl[ws:i+1], adx_len)
                                av = a[-1] if len(a) > 0 else np.nan
                                adx_ok = not np.isnan(av) and av >= adx_min
                            elif cfg.adx_enabled:
                                adx_ok = False
                            last_bp_ok = (prev_high_val > 0 and (cl_i - prev_high_val) / prev_high_val >= min_bp)
                            if adx_ok and last_bp_ok:
                                lot1_done = True
                                trade_count += 1
                                ep_raw = cl_i
                                ep1, net1 = apply_buy_costs(ep_raw, costs)
                                sl1 = _calc_stop(ep1, atr_pct)
                                bt_min = (ts_i - breakout_ts_day).total_seconds() / 60.0 if breakout_ts_day else 0.0
                                position = {
                                    "entry_ts":         ts_i.to_pydatetime(),
                                    "breakout_ts":      breakout_ts_day.to_pydatetime() if breakout_ts_day else None,
                                    "entry_price":      ep1,
                                    "net_entry":        net1,
                                    "net1":             net1,
                                    "net2":             None,
                                    "ratio1":           first_ratio,
                                    "ratio2":           0.0,
                                    "stop_loss":        sl1,
                                    "highest_price":    float(hi_i),
                                    "breakeven_active": False,
                                    "lot1_only":        True,
                                    "vol_hit":          False,
                                    "delay_min":        bt_min,
                                }

            elif position is not None and first_ratio < 1.0 and not lot2_done:
                # 2차: volume 충족 시 추가 매수
                if (
                    cum_vols[i] >= required_vol
                    and mn_i >= signal_block_min
                    and (not buy_time_enabled or mn_i < buy_time_end_min)
                ):
                    lot2_done = True
                    ep2, net2 = apply_buy_costs(cl_i, costs)
                    r1, r2 = first_ratio, 1.0 - first_ratio
                    net_combined = position["net1"] * r1 + net2 * r2
                    ep_combined  = position["entry_price"] * r1 + ep2 * r2
                    position["net2"]      = net2
                    position["ratio2"]    = r2
                    position["net_entry"] = net_combined
                    position["entry_price"] = ep_combined
                    position["lot1_only"]   = False
                    position["vol_hit"]     = True
                    # stop_loss는 lot1 기준 유지 (이미 계산됨)

            # ── 포지션 청산 ──────────────────────────────────────────
            if position is None:
                pass
            else:
                remaining = 1.0
                net_e     = position["net_entry"]

                # 상한가
                if lu_enabled and lu_price > 0 and hi_i >= lu_price:
                    ep_x, net_x = apply_sell_costs(lu_price, costs)
                    pnl = (net_x - net_e) * remaining
                    all_trades.append({
                        "entry_ts":    position["entry_ts"],
                        "breakout_ts": position.get("breakout_ts"),
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep_x,
                        "pnl":         pnl,
                        "pnl_pct":     (net_x - net_e) / net_e,
                        "exit_reason": "limit_up_exit",
                        "lot1_only":   position["lot1_only"],
                        "vol_hit":     position["vol_hit"],
                        "delay_min":   position.get("delay_min", 0.0),
                        "highest_price": max(position["highest_price"], float(hi_i)),
                    })
                    position = None
                    lot1_done = False
                    lot2_done = False
                    last_exit_min = mn_i
                    continue

                # 1차 손절 (trailing 갱신 전)
                if lo_i <= position["stop_loss"]:
                    ep_x, net_x = apply_sell_costs(position["stop_loss"], costs)
                    pnl = (net_x - net_e) * remaining
                    reason = (
                        "breakeven_stop"
                        if position["breakeven_active"] and position["stop_loss"] >= position["entry_price"]
                        else "stop_loss"
                    )
                    all_trades.append({
                        "entry_ts":    position["entry_ts"],
                        "breakout_ts": position.get("breakout_ts"),
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep_x,
                        "pnl":         pnl,
                        "pnl_pct":     (net_x - net_e) / net_e,
                        "exit_reason": reason,
                        "lot1_only":   position["lot1_only"],
                        "vol_hit":     position["vol_hit"],
                        "delay_min":   position.get("delay_min", 0.0),
                        "highest_price": position["highest_price"],
                    })
                    position = None
                    lot1_done = False
                    lot2_done = False
                    last_exit_min = mn_i
                    continue

                # 고점 갱신 + trailing
                if hi_i > position["highest_price"]:
                    position["highest_price"] = float(hi_i)
                    decay     = _get_decay(mn_i, td_phases, td_enabled)
                    eff_mult  = trail_mult * decay
                    eff_min   = max(trail_min_pct * decay, td_floor)
                    if atr_trail_on and atr_pct is not None:
                        raw_t  = atr_pct * eff_mult
                        tr_pct = max(eff_min, min(trail_max_pct, raw_t))
                    else:
                        tr_pct = max(eff_min, trail_fallback)
                    new_sl = position["highest_price"] * (1.0 - tr_pct)
                    position["stop_loss"] = max(position["stop_loss"], new_sl)

                # Breakeven (FastBacktester 동일: 단순 be_trigger_pct 기준)
                if be_enabled and not position["breakeven_active"]:
                    peak_ret = (position["highest_price"] - position["entry_price"]) / position["entry_price"]
                    if peak_ret >= be_trigger_pct:
                        be_stop = position["entry_price"] * (1.0 + be_offset_pct)
                        position["stop_loss"] = max(position["stop_loss"], be_stop)
                        position["breakeven_active"] = True

                # 2차 손절 (trailing 후)
                if lo_i <= position["stop_loss"]:
                    ep_x, net_x = apply_sell_costs(position["stop_loss"], costs)
                    pnl = (net_x - net_e) * remaining
                    reason = (
                        "breakeven_stop"
                        if (position["breakeven_active"] and position["stop_loss"] >= position["entry_price"]
                            and position["stop_loss"] <= position["entry_price"] * 1.02)
                        else "trailing_stop"
                    )
                    all_trades.append({
                        "entry_ts":    position["entry_ts"],
                        "breakout_ts": position.get("breakout_ts"),
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep_x,
                        "pnl":         pnl,
                        "pnl_pct":     (net_x - net_e) / net_e,
                        "exit_reason": reason,
                        "lot1_only":   position["lot1_only"],
                        "vol_hit":     position["vol_hit"],
                        "delay_min":   position.get("delay_min", 0.0),
                        "highest_price": position["highest_price"],
                    })
                    position = None
                    lot1_done = False
                    lot2_done = False
                    last_exit_min = mn_i
                    continue

                # Momentum fade
                if fade_enabled:
                    now_dt   = ts_i.to_pydatetime()
                    entry_dt = position["entry_ts"]
                    hold_sec = (now_dt - entry_dt).total_seconds()
                    if hold_sec >= fade_min_sec:
                        cur_profit = (cl_i - position["entry_price"]) / position["entry_price"]
                        if cur_profit >= fade_min_profit:
                            lb_idx = max(0, i - fade_lookback)
                            base_c = float(cl[lb_idx])
                            if base_c > 0 and (cl_i / base_c - 1.0) <= fade_threshold:
                                ep_x, net_x = apply_sell_costs(cl_i, costs)
                                pnl = (net_x - net_e) * remaining
                                all_trades.append({
                                    "entry_ts":    position["entry_ts"],
                                    "breakout_ts": position.get("breakout_ts"),
                                    "exit_ts":     now_dt,
                                    "entry_price": position["entry_price"],
                                    "exit_price":  ep_x,
                                    "pnl":         pnl,
                                    "pnl_pct":     (net_x - net_e) / net_e,
                                    "exit_reason": "momentum_fade",
                                    "lot1_only":   position["lot1_only"],
                                    "vol_hit":     position["vol_hit"],
                                    "delay_min":   position.get("delay_min", 0.0),
                                    "highest_price": position["highest_price"],
                                })
                                position = None
                                lot1_done = False
                                lot2_done = False
                                last_exit_min = mn_i
                                continue

                # 강제 청산 (마지막 캔들)
                if i == n - 1 and position is not None:
                    ep_x, net_x = apply_sell_costs(cl_i, costs)
                    pnl = (net_x - net_e) * remaining
                    all_trades.append({
                        "entry_ts":    position["entry_ts"],
                        "breakout_ts": position.get("breakout_ts"),
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep_x,
                        "pnl":         pnl,
                        "pnl_pct":     (net_x - net_e) / net_e,
                        "exit_reason": "forced_close",
                        "lot1_only":   position["lot1_only"],
                        "vol_hit":     position["vol_hit"],
                        "delay_min":   position.get("delay_min", 0.0),
                        "highest_price": position["highest_price"],
                    })
                    position = None
                    lot1_done = False
                    lot2_done = False
                    last_exit_min = mn_i

        prev_close_val  = float(cl[-1]) if n > 0 else prev_close_val
        prev_volume_val = float(np.sum(vo))
        prev_high_val   = float(np.max(hi)) if n > 0 else prev_high_val

    return all_trades


# ---------------------------------------------------------------------------
# 통계 계산
# ---------------------------------------------------------------------------

def _compute_stats(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {
            "pf": 0.0, "pnl": 0, "trades": 0,
            "win_rate": 0.0, "fc_pct": 0.0,
            "lot1_only_pct": 0.0, "sl_lot1_pct": 0.0,
            "avg_delay_min": 0.0,
        }
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    pnl = sum(t["pnl"] for t in trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    exits = Counter(t.get("exit_reason", "?") for t in trades)
    lot1_only = sum(1 for t in trades if t.get("lot1_only", False))
    sl_trades  = [t for t in trades if t.get("exit_reason") == "stop_loss"]
    sl_lot1    = sum(1 for t in sl_trades if t.get("lot1_only", False))
    delays     = [t.get("delay_min", 0.0) for t in trades]
    avg_delay  = sum(delays) / len(delays) if delays else 0.0
    return {
        "pf":           round(gp / gl, 4) if gl > 0 else float("inf"),
        "pnl":          int(pnl),
        "trades":       n,
        "win_rate":     round(wins / n, 4),
        "fc_pct":       round(exits.get("forced_close", 0) / n * 100, 2),
        "lot1_only_pct": round(lot1_only / n * 100, 2),
        "sl_lot1_pct":  round(sl_lot1 / max(len(sl_trades), 1) * 100, 2),
        "avg_delay_min": round(avg_delay, 1),
        "exit_counts":  dict(exits),
    }


# ---------------------------------------------------------------------------
# 전 종목 실행
# ---------------------------------------------------------------------------

def _run_all_tickers(
    candles_cache: dict,
    ticker_to_market: dict,
    market_map: dict,
    cfg,
    costs,
    first_ratio: float,
) -> list[dict]:
    all_trades = []
    for tk, df in candles_cache.items():
        market = ticker_to_market.get(tk, "unknown")
        trades = _run_split_entry(df, tk, market, market_map, cfg, costs, first_ratio)
        for t in trades:
            t["ticker"] = tk
            all_trades.append(t)
    return all_trades


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

async def main(verify_only: bool = False):
    import dataclasses as _dc
    from core.cost_model import TradeCosts

    print("=" * 60)
    print("분할 진입 그리드 - split_first_ratio x [0.3, 0.5, 0.7, 1.0]")
    print("=" * 60)

    t0 = _time.time()

    if verify_only:
        first_ratios = [1.0]
    else:
        first_ratios = [1.0, 0.7, 0.5, 0.3]

    print("[LOAD] OLD 구간 캔들 로드…")
    cache_old = await load_candle_cache(OLD_START, OLD_END)
    print("[LOAD] NEW 구간 캔들 로드…")
    cache_new = await load_candle_cache(NEW_START, NEW_END)

    costs = TradeCosts.from_backtest_config(cache_old.bt_config)

    rows = []
    for fr in first_ratios:
        tag = f"baseline" if fr >= 1.0 else f"split_{int(fr*10):02d}"
        print(f"\n[RUN] first_ratio={fr:.1f} ({tag})")

        t1 = _time.time()
        old_trades = _run_all_tickers(
            cache_old.candles, cache_old.ticker_to_market, cache_old.market_map,
            cache_old.base_config, costs, fr,
        )
        new_trades = _run_all_tickers(
            cache_new.candles, cache_new.ticker_to_market, cache_new.market_map,
            cache_new.base_config, costs, fr,
        )
        elapsed = _time.time() - t1

        old_s = _compute_stats(old_trades)
        new_s = _compute_stats(new_trades)
        row = {
            "tag":           tag,
            "first_ratio":   fr,
            "old_pf":        old_s["pf"],
            "old_pnl":       old_s["pnl"],
            "old_trades":    old_s["trades"],
            "old_winrate":   old_s["win_rate"],
            "old_fc_pct":    old_s["fc_pct"],
            "old_lot1only":  old_s["lot1_only_pct"],
            "old_sl_lot1":   old_s["sl_lot1_pct"],
            "old_delay_min": old_s["avg_delay_min"],
            "new_pf":        new_s["pf"],
            "new_pnl":       new_s["pnl"],
            "new_trades":    new_s["trades"],
            "elapsed_s":     round(elapsed, 1),
        }
        rows.append(row)

        if verify_only and fr >= 1.0:
            pf_ok  = abs(old_s["pf"] - BASELINE["pf"]) <= VERIFY_TOLERANCE["pf"]
            pnl_ok = abs(old_s["pnl"] - BASELINE["pnl"]) <= VERIFY_TOLERANCE["pnl"]
            status = "OK" if pf_ok and pnl_ok else "MISMATCH"
            print(f"  [VERIFY] PF={old_s['pf']:.3f} (expect {BASELINE['pf']}) → {'OK' if pf_ok else 'FAIL'}")
            print(f"  [VERIFY] PnL={old_s['pnl']:+,} (expect {BASELINE['pnl']:+,}) → {'OK' if pnl_ok else 'FAIL'}")
            print(f"  [VERIFY] 종합: {status}")

        print(f"  OLD  PF={old_s['pf']:.3f}  PnL={old_s['pnl']:+,}  #={old_s['trades']}  "
              f"lot1only%={old_s['lot1_only_pct']:.1f}%  sl_lot1%={old_s['sl_lot1_pct']:.1f}%  "
              f"delay={old_s['avg_delay_min']:.1f}분")
        print(f"  NEW  PF={new_s['pf']:.3f}  PnL={new_s['pnl']:+,}  #={new_s['trades']}")

    total_elapsed = _time.time() - t0
    print(f"\n[완료] 총 {total_elapsed:.1f}초")

    print("\n" + "=" * 60)
    print("결과 요약")
    print("=" * 60)
    header = f"{'tag':<15}{'ratio':>6} | {'OLD_PF':>7} {'OLD_PnL':>10} {'#':>5} {'lot1only%':>10} {'delay분':>7} | {'NEW_PF':>7} {'NEW_PnL':>10}"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r['tag']:<15}{r['first_ratio']:>6.1f} | "
            f"{r['old_pf']:>7.3f} {r['old_pnl']:>+10,} {r['old_trades']:>5} "
            f"{r['old_lot1only']:>10.1f}% {r['old_delay_min']:>6.1f}분 | "
            f"{r['new_pf']:>7.3f} {r['new_pnl']:>+10,}"
        )

    # 선정 기준 평가
    print("\n[선정기준] OLD PF >= 3.0인 조합:")
    passed = [r for r in rows if r["old_pf"] >= 3.0]
    if passed:
        for r in passed:
            print(f"  → {r['tag']} PF={r['old_pf']:.3f}")
    else:
        print("  → 없음 (전 조합 기준 미달)")

    # 리포트 저장
    if not verify_only:
        _save_report(rows)


def _save_report(rows: list[dict]):
    lines = [
        "# 모멘텀 분할 진입 그리드 결과",
        "",
        f"> 생성: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> baseline OLD PF={BASELINE['pf']} / PnL={BASELINE['pnl']:+,} / #={BASELINE['trades']}",
        "",
        "## 결과 테이블",
        "",
        "| tag | ratio | OLD_PF | OLD_PnL | #거래 | lot1only% | sl_lot1% | delay분 | NEW_PF | NEW_PnL |",
        "|-----|-------|--------|---------|-------|----------|---------|--------|--------|--------|",
    ]
    for r in rows:
        lines.append(
            f"| {r['tag']} | {r['first_ratio']:.1f} | {r['old_pf']:.3f} | "
            f"{r['old_pnl']:+,} | {r['old_trades']} | {r['old_lot1only']:.1f}% | "
            f"{r['old_sl_lot1']:.1f}% | {r['old_delay_min']:.1f} | {r['new_pf']:.3f} | {r['new_pnl']:+,} |"
        )

    lines += [
        "",
        "## 지표 설명",
        "",
        "- **lot1only%**: 전체 거래 중 2차(volume 충족) 없이 1차만으로 청산된 비율",
        "- **sl_lot1%**: stop_loss 청산 중 1차만(vol 미충족) 상태에서 잡힌 비율 (허수돌파 척도)",
        "- **delay분**: BREAKOUT 감지 → 1차 진입까지 평균 지연 (분)",
        "",
        "## 선정 기준",
        "",
        "- OLD PF >= 3.0 (baseline 4.881의 60% 이상)",
        "- NEW 기간 방향성 일치",
    ]

    out = Path("reports/split_entry_grid_result.md")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[저장] {out}")


if __name__ == "__main__":
    verify = "--verify" in sys.argv
    asyncio.run(main(verify_only=verify))
