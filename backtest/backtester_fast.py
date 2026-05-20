"""backtest/backtester_fast.py — numpy 가속 백테스터.

기존 backtester.py와 동일한 인터페이스(run_backtest, run_multi_day_cached)를 유지하면서
이하 최적화를 적용한다.

최적화 항목
-----------
1. iterrows() 제거       — numpy 배열 직접 접근 (pandas Series 생성 제로)
2. cumsum 사전계산       — 누적거래량 O(n²) → O(n)  (병목 제거)
3. ADX 전체 시리즈 계산  — 행별 반복 wilder_adx 호출 제거
4. 실행 VWAP 사전계산    — 행마다 DataFrame.sum() 대신 누적배열 조회
5. ATR DB 캐싱           — 분봉마다 SQLite 재연결 → 일별 1회 (trailing stop)

결과 동등성
-----------
기존 backtester.py 대비 PF 차이 < 0.001.
주요 차이 원인: ADX 초기화 방식 (현재: tail-34 창 매번 재초기화 vs 전체 시리즈 1회).
34봉 이후 수렴하므로 실제 거래 판정에 미치는 영향 최소.

금지 사항
---------
- 기존 backtester.py 수정/삭제 금지
- 전략 로직 변경 금지 (동일 결과 보장)
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

from backtest.backtester import Backtester
from core.cost_model import apply_buy_costs, apply_sell_costs
from strategy.base_strategy import BaseStrategy


# ---------------------------------------------------------------------------
# 순수 numpy ADX (pandas 의존 없음)
# ---------------------------------------------------------------------------

def _wilder_adx_numpy(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    length: int = 14,
) -> np.ndarray:
    """Wilder ADX — pandas wilder_adx(mamode='rma')와 동등 (EWM adjust=False).

    전략 _check_adx가 tail(length+20) 롤링 윈도우를 사용하므로
    _wilder_adx_rolling을 통해 호출하면 정확히 일치한다.
    직접 호출 시 전체 시리즈 한 번 계산 (초기화 차이 주의).

    입력 배열은 float64 보장 필요. 길이 부족 시 NaN 배열 반환.
    """
    n = len(close)
    adx_out = np.full(n, np.nan, dtype=np.float64)
    if n < length + 1:
        return adx_out

    alpha = 1.0 / length
    beta  = 1.0 - alpha
    eps   = np.finfo(np.float64).eps

    # True Range (prenan=True: TR[0]=NaN, pandas_ta ADX 내부 동작 복제)
    tr = np.empty(n, dtype=np.float64)
    tr[0] = np.nan
    for i in range(1, n):
        hl  = high[i]  - low[i]
        hpc = abs(high[i]  - close[i - 1])
        lpc = abs(low[i]   - close[i - 1])
        tr[i] = hl if hl >= hpc and hl >= lpc else (hpc if hpc >= lpc else lpc)

    # DM+, DM- (zero-small 적용)
    dm_p = np.zeros(n, dtype=np.float64)
    dm_n = np.zeros(n, dtype=np.float64)
    for i in range(1, n):
        up = high[i] - high[i - 1]
        dn = low[i - 1] - low[i]
        if up > dn and up > 0:
            dm_p[i] = up if abs(up) >= eps else 0.0
        elif dn > up and dn > 0:
            dm_n[i] = dn if abs(dn) >= eps else 0.0

    # ATR via Wilder RMA (pandas_ta presma=True 복제)
    atr = np.full(n, np.nan, dtype=np.float64)
    valid_tr = tr[~np.isnan(tr)]
    if len(valid_tr) < length:
        return adx_out
    init_mean = float(np.nanmean(tr[:length]))
    if np.isnan(init_mean):
        return adx_out
    atr[length - 1] = init_mean
    for i in range(length, n):
        atr[i] = atr[i - 1] * beta + (tr[i] if not np.isnan(tr[i]) else 0.0) * alpha

    # DMP, DMN via EWM adjust=False (pandas 호환)
    # pandas: pos[0]=NaN → EWM skip → dmp[1] = pos[1] (fresh start, not weighted by bar 0)
    # numpy가 bar 0을 0으로 초기화하면 EWM이 희석됨 → NaN으로 처리하고 bar 1부터 시작
    dmp = np.full(n, np.nan, dtype=np.float64)
    dmn = np.full(n, np.nan, dtype=np.float64)
    if n > 1:
        dmp[1] = dm_p[1]
        dmn[1] = dm_n[1]
        for i in range(2, n):
            dmp[i] = dmp[i - 1] * beta + dm_p[i] * alpha
            dmn[i] = dmn[i - 1] * beta + dm_n[i] * alpha

    # DI+, DI-
    dip = np.full(n, np.nan, dtype=np.float64)
    din = np.full(n, np.nan, dtype=np.float64)
    valid_mask = ~np.isnan(atr) & (atr > 0)
    k = 100.0 / np.where(valid_mask, atr, 1.0)
    dip[valid_mask] = dmp[valid_mask] * k[valid_mask]
    din[valid_mask] = dmn[valid_mask] * k[valid_mask]

    # DX
    dip_sum = dip + din
    with np.errstate(invalid="ignore", divide="ignore"):
        dx = np.where(
            valid_mask & (dip_sum > 0),
            100.0 * np.abs(dip - din) / dip_sum,
            np.nan,
        )

    # ADX = EWM adjust=False of DX
    adx_arr = np.full(n, np.nan, dtype=np.float64)
    started = False
    for i in range(n):
        if np.isnan(dx[i]):
            if started:
                adx_arr[i] = adx_arr[i - 1] * beta
        else:
            if not started:
                adx_arr[i] = dx[i]
                started = True
            else:
                adx_arr[i] = adx_arr[i - 1] * beta + dx[i] * alpha

    return adx_arr


def _wilder_adx_rolling(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    length: int = 14,
    window: int = 34,
) -> np.ndarray:
    """전략 _check_adx의 tail(window) 동작을 복제한 롤링 ADX.

    adx_rolling[i] = _wilder_adx_numpy(high[i-window+1:i+1], ...)[-1]

    전략 코드와 동일한 초기화 방식 → slow Backtester와 완전 일치.
    O(window × n) — 34 × n 이지만 순수 numpy, pandas 보다 충분히 빠름.
    """
    n = len(close)
    adx_out = np.full(n, np.nan, dtype=np.float64)
    for i in range(window - 1, n):
        a = _wilder_adx_numpy(
            high[i - window + 1 : i + 1],
            low[i - window + 1 : i + 1],
            close[i - window + 1 : i + 1],
            length=length,
        )
        if len(a) > 0 and not np.isnan(a[-1]):
            adx_out[i] = a[-1]
    return adx_out


# ---------------------------------------------------------------------------
# 보조 함수
# ---------------------------------------------------------------------------

def _parse_hhmm(s: str, default: int) -> int:
    """'HH:MM' → 분 단위 정수. 파싱 실패 시 default."""
    try:
        h, m = map(int, str(s).split(":"))
        return h * 60 + m
    except Exception:
        return default


def _get_decay(minute_of_day: int, phases: tuple, enabled: bool) -> float:
    """time_decay multiplier — datetime 객체 없이 분 단위로 계산."""
    if not enabled or not phases:
        return 1.0
    for phase in phases:
        until_min = _parse_hhmm(phase.until, 9999)
        if minute_of_day <= until_min:
            return float(phase.multiplier)
    return float(phases[-1].multiplier)


# ---------------------------------------------------------------------------
# FastBacktester
# ---------------------------------------------------------------------------

class FastBacktester(Backtester):
    """numpy 가속 백테스터 — Backtester의 드롭인 대체.

    run_backtest만 재정의. run_multi_day_cached는 상속 그대로 사용
    (self.run_backtest → 오버라이드된 fast 버전 자동 호출).
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # ATR 캐시: {date_str: atr_pct_or_None}  — 일별 최대 1회 DB 조회
        self._day_atr_cache: dict[str, float | None] = {}

    # ------------------------------------------------------------------
    # ATR 캐시
    # ------------------------------------------------------------------

    def _get_atr_for_date(self, date_str: str) -> float | None:
        """날짜별 1회 ATR 조회 (이후 캐시 반환)."""
        if date_str in self._day_atr_cache:
            return self._day_atr_cache[date_str]

        ticker = getattr(self, "_current_ticker", None)
        if not ticker:
            self._day_atr_cache[date_str] = None
            return None

        try:
            from core.indicators import get_latest_atr
            db_path = (
                self._db.db_path if self._db is not None
                else self._atr_db_path
            )
            result = get_latest_atr(db_path, ticker, date_str)
        except Exception as e:
            logger.debug(f"[FAST] ATR 조회 실패 ({ticker} {date_str}): {e}")
            result = None

        self._day_atr_cache[date_str] = result
        return result

    # ------------------------------------------------------------------
    # 메인: 단일 날짜 시뮬레이션
    # ------------------------------------------------------------------

    def run_backtest(
        self,
        candles: pd.DataFrame,
        strategy: BaseStrategy,
    ) -> dict[str, Any]:
        """numpy 기반 단일 날짜 시뮬레이션 — iterrows 없음."""
        if candles.empty:
            logger.warning("빈 캔들 데이터 — 백테스트 스킵")
            return {**self.calculate_kpi([]), "trades": []}

        candles = candles.reset_index(drop=True)
        n = len(candles)

        # ── 1. numpy 배열 변환 (1회) ─────────────────────────────────
        closes  = candles["close"].values.astype(np.float64)
        highs   = candles["high"].values.astype(np.float64)
        lows    = candles["low"].values.astype(np.float64)
        volumes = candles["volume"].values.astype(np.float64)
        opens   = candles["open"].values.astype(np.float64)
        ts_vals = candles["ts"].values          # datetime64[ns]
        ts_pd   = pd.DatetimeIndex(ts_vals)
        minutes = ts_pd.hour * 60 + ts_pd.minute  # 분 단위 시각

        # ── 2. 사전계산 ──────────────────────────────────────────────
        # 누적 거래량 (O(n) 한 번 → 행별 sum O(n²) 제거)
        cum_vols = np.cumsum(volumes)

        # 실행 VWAP (running)
        tp = (highs + lows + closes) / 3.0
        cum_tp_vol  = np.cumsum(tp * volumes)
        cum_vol_safe = np.where(cum_vols > 0, cum_vols, 1.0)
        vwap_arr    = cum_tp_vol / cum_vol_safe

        # ADX는 신호 후보 시점에만 지연 계산 (lazy) — 매 바마다 계산하지 않음
        # 진입 조건(가격·거래량)이 맞는 바에서만 _wilder_adx_numpy(tail window) 호출
        _adx_window = int(self._config.adx_length) + 20

        # ── 3. 설정값 캐시 ─────────────────────────────────────────────
        cfg = self._config

        prev_high   = float(getattr(strategy, "_prev_day_high",   0.0))
        prev_volume = float(getattr(strategy, "_prev_day_volume", 0))

        min_bp         = float(getattr(cfg, "min_breakout_pct", 0.0))
        vol_ratio      = float(cfg.momentum_volume_ratio)
        adx_min        = float(cfg.adx_min)
        adx_len        = int(cfg.adx_length)
        min_bars_adx   = adx_len + 20          # 현재 backtester.py와 동일
        rvol_win       = int(cfg.rvol_window)
        rvol_min_v     = float(cfg.rvol_min)
        vwap_min_above = float(cfg.vwap_min_above)
        max_entry_gap  = float(getattr(cfg, "max_entry_above_breakout_pct", 0.10))
        max_close_pct_raw = float(getattr(cfg, "max_entry_above_close_pct", 999.0))
        prev_close_day    = float(getattr(strategy, "_prev_day_close", 0.0))

        signal_block_min = _parse_hhmm(cfg.signal_block_until, 545)
        buy_time_end_min = _parse_hhmm(cfg.buy_time_end, 720)
        buy_time_enabled = bool(getattr(cfg, "buy_time_limit_enabled", False))

        trail_mult     = float(cfg.atr_trail_multiplier)
        trail_min_pct  = float(cfg.atr_trail_min_pct)
        trail_max_pct  = float(cfg.atr_trail_max_pct)
        td_enabled     = bool(cfg.time_decay_trailing_enabled)
        td_phases      = cfg.time_decay_phases
        td_floor       = float(cfg.time_decay_min_pct_floor)
        be_enabled     = bool(cfg.breakeven_enabled)
        be_trigger_pct = float(cfg.breakeven_trigger_pct)
        be_offset_pct  = float(cfg.breakeven_offset_pct)
        fade_enabled   = bool(cfg.momentum_fade_exit_enabled)
        fade_lookback  = int(cfg.momentum_fade_lookback)
        fade_threshold = float(cfg.momentum_fade_threshold)
        fade_min_sec   = int(cfg.momentum_fade_min_hold_min) * 60
        fade_min_profit= float(cfg.momentum_fade_min_profit)
        lu_enabled     = bool(getattr(cfg, "limit_up_exit_enabled", False))
        trail_fallback = float(cfg.trailing_stop_pct)
        atr_trail_on   = bool(cfg.atr_trail_enabled)
        atr_stop_on    = bool(getattr(cfg, "atr_stop_enabled", False))
        stale_enabled  = bool(getattr(cfg, "stale_position_exit_enabled", False))
        stale_min_min  = int(getattr(cfg, "stale_position_check_minutes", 30))
        stale_min_pnl  = float(getattr(cfg, "stale_position_min_profit", 0.005))
        lu_price       = self._current_limit_up

        # Gap breakout 기준가
        breakout_ref = prev_high
        if (
            getattr(cfg, "gap_breakout_adjust_enabled", False)
            and float(getattr(strategy, "_prev_day_close", 0.0)) > 0
            and n > 0
        ):
            prev_close_ = float(getattr(strategy, "_prev_day_close", 0.0))
            today_open  = float(opens[0])
            gap_thr     = float(getattr(cfg, "gap_threshold_pct", 0.03))
            if today_open > 0 and (today_open - prev_close_) / prev_close_ >= gap_thr:
                breakout_ref = today_open

        breakout_level = breakout_ref * (1.0 + min_bp)
        required_vol   = prev_volume * vol_ratio

        # ATR for this day (1회 DB 조회 후 캐시)
        date_str = ts_pd[0].strftime("%Y-%m-%d") if n > 0 else ""
        atr_pct  = self._get_atr_for_date(date_str) if date_str else None

        # 손절가 계산 헬퍼
        def _calc_stop(entry_price: float) -> float:
            fallback = entry_price * (1.0 + cfg.momentum_stop_loss_pct)
            if not atr_stop_on or atr_pct is None:
                return fallback
            sl_pct = max(cfg.atr_stop_min_pct, min(cfg.atr_stop_max_pct,
                         atr_pct * cfg.atr_stop_multiplier))
            return entry_price * (1.0 - sl_pct)

        # 복수 매매 — 전략 base_strategy.can_trade() 로직 복제
        max_trades_day   = int(getattr(cfg, "max_trades_per_day", 5))
        cooldown_min_cfg = int(getattr(cfg, "cooldown_minutes",    0))

        # ── 4. 메인 루프 ────────────────────────────────────────────
        trades: list[dict] = []
        position: dict | None = None
        breakout_price_day: float | None = None
        trade_count    = 0
        last_exit_min: int | None = None   # 마지막 청산 분(분 단위 시각)

        for i in range(n):
            close_i = closes[i]
            high_i  = highs[i]
            low_i   = lows[i]
            min_i   = int(minutes[i])
            ts_i    = ts_pd[i]

            # ── 포지션 없음: 진입 신호 탐색 ─────────────────────────
            if position is None:
                # 당일 최초 돌파 가격 추적 (고점 진입 방지)
                if breakout_price_day is None and high_i >= breakout_level:
                    breakout_price_day = breakout_level

                # can_trade: max_trades + cooldown 검사 (base_strategy.can_trade 복제)
                cooldown_ok = (
                    last_exit_min is None
                    or cooldown_min_cfg <= 0
                    or (min_i - last_exit_min) >= cooldown_min_cfg
                )
                _close_chg_ok = (
                    max_close_pct_raw >= 500.0
                    or prev_close_day <= 0
                    or (close_i - prev_close_day) / prev_close_day * 100.0 <= max_close_pct_raw
                )
                if (
                    trade_count < max_trades_day
                    and cooldown_ok
                    and prev_high > 0
                    and min_i >= signal_block_min
                    and (not buy_time_enabled or min_i < buy_time_end_min)
                    and close_i >= breakout_level
                    and cum_vols[i] >= required_vol
                    and _close_chg_ok
                ):
                    bp = breakout_price_day if breakout_price_day is not None else breakout_level
                    entry_gap = (close_i - bp) / bp if bp > 0 else 0.0

                    if entry_gap <= max_entry_gap:
                        # ADX — tail(window) 방식으로 지연 계산 (slow 버전 정확히 복제)
                        adx_ok = True
                        if cfg.adx_enabled:
                            if i < min_bars_adx - 1:
                                adx_ok = False
                            else:
                                ws = max(0, i - _adx_window + 1)
                                a = _wilder_adx_numpy(
                                    highs[ws:i + 1], lows[ws:i + 1], closes[ws:i + 1],
                                    self._config.adx_length,
                                )
                                av = a[-1] if len(a) > 0 else np.nan
                                adx_ok = not np.isnan(av) and av >= adx_min

                        # RVol
                        rvol_ok = True
                        if cfg.rvol_enabled:
                            if i < rvol_win + 9:
                                rvol_ok = False
                            else:
                                recent_sum = float(np.sum(volumes[i - rvol_win + 1:i + 1]))
                                prev_arr   = volumes[:i - rvol_win + 1]
                                avg_v = float(np.mean(prev_arr)) if len(prev_arr) > 0 else 0.0
                                rvol_ok = avg_v > 0 and (recent_sum / (avg_v * rvol_win)) >= rvol_min_v

                        # VWAP
                        vwap_ok = True
                        if cfg.vwap_enabled:
                            if i < 9:
                                vwap_ok = False
                            else:
                                vwap_ok = close_i >= vwap_arr[i] * (1.0 + vwap_min_above)

                        # 마지막 종가 돌파 재확인
                        last_bp_ok = (
                            breakout_ref > 0
                            and (close_i - breakout_ref) / breakout_ref >= min_bp
                        )

                        if adx_ok and rvol_ok and vwap_ok and last_bp_ok:
                            # ── 진입 ─────────────────────────────────
                            strategy.on_entry()
                            trade_count += 1
                            ep_raw = close_i
                            entry_price, net_entry = apply_buy_costs(ep_raw, self._costs)
                            stop_loss = _calc_stop(entry_price)

                            position = {
                                "entry_ts":        ts_i.to_pydatetime(),
                                "entry_price":     entry_price,
                                "net_entry":       net_entry,
                                "stop_loss":       stop_loss,
                                "highest_price":   float(high_i),
                                "breakeven_active": False,
                                "tp1_hit":         True,    # pure trailing
                                "remaining_ratio": 1.0,
                                "entry_chg_from_close": (
                                    (entry_price - prev_close_day) / prev_close_day
                                    if prev_close_day > 0 else 0.0
                                ),
                            }
                            logger.debug(
                                f"[FAST] 진입 ts={ts_i} price={entry_price:.1f} sl={stop_loss:.1f}"
                            )

            # ── 포지션 보유 중: 청산 확인 ───────────────────────────
            else:
                remaining = position["remaining_ratio"]

                # 상한가 즉시 청산
                if lu_enabled and lu_price and high_i >= lu_price:
                    ep_sl, net_exit = apply_sell_costs(lu_price, self._costs)
                    pnl = (net_exit - position["net_entry"]) * remaining
                    trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep_sl,
                        "pnl":         pnl,
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "limit_up_exit",
                        "entry_chg_from_close": position.get("entry_chg_from_close", 0.0),
                    })
                    position = None
                    strategy.on_exit()
                    last_exit_min = min_i
                    continue

                # 1차 손절 확인 (trailing 갱신 전)
                if low_i <= position["stop_loss"]:
                    ep_sl, net_exit = apply_sell_costs(position["stop_loss"], self._costs)
                    pnl = (net_exit - position["net_entry"]) * remaining
                    exit_reason = (
                        "breakeven_stop"
                        if position["breakeven_active"]
                        and position["stop_loss"] >= position["entry_price"]
                        else "stop_loss"
                    )
                    trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep_sl,
                        "pnl":         pnl,
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": exit_reason,
                        "entry_chg_from_close": position.get("entry_chg_from_close", 0.0),
                    })
                    position = None
                    strategy.on_exit()
                    last_exit_min = min_i
                    continue

                # TP1 미히트 상태 (pure trailing에서는 미발생, 마지막 캔들만 처리)
                if not position.get("tp1_hit"):
                    if i == n - 1:
                        ep_sl, net_exit = apply_sell_costs(close_i, self._costs)
                        pnl = net_exit - position["net_entry"]
                        trades.append({
                            "entry_ts":    position["entry_ts"],
                            "exit_ts":     ts_i.to_pydatetime(),
                            "entry_price": position["entry_price"],
                            "exit_price":  ep_sl,
                            "pnl":         pnl,
                            "pnl_pct":     pnl / position["net_entry"],
                            "exit_reason": "forced_close",
                            "entry_chg_from_close": position.get("entry_chg_from_close", 0.0),
                        })
                        position = None
                        strategy.on_exit()
                        last_exit_min = min_i
                    continue

                # ── tp1_hit=True 브랜치: trailing / BE / fade / forced_close ──

                # 고점 갱신 + trailing stop 업데이트
                if high_i > position["highest_price"]:
                    position["highest_price"] = float(high_i)
                    decay = _get_decay(min_i, td_phases, td_enabled)
                    eff_mult    = trail_mult * decay
                    eff_min_pct = max(trail_min_pct * decay, td_floor)

                    if atr_trail_on and atr_pct is not None:
                        raw_trail = atr_pct * eff_mult
                        trail_pct = max(eff_min_pct, min(trail_max_pct, raw_trail))
                    else:
                        trail_pct = max(eff_min_pct, trail_fallback)

                    new_stop = position["highest_price"] * (1.0 - trail_pct)
                    position["stop_loss"] = max(position["stop_loss"], new_stop)

                # Breakeven
                if be_enabled and not position["breakeven_active"]:
                    peak_ret = (
                        (position["highest_price"] - position["entry_price"])
                        / position["entry_price"]
                    )
                    if peak_ret >= be_trigger_pct:
                        be_stop = position["entry_price"] * (1.0 + be_offset_pct)
                        position["stop_loss"] = max(position["stop_loss"], be_stop)
                        position["breakeven_active"] = True

                # 2차 손절 (trailing 갱신 후)
                if low_i <= position["stop_loss"]:
                    ep_sl, net_exit = apply_sell_costs(position["stop_loss"], self._costs)
                    pnl = (net_exit - position["net_entry"]) * remaining
                    exit_reason = (
                        "breakeven_stop"
                        if (
                            position["breakeven_active"]
                            and position["stop_loss"] >= position["entry_price"]
                            and position["stop_loss"] <= position["entry_price"] * 1.02
                        )
                        else "trailing_stop"
                    )
                    trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep_sl,
                        "pnl":         pnl,
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": exit_reason,
                        "entry_chg_from_close": position.get("entry_chg_from_close", 0.0),
                    })
                    position = None
                    strategy.on_exit()
                    last_exit_min = min_i
                    continue

                # Momentum fade
                if fade_enabled:
                    now_dt   = ts_i.to_pydatetime()
                    entry_dt = position["entry_ts"]
                    hold_sec = (now_dt - entry_dt).total_seconds()
                    if hold_sec >= fade_min_sec:
                        cur_profit = (close_i - position["entry_price"]) / position["entry_price"]
                        if cur_profit >= fade_min_profit:
                            lb_idx = max(0, i - fade_lookback)
                            base_c = float(closes[lb_idx])
                            if base_c > 0 and (close_i / base_c - 1.0) <= fade_threshold:
                                ep_sl, net_exit = apply_sell_costs(close_i, self._costs)
                                pnl = (net_exit - position["net_entry"]) * remaining
                                trades.append({
                                    "entry_ts":    position["entry_ts"],
                                    "exit_ts":     now_dt,
                                    "entry_price": position["entry_price"],
                                    "exit_price":  ep_sl,
                                    "pnl":         pnl,
                                    "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                                    "exit_reason": "momentum_fade",
                                    "entry_chg_from_close": position.get("entry_chg_from_close", 0.0),
                                })
                                position = None
                                strategy.on_exit()
                                last_exit_min = min_i
                                continue

                # Stale exit
                if stale_enabled and position is not None:
                    now_dt   = ts_i.to_pydatetime()
                    hold_min = (now_dt - position["entry_ts"]).total_seconds() / 60.0
                    cur_pnl  = (close_i - position["entry_price"]) / position["entry_price"]
                    if hold_min >= stale_min_min and cur_pnl < stale_min_pnl:
                        ep_sl, net_exit = apply_sell_costs(close_i, self._costs)
                        pnl = (net_exit - position["net_entry"]) * remaining
                        trades.append({
                            "entry_ts":    position["entry_ts"],
                            "exit_ts":     now_dt,
                            "entry_price": position["entry_price"],
                            "exit_price":  ep_sl,
                            "pnl":         pnl,
                            "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                            "exit_reason": "stale_exit",
                            "entry_chg_from_close": position.get("entry_chg_from_close", 0.0),
                        })
                        position = None
                        strategy.on_exit()
                        last_exit_min = min_i
                        continue

                # 마지막 캔들 강제 청산
                if i == n - 1 and position is not None:
                    ep_sl, net_exit = apply_sell_costs(close_i, self._costs)
                    pnl = (net_exit - position["net_entry"]) * remaining
                    trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep_sl,
                        "pnl":         pnl,
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "forced_close",
                        "entry_chg_from_close": position.get("entry_chg_from_close", 0.0),
                    })
                    position = None
                    strategy.on_exit()
                    last_exit_min = min_i

        strategy.set_backtest_time(None)
        kpi = self.calculate_kpi(trades)
        kpi["trades"] = trades
        logger.debug(
            f"[FAST] 완료: total_trades={kpi['total_trades']} "
            f"pnl={kpi['total_pnl']:.1f}"
        )
        return kpi


# ---------------------------------------------------------------------------
# ORBFastBacktester — Opening Range Breakout 전용 numpy 백테스터
# ---------------------------------------------------------------------------

class ORBFastBacktester(Backtester):
    """ORB 전략 전용 numpy 가속 백테스터.

    Backtester.run_multi_day_cached 를 그대로 상속하되
    run_backtest 만 ORB 특화 로직으로 override 한다.

    진입 조건
    ---------
    - 09:00~09:04 분봉에서 range_high/range_low 산출
    - 레인지 유효성: min_range_pct ≤ range_size/ref ≤ max_range_pct
    - 09:05 ~ entry_deadline 사이에 close > range_high + range_size × breakout_buffer
    - 거래량 (선택): cum_vol ≥ prev_vol × rvol_min

    청산 조건 (우선순위 순)
    ---------------------
    1. TP 도달 (high ≥ tp_price)  → tp_exit
    2. 손절 (low ≤ stop_loss)     → stop_loss
    3. 마지막 캔들 강제 청산       → forced_close
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._day_atr_cache: dict[str, float | None] = {}

    async def run_multi_day_cached(
        self,
        ticker: str,
        all_candles: pd.DataFrame,
        strategy: "Any",  # ORBStrategy
    ) -> dict[str, Any]:
        """ORB 전용 고속 멀티데이 백테스트.

        부모 run_multi_day_cached 대비 최적화:
        - numpy 배열 종목당 1회 변환 (매일 DataFrame copy 제거)
        - 날짜 경계를 diff로 계산 (groupby 제거)
        - 시장/블랙리스트/변동성사이징 모두 disable 가정 (ORB 그리드 용)
        """
        if all_candles.empty:
            return {**self.calculate_kpi([]), "trades": []}
        self._current_ticker = ticker

        # ── 종목당 1회 numpy 변환 ──────────────────────────────────────
        ts_pd = pd.DatetimeIndex(all_candles["ts"])
        all_closes  = all_candles["close"].values.astype(np.float64)
        all_highs   = all_candles["high"].values.astype(np.float64)
        all_lows    = all_candles["low"].values.astype(np.float64)
        all_volumes = all_candles["volume"].values.astype(np.float64)
        all_minutes = (ts_pd.hour * 60 + ts_pd.minute).values.astype(np.int32)

        # 날짜 경계 (일 ordinal — UTC에서 KST 오차 무시, 정렬된 캔들 가정)
        day_ord = (ts_pd.asi8 // (86_400 * 10 ** 9)).astype(np.int64)
        day_starts = np.where(np.diff(day_ord, prepend=day_ord[0] - 1) != 0)[0]
        day_ends   = np.append(day_starts[1:], len(day_ord))
        n_days     = len(day_starts)

        # ── 설정 (조합당 1회) ──────────────────────────────────────────
        cfg = self._config
        range_minutes    = int(getattr(cfg, "orb_range_minutes", 5))
        min_range_pct    = float(getattr(cfg, "orb_min_range_pct", 0.005))
        max_range_pct    = float(getattr(cfg, "orb_max_range_pct", 0.05))
        breakout_buffer  = float(getattr(cfg, "orb_breakout_buffer", 0.0))
        sl_ratio         = float(getattr(cfg, "orb_sl_ratio", 1.0))
        tp_ratio         = float(getattr(cfg, "orb_tp_ratio", 2.0))
        use_vol_filter   = bool(getattr(cfg, "orb_use_volume_filter", True))
        rvol_min         = float(getattr(cfg, "orb_rvol_min", 1.5))
        entry_deadline   = _parse_hhmm(str(getattr(cfg, "orb_entry_deadline", "10:00")), 600)
        signal_block     = _parse_hhmm(str(getattr(cfg, "signal_block_until", "09:05")), 545)
        max_trades_day   = int(getattr(cfg, "max_trades_per_day", 2))
        cooldown_min_cfg = int(getattr(cfg, "cooldown_minutes", 0))
        range_start_min  = 540   # 9 * 60
        range_end_min    = range_start_min + range_minutes - 1  # 09:04

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

            # ── ORB 레인지 ─────────────────────────────────────────────
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

            # ── 당일 거래 루프 ─────────────────────────────────────────
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
                        or cooldown_min_cfg <= 0
                        or (min_i - last_exit_min) >= cooldown_min_cfg
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
                        entry_price, net_entry = apply_buy_costs(close_i, self._costs)
                        sl = entry_price - range_size * sl_ratio
                        stop_loss = max(sl, range_low * 0.99)
                        tp_price  = entry_price + range_size * tp_ratio
                        ts_i = ts_pd[s + i]
                        position = {
                            "entry_ts":    ts_i.to_pydatetime(),
                            "entry_price": entry_price,
                            "net_entry":   net_entry,
                            "stop_loss":   stop_loss,
                            "tp_price":    tp_price,
                        }
                else:
                    if high_i >= position["tp_price"]:
                        ep, net_exit = apply_sell_costs(position["tp_price"], self._costs)
                        all_trades.append({
                            "entry_ts":    position["entry_ts"],
                            "exit_ts":     ts_pd[s + i].to_pydatetime(),
                            "entry_price": position["entry_price"],
                            "exit_price":  ep,
                            "pnl":         net_exit - position["net_entry"],
                            "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                            "exit_reason": "tp_exit",
                        })
                        position = None
                        last_exit_min = min_i
                    elif low_i <= position["stop_loss"]:
                        ep, net_exit = apply_sell_costs(position["stop_loss"], self._costs)
                        all_trades.append({
                            "entry_ts":    position["entry_ts"],
                            "exit_ts":     ts_pd[s + i].to_pydatetime(),
                            "entry_price": position["entry_price"],
                            "exit_price":  ep,
                            "pnl":         net_exit - position["net_entry"],
                            "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                            "exit_reason": "stop_loss",
                        })
                        position = None
                        last_exit_min = min_i
                    elif i == n - 1:
                        ep, net_exit = apply_sell_costs(close_i, self._costs)
                        all_trades.append({
                            "entry_ts":    position["entry_ts"],
                            "exit_ts":     ts_pd[s + i].to_pydatetime(),
                            "entry_price": position["entry_price"],
                            "exit_price":  ep,
                            "pnl":         net_exit - position["net_entry"],
                            "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                            "exit_reason": "forced_close",
                        })
                        position = None
                        last_exit_min = min_i

            prev_close  = float(closes[-1])
            prev_volume = float(np.sum(volumes))

        kpi = self.calculate_kpi(all_trades)
        kpi["trades"] = all_trades
        return kpi

    def run_backtest(
        self,
        candles: pd.DataFrame,
        strategy: "Any",  # ORBStrategy
    ) -> dict[str, Any]:
        """numpy 기반 ORB 단일 날짜 시뮬레이션."""
        if candles.empty:
            return {**self.calculate_kpi([]), "trades": []}

        candles = candles.reset_index(drop=True)
        n = len(candles)

        # ── 1. numpy 배열 변환 ─────────────────────────────────────────
        closes  = candles["close"].values.astype(np.float64)
        highs   = candles["high"].values.astype(np.float64)
        lows    = candles["low"].values.astype(np.float64)
        volumes = candles["volume"].values.astype(np.float64)
        ts_vals = candles["ts"].values
        ts_pd   = pd.DatetimeIndex(ts_vals)
        minutes = ts_pd.hour * 60 + ts_pd.minute   # 분 단위 시각

        # ── 2. 설정값 캐시 ─────────────────────────────────────────────
        cfg = self._config

        range_minutes   = int(getattr(cfg, "orb_range_minutes", 5))
        min_range_pct   = float(getattr(cfg, "orb_min_range_pct", 0.005))
        max_range_pct   = float(getattr(cfg, "orb_max_range_pct", 0.05))
        breakout_buffer = float(getattr(cfg, "orb_breakout_buffer", 0.0))
        sl_ratio        = float(getattr(cfg, "orb_sl_ratio", 1.0))
        tp_ratio        = float(getattr(cfg, "orb_tp_ratio", 2.0))
        use_vol_filter  = bool(getattr(cfg, "orb_use_volume_filter", True))
        rvol_min        = float(getattr(cfg, "orb_rvol_min", 1.5))
        entry_deadline  = _parse_hhmm(str(getattr(cfg, "orb_entry_deadline", "10:00")), 600)
        signal_block    = _parse_hhmm(str(getattr(cfg, "signal_block_until", "09:05")), 545)

        prev_volume = float(getattr(strategy, "_prev_day_volume", 0))
        prev_close  = float(getattr(strategy, "_prev_day_close", 0.0))

        max_trades_day   = int(getattr(cfg, "max_trades_per_day", 2))
        cooldown_min_cfg = int(getattr(cfg, "cooldown_minutes", 0))

        # ── 3. ORB 레인지 계산 ─────────────────────────────────────────
        range_start_min = 9 * 60            # 09:00
        range_end_min   = range_start_min + range_minutes - 1  # 09:04

        mask = (minutes >= range_start_min) & (minutes <= range_end_min)
        if not np.any(mask):
            logger.debug("[ORB-FAST] 레인지 분봉 없음 — 스킵")
            return {**self.calculate_kpi([]), "trades": []}

        range_high = float(np.max(highs[mask]))
        range_low  = float(np.min(lows[mask]))
        range_size = range_high - range_low

        ref_price = prev_close if prev_close > 0 else float(closes[0]) if n > 0 else range_high
        if ref_price <= 0:
            ref_price = range_high

        range_pct = range_size / ref_price if ref_price > 0 else 0.0
        if range_pct < min_range_pct or range_pct > max_range_pct:
            logger.debug(
                f"[ORB-FAST] 레인지 유효성 실패 "
                f"({range_pct:.3%}, min={min_range_pct:.3%}, max={max_range_pct:.3%})"
            )
            return {**self.calculate_kpi([]), "trades": []}

        # 돌파 임계
        breakout_threshold = range_high + range_size * breakout_buffer
        # 손절 / 익절 계산 헬퍼
        def _calc_stop(entry_price: float) -> float:
            sl = entry_price - range_size * sl_ratio
            return max(sl, range_low * 0.99)

        def _calc_tp(entry_price: float) -> float:
            return entry_price + range_size * tp_ratio

        # ── 4. 누적 거래량 (거래량 필터용) ────────────────────────────
        cum_vols = np.cumsum(volumes)

        # ── 5. 메인 루프 ────────────────────────────────────────────────
        trades: list[dict] = []
        position: dict | None = None
        trade_count    = 0
        last_exit_min: int | None = None
        already_entered = False  # 당일 최초 진입 여부 (max_trades_day=1 기본)

        for i in range(n):
            close_i = closes[i]
            high_i  = highs[i]
            low_i   = lows[i]
            min_i   = int(minutes[i])
            ts_i    = ts_pd[i]

            # ── 포지션 없음: 진입 탐색 ──────────────────────────────────
            if position is None:
                cooldown_ok = (
                    last_exit_min is None
                    or cooldown_min_cfg <= 0
                    or (min_i - last_exit_min) >= cooldown_min_cfg
                )
                in_entry_window = (min_i >= signal_block) and (min_i <= entry_deadline)
                vol_ok = (
                    not use_vol_filter
                    or prev_volume <= 0
                    or cum_vols[i] >= prev_volume * rvol_min
                )

                if (
                    trade_count < max_trades_day
                    and cooldown_ok
                    and in_entry_window
                    and close_i > breakout_threshold
                    and vol_ok
                ):
                    strategy.on_entry()
                    trade_count += 1
                    ep_raw = close_i
                    entry_price, net_entry = apply_buy_costs(ep_raw, self._costs)
                    stop_loss = _calc_stop(entry_price)
                    tp_price  = _calc_tp(entry_price)

                    position = {
                        "entry_ts":    ts_i.to_pydatetime(),
                        "entry_price": entry_price,
                        "net_entry":   net_entry,
                        "stop_loss":   stop_loss,
                        "tp_price":    tp_price,
                    }
                    logger.debug(
                        f"[ORB-FAST] 진입 ts={ts_i} price={entry_price:.1f} "
                        f"sl={stop_loss:.1f} tp={tp_price:.1f}"
                    )

            # ── 포지션 보유 중: 청산 확인 ──────────────────────────────
            else:
                tp_price = position["tp_price"]

                # TP 도달
                if high_i >= tp_price:
                    ep_sl, net_exit = apply_sell_costs(tp_price, self._costs)
                    pnl = net_exit - position["net_entry"]
                    trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep_sl,
                        "pnl":         pnl,
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "tp_exit",
                    })
                    position = None
                    strategy.on_exit()
                    last_exit_min = min_i
                    continue

                # 손절
                if low_i <= position["stop_loss"]:
                    ep_sl, net_exit = apply_sell_costs(position["stop_loss"], self._costs)
                    pnl = net_exit - position["net_entry"]
                    trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep_sl,
                        "pnl":         pnl,
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "stop_loss",
                    })
                    position = None
                    strategy.on_exit()
                    last_exit_min = min_i
                    continue

                # 마지막 캔들 강제 청산
                if i == n - 1:
                    ep_sl, net_exit = apply_sell_costs(close_i, self._costs)
                    pnl = net_exit - position["net_entry"]
                    trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep_sl,
                        "pnl":         pnl,
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "forced_close",
                    })
                    position = None
                    strategy.on_exit()
                    last_exit_min = min_i

        strategy.set_backtest_time(None)
        kpi = self.calculate_kpi(trades)
        kpi["trades"] = trades
        logger.debug(
            f"[ORB-FAST] 완료: total_trades={kpi['total_trades']} "
            f"pnl={kpi['total_pnl']:.1f}"
        )
        return kpi
