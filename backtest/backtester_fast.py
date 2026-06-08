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

        # TRVOL 설정 캐시
        trvol_enabled   = bool(getattr(cfg, "trvol_enabled", False))
        trvol_ratio_v   = float(getattr(cfg, "trvol_ratio", 3.0))
        trvol_min_prev  = int(getattr(cfg, "trvol_min_prev_volume", 1000))
        trvol_only_mode = bool(getattr(cfg, "trvol_only_mode", False))
        _prev_day_slot_vols: dict = getattr(strategy, "_prev_day_slot_vols", {})

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

        # TRVOL 슬롯 사전계산
        slot_hours = ts_pd.hour.values.astype(np.int32)
        slot_mins5 = (ts_pd.minute.values.astype(np.int32) // 5) * 5

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
        breakout_ts_day: "pd.Timestamp | None" = None
        trade_count    = 0
        last_exit_min: int | None = None   # 마지막 청산 분(분 단위 시각)

        # TRVOL 슬롯 추적 상태
        _cur_slot: tuple[int, int] = (-1, -1)
        _cur_slot_vol: float = 0.0

        for i in range(n):
            close_i = closes[i]
            high_i  = highs[i]
            low_i   = lows[i]
            min_i   = int(minutes[i])
            ts_i    = ts_pd[i]

            # TRVOL: 5분 슬롯 거래량 누적 (포지션 유무 무관하게 항상 갱신)
            if trvol_enabled:
                _slot_i = (int(slot_hours[i]), int(slot_mins5[i]))
                if _slot_i != _cur_slot:
                    _cur_slot = _slot_i
                    _cur_slot_vol = 0.0
                _cur_slot_vol += float(volumes[i])

            # ── 포지션 없음: 진입 신호 탐색 ─────────────────────────
            if position is None:
                # 당일 최초 돌파 가격 + 시각 추적 (고점 진입 방지 + 진입지연 계산)
                if breakout_price_day is None and high_i >= breakout_level:
                    breakout_price_day = breakout_level
                    breakout_ts_day = ts_i

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
                # 거래량 조건 (TRVOL 또는 cumvol)
                if trvol_enabled:
                    _sp_vol = _prev_day_slot_vols.get(_cur_slot, 0)
                    _trvol_ok = (
                        _sp_vol >= trvol_min_prev
                        and _cur_slot_vol >= _sp_vol * trvol_ratio_v
                    )
                    _vol_ok = _trvol_ok if trvol_only_mode else (
                        _trvol_ok or cum_vols[i] >= required_vol
                    )
                else:
                    _vol_ok = cum_vols[i] >= required_vol

                if (
                    trade_count < max_trades_day
                    and cooldown_ok
                    and prev_high > 0
                    and min_i >= signal_block_min
                    and (not buy_time_enabled or min_i < buy_time_end_min)
                    and close_i >= breakout_level
                    and _vol_ok
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
                                "breakout_ts":     breakout_ts_day.to_pydatetime() if breakout_ts_day is not None else None,
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
                        "breakout_ts": position.get("breakout_ts"),
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep_sl,
                        "pnl":         pnl,
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "limit_up_exit",
                        "highest_price": max(position.get("highest_price", 0.0), float(high_i)),
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
                        "breakout_ts": position.get("breakout_ts"),
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep_sl,
                        "pnl":         pnl,
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": exit_reason,
                        "highest_price": position.get("highest_price", 0.0),
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
                            "breakout_ts": position.get("breakout_ts"),
                            "exit_ts":     ts_i.to_pydatetime(),
                            "entry_price": position["entry_price"],
                            "exit_price":  ep_sl,
                            "pnl":         pnl,
                            "pnl_pct":     pnl / position["net_entry"],
                            "exit_reason": "forced_close",
                            "highest_price": position.get("highest_price", 0.0),
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
                        "breakout_ts": position.get("breakout_ts"),
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep_sl,
                        "pnl":         pnl,
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": exit_reason,
                        "highest_price": position.get("highest_price", 0.0),
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
                                    "breakout_ts": position.get("breakout_ts"),
                                    "exit_ts":     now_dt,
                                    "entry_price": position["entry_price"],
                                    "exit_price":  ep_sl,
                                    "pnl":         pnl,
                                    "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                                    "exit_reason": "momentum_fade",
                                    "highest_price": position.get("highest_price", 0.0),
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
                            "breakout_ts": position.get("breakout_ts"),
                            "exit_ts":     now_dt,
                            "entry_price": position["entry_price"],
                            "exit_price":  ep_sl,
                            "pnl":         pnl,
                            "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                            "exit_reason": "stale_exit",
                            "highest_price": position.get("highest_price", 0.0),
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
                        "breakout_ts": position.get("breakout_ts"),
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep_sl,
                        "pnl":         pnl,
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "forced_close",
                        "highest_price": position.get("highest_price", 0.0),
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
# MomentumTightSLFastBacktester — 고정 SL/TP 그리드 전용 numpy 백테스터
# ---------------------------------------------------------------------------

class MomentumTightSLFastBacktester(Backtester):
    """모멘텀 고정 SL/TP 백테스터 — grid_momentum_tight_sl.py 전용.

    FastBacktester와 동일한 진입 조건(전일고가 돌파·거래량·ADX·RVol·VWAP)을
    사용하되 청산 로직을 단순화한다.

    청산 모드
    ----------
    - trail_mode="off"       : 고정 SL + 고정 TP (tp_exit 또는 stop_loss, 미달 시 forced_close)
    - trail_mode="trail_1pct": 고정 초기 SL + 고점 대비 1% 단순 트레일링 (TP 없음)
    - trail_mode="trail_1.5pct": 고점 대비 1.5% 트레일링 (TP 없음)

    비활성 항목
    -----------
    ATR 비례 손절, Chandelier 트레일링, time_decay, breakeven, momentum_fade,
    stale_exit, limit_up_exit — 이 클래스에서는 읽지 않음.
    """

    def run_backtest(
        self,
        candles: "pd.DataFrame",
        strategy: "BaseStrategy",
    ) -> "dict[str, Any]":
        """고정 SL/TP numpy 시뮬레이션."""
        if candles.empty:
            return {**self.calculate_kpi([]), "trades": []}

        candles = candles.reset_index(drop=True)
        n = len(candles)

        # ── 1. numpy 배열 변환 ───────────────────────────────────────────
        closes  = candles["close"].values.astype(np.float64)
        highs   = candles["high"].values.astype(np.float64)
        lows    = candles["low"].values.astype(np.float64)
        volumes = candles["volume"].values.astype(np.float64)
        ts_vals = candles["ts"].values
        ts_pd   = pd.DatetimeIndex(ts_vals)
        minutes = ts_pd.hour * 60 + ts_pd.minute

        # ── 2. 사전계산 ─────────────────────────────────────────────────
        cum_vols     = np.cumsum(volumes)
        tp_arr       = (highs + lows + closes) / 3.0
        cum_tp_vol   = np.cumsum(tp_arr * volumes)
        cum_vol_safe = np.where(cum_vols > 0, cum_vols, 1.0)
        vwap_arr     = cum_tp_vol / cum_vol_safe
        _adx_window  = int(self._config.adx_length) + 20

        # ── 3. 설정값 캐시 ──────────────────────────────────────────────
        cfg = self._config

        prev_high      = float(getattr(strategy, "_prev_day_high",   0.0))
        prev_volume    = float(getattr(strategy, "_prev_day_volume", 0))
        prev_close_day = float(getattr(strategy, "_prev_day_close",  0.0))

        min_bp            = float(getattr(cfg, "min_breakout_pct", 0.0))
        vol_ratio         = float(cfg.momentum_volume_ratio)
        adx_min           = float(cfg.adx_min)
        adx_len           = int(cfg.adx_length)
        min_bars_adx      = adx_len + 20
        rvol_win          = int(cfg.rvol_window)
        rvol_min_v        = float(cfg.rvol_min)
        vwap_min_above    = float(cfg.vwap_min_above)
        max_entry_gap     = float(getattr(cfg, "max_entry_above_breakout_pct", 0.10))
        max_close_pct_raw = float(getattr(cfg, "max_entry_above_close_pct", 999.0))

        signal_block_min = _parse_hhmm(cfg.signal_block_until, 545)
        buy_time_end_min = _parse_hhmm(cfg.buy_time_end, 720)
        buy_time_enabled = bool(getattr(cfg, "buy_time_limit_enabled", True))

        tight_sl_pct     = float(getattr(cfg, "tight_sl_pct",    0.010))
        tight_tp_pct     = float(getattr(cfg, "tight_tp_pct",    0.020))
        tight_trail_mode = str(getattr(cfg,  "tight_trail_mode", "off"))

        trail_pct = 0.0
        if tight_trail_mode == "trail_1pct":
            trail_pct = 0.010
        elif tight_trail_mode == "trail_1.5pct":
            trail_pct = 0.015

        breakout_level = prev_high * (1.0 + min_bp)
        required_vol   = prev_volume * vol_ratio

        max_trades_day   = int(getattr(cfg, "max_trades_per_day", 1))
        cooldown_min_cfg = int(getattr(cfg, "cooldown_minutes",   0))

        # ── 4. 메인 루프 ────────────────────────────────────────────────
        trades: list[dict] = []
        position: dict | None = None
        breakout_price_day: float | None = None
        trade_count    = 0
        last_exit_min: int | None = None

        for i in range(n):
            close_i = closes[i]
            high_i  = highs[i]
            low_i   = lows[i]
            min_i   = int(minutes[i])
            ts_i    = ts_pd[i]

            # ── 포지션 없음: 진입 신호 탐색 ─────────────────────────────
            if position is None:
                if breakout_price_day is None and high_i >= breakout_level:
                    breakout_price_day = breakout_level

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
                    bp        = breakout_price_day if breakout_price_day is not None else breakout_level
                    entry_gap = (close_i - bp) / bp if bp > 0 else 0.0

                    if entry_gap <= max_entry_gap:
                        # ADX
                        adx_ok = True
                        if cfg.adx_enabled:
                            if i < min_bars_adx - 1:
                                adx_ok = False
                            else:
                                ws = max(0, i - _adx_window + 1)
                                a  = _wilder_adx_numpy(
                                    highs[ws:i + 1], lows[ws:i + 1], closes[ws:i + 1],
                                    self._config.adx_length,
                                )
                                av     = a[-1] if len(a) > 0 else np.nan
                                adx_ok = not np.isnan(av) and av >= adx_min

                        # RVol
                        rvol_ok = True
                        if cfg.rvol_enabled:
                            if i < rvol_win + 9:
                                rvol_ok = False
                            else:
                                recent_sum = float(np.sum(volumes[i - rvol_win + 1:i + 1]))
                                prev_arr   = volumes[:i - rvol_win + 1]
                                avg_v      = float(np.mean(prev_arr)) if len(prev_arr) > 0 else 0.0
                                rvol_ok    = avg_v > 0 and (recent_sum / (avg_v * rvol_win)) >= rvol_min_v

                        # VWAP
                        vwap_ok = True
                        if cfg.vwap_enabled:
                            if i < 9:
                                vwap_ok = False
                            else:
                                vwap_ok = close_i >= vwap_arr[i] * (1.0 + vwap_min_above)

                        # 마지막 종가 돌파 재확인
                        last_bp_ok = (
                            prev_high > 0
                            and (close_i - prev_high) / prev_high >= min_bp
                        )

                        if adx_ok and rvol_ok and vwap_ok and last_bp_ok:
                            strategy.on_entry()
                            trade_count += 1
                            entry_price, net_entry = apply_buy_costs(close_i, self._costs)
                            stop_loss = entry_price * (1.0 - tight_sl_pct)
                            tp_price  = (
                                entry_price * (1.0 + tight_tp_pct)
                                if tight_trail_mode == "off"
                                else None
                            )

                            position = {
                                "entry_ts":    ts_i.to_pydatetime(),
                                "entry_price": entry_price,
                                "net_entry":   net_entry,
                                "stop_loss":   stop_loss,
                                "tp_price":    tp_price,
                                "highest_price": float(high_i),
                                "entry_chg_from_close": (
                                    (entry_price - prev_close_day) / prev_close_day
                                    if prev_close_day > 0 else 0.0
                                ),
                            }

            # ── 포지션 보유 중: 청산 확인 ───────────────────────────────
            else:
                # 1. TP 확인 (trail_mode="off" 전용, 고가 기준)
                if tight_trail_mode == "off" and position["tp_price"] is not None:
                    if high_i >= position["tp_price"]:
                        ep_tp, net_exit = apply_sell_costs(position["tp_price"], self._costs)
                        pnl = net_exit - position["net_entry"]
                        trades.append({
                            "entry_ts":    position["entry_ts"],
                            "exit_ts":     ts_i.to_pydatetime(),
                            "entry_price": position["entry_price"],
                            "exit_price":  ep_tp,
                            "pnl":         pnl,
                            "pnl_pct":     pnl / position["net_entry"],
                            "exit_reason": "tp_exit",
                            "entry_chg_from_close": position.get("entry_chg_from_close", 0.0),
                        })
                        position = None
                        strategy.on_exit()
                        last_exit_min = min_i
                        continue

                # 2. SL 확인 (트레일 업데이트 전)
                if low_i <= position["stop_loss"]:
                    ep_sl, net_exit = apply_sell_costs(position["stop_loss"], self._costs)
                    pnl = net_exit - position["net_entry"]
                    exit_reason = (
                        "trailing_stop"
                        if position["stop_loss"] >= position["entry_price"]
                        else "stop_loss"
                    )
                    trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep_sl,
                        "pnl":         pnl,
                        "pnl_pct":     pnl / position["net_entry"],
                        "exit_reason": exit_reason,
                        "entry_chg_from_close": position.get("entry_chg_from_close", 0.0),
                    })
                    position = None
                    strategy.on_exit()
                    last_exit_min = min_i
                    continue

                # 3. 트레일 업데이트 + 재확인 (trail_mode != "off")
                if tight_trail_mode != "off" and trail_pct > 0:
                    if high_i > position["highest_price"]:
                        position["highest_price"] = float(high_i)
                        new_stop = position["highest_price"] * (1.0 - trail_pct)
                        position["stop_loss"] = max(position["stop_loss"], new_stop)

                    if low_i <= position["stop_loss"]:
                        ep_sl, net_exit = apply_sell_costs(position["stop_loss"], self._costs)
                        pnl = net_exit - position["net_entry"]
                        exit_reason = (
                            "trailing_stop"
                            if position["stop_loss"] >= position["entry_price"]
                            else "stop_loss"
                        )
                        trades.append({
                            "entry_ts":    position["entry_ts"],
                            "exit_ts":     ts_i.to_pydatetime(),
                            "entry_price": position["entry_price"],
                            "exit_price":  ep_sl,
                            "pnl":         pnl,
                            "pnl_pct":     pnl / position["net_entry"],
                            "exit_reason": exit_reason,
                            "entry_chg_from_close": position.get("entry_chg_from_close", 0.0),
                        })
                        position = None
                        strategy.on_exit()
                        last_exit_min = min_i
                        continue

                # 4. 마지막 캔들 강제 청산
                if i == n - 1 and position is not None:
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

        strategy.set_backtest_time(None)
        kpi = self.calculate_kpi(trades)
        kpi["trades"] = trades
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


# ---------------------------------------------------------------------------
# VWAPReversionFastBacktester — VWAP 리버전 전략 전용 numpy 백테스터
# ---------------------------------------------------------------------------

class VWAPReversionFastBacktester(Backtester):
    """VWAP 리버전 전략 전용 numpy 가속 백테스터.

    진입 조건
    ---------
    - 진입 시간: entry_start(09:30) ~ entry_end(14:00)
    - 가격 조건: close <= VWAP × (1 + entry_deviation)  [entry_deviation < 0]
    - 거래량 조건: 현재 분봉 거래량 >= 당일 평균 분봉 거래량 (이전 바 기준)
    - 일봉 필터: 당일 시가 등락률 > max_daily_drop (-7%)
    - 전일 거래량 필터: prev_volume >= min_prev_volume (50,000주, 데이터 있을 때만)

    청산 조건 (우선순위)
    ---------------------
    1. TP 도달: high >= VWAP × (1 + tp_above_vwap)  → exit_reason="vwap_exit"
    2. 손절:   low  <= entry × (1 - stop_loss_pct)  → exit_reason="stop_loss"
    3. 강제 청산: force_close_time(15:10) 또는 마지막 캔들 → exit_reason="forced_close"
    """

    async def run_multi_day_cached(
        self,
        ticker: str,
        all_candles: pd.DataFrame,
        strategy: "Any" = None,
    ) -> dict[str, Any]:
        """VWAP 리버전 멀티데이 고속 백테스트.

        종목당 1회 numpy 변환, 날짜 경계 diff 계산.
        """
        if all_candles.empty:
            return {**self.calculate_kpi([]), "trades": []}
        self._current_ticker = ticker

        # ── 종목당 1회 numpy 변환 ─────────────────────────────────────────
        ts_pd       = pd.DatetimeIndex(all_candles["ts"])
        all_closes  = all_candles["close"].values.astype(np.float64)
        all_highs   = all_candles["high"].values.astype(np.float64)
        all_lows    = all_candles["low"].values.astype(np.float64)
        all_volumes = all_candles["volume"].values.astype(np.float64)
        all_opens   = all_candles["open"].values.astype(np.float64)
        all_minutes = (ts_pd.hour * 60 + ts_pd.minute).values.astype(np.int32)

        # 날짜 경계
        day_ord    = (ts_pd.asi8 // (86_400 * 10 ** 9)).astype(np.int64)
        day_starts = np.where(np.diff(day_ord, prepend=day_ord[0] - 1) != 0)[0]
        day_ends   = np.append(day_starts[1:], len(day_ord))
        n_days     = len(day_starts)

        # ── 설정값 캐시 (조합당 1회) ─────────────────────────────────────
        cfg = self._config
        entry_deviation  = float(getattr(cfg, "vwap_rev_entry_deviation", -0.015))
        stop_loss_pct    = float(getattr(cfg, "vwap_rev_stop_loss_pct",   0.015))
        tp_above_vwap    = float(getattr(cfg, "vwap_rev_tp_above_vwap",   0.003))
        entry_start_min  = _parse_hhmm(str(getattr(cfg, "vwap_rev_entry_start", "09:30")), 570)
        entry_end_min    = _parse_hhmm(str(getattr(cfg, "vwap_rev_entry_end",   "14:00")), 840)
        min_prev_volume  = float(getattr(cfg, "vwap_rev_min_prev_volume", 50000))
        max_daily_drop   = float(getattr(cfg, "vwap_rev_max_daily_drop",  -0.07))
        max_trades_day   = int(getattr(cfg, "max_trades_per_day", 2))
        cooldown_min_cfg = int(getattr(cfg, "cooldown_minutes",   0))
        force_close_min  = _parse_hhmm(str(getattr(cfg, "force_close_time", "15:10")), 910)

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
            opens   = all_opens[s:e]
            mins    = all_minutes[s:e]
            n       = e - s

            if n == 0:
                continue

            # ── 전일 거래량 필터 (데이터 있을 때만) ───────────────────────
            if min_prev_volume > 0 and prev_volume > 0 and prev_volume < min_prev_volume:
                prev_close  = float(closes[-1])
                prev_volume = float(np.sum(volumes))
                continue

            # ── 당일 등락률 필터 (전일 종가 대비 시가) ────────────────────
            if prev_close > 0 and n > 0:
                today_open = float(opens[0])
                if today_open > 0:
                    daily_chg = (today_open - prev_close) / prev_close
                    if daily_chg < max_daily_drop:
                        prev_close  = float(closes[-1])
                        prev_volume = float(np.sum(volumes))
                        continue

            # ── 당일 누적 VWAP 배열 ───────────────────────────────────────
            tp       = (highs + lows + closes) / 3.0
            cum_vols = np.cumsum(volumes)
            cum_tp_v = np.cumsum(tp * volumes)
            safe_v   = np.where(cum_vols > 0, cum_vols, 1.0)
            vwap_arr = cum_tp_v / safe_v

            # ── 당일 거래 루프 ────────────────────────────────────────────
            position: dict | None = None
            trade_count    = 0
            last_exit_min: int | None = None

            for i in range(n):
                close_i = closes[i]
                high_i  = highs[i]
                low_i   = lows[i]
                vol_i   = volumes[i]
                min_i   = int(mins[i])
                vwap_i  = float(vwap_arr[i])
                ts_i    = ts_pd[s + i]

                # ── 강제 청산 시간 도달 ────────────────────────────────────
                if position is not None and min_i >= force_close_min:
                    ep, net_exit = apply_sell_costs(close_i, self._costs)
                    all_trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep,
                        "pnl":         net_exit - position["net_entry"],
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "forced_close",
                    })
                    position = None
                    last_exit_min = min_i
                    continue

                if position is None:
                    cooldown_ok = (
                        last_exit_min is None
                        or cooldown_min_cfg <= 0
                        or (min_i - last_exit_min) >= cooldown_min_cfg
                    )
                    in_window = (entry_start_min <= min_i <= entry_end_min)

                    # 거래량 필터: 현재 분봉 거래량 >= 오늘 이전 바 평균 거래량
                    if i > 0 and cum_vols[i - 1] > 0:
                        avg_bar_vol = cum_vols[i - 1] / i
                        vol_ok = vol_i >= avg_bar_vol
                    else:
                        vol_ok = True

                    # 가격 조건: close <= VWAP × (1 + entry_deviation)
                    price_ok = vwap_i > 0 and close_i <= vwap_i * (1.0 + entry_deviation)

                    if (
                        trade_count < max_trades_day
                        and cooldown_ok
                        and in_window
                        and price_ok
                        and vol_ok
                    ):
                        trade_count += 1
                        entry_price, net_entry = apply_buy_costs(close_i, self._costs)
                        stop_loss = entry_price * (1.0 - stop_loss_pct)
                        position = {
                            "entry_ts":    ts_i.to_pydatetime(),
                            "entry_price": entry_price,
                            "net_entry":   net_entry,
                            "stop_loss":   stop_loss,
                        }
                        logger.debug(
                            f"[VWAP-FAST] 진입 {ts_i} price={entry_price:.1f} "
                            f"sl={stop_loss:.1f} vwap={vwap_i:.1f}"
                        )

                else:
                    # TP: high >= VWAP × (1 + tp_above_vwap) — 동적 TP
                    tp_target = vwap_i * (1.0 + tp_above_vwap)
                    if high_i >= tp_target:
                        ep, net_exit = apply_sell_costs(tp_target, self._costs)
                        all_trades.append({
                            "entry_ts":    position["entry_ts"],
                            "exit_ts":     ts_i.to_pydatetime(),
                            "entry_price": position["entry_price"],
                            "exit_price":  ep,
                            "pnl":         net_exit - position["net_entry"],
                            "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                            "exit_reason": "vwap_exit",
                        })
                        position = None
                        last_exit_min = min_i
                        continue

                    # SL: low <= entry × (1 - stop_loss_pct)
                    if low_i <= position["stop_loss"]:
                        ep, net_exit = apply_sell_costs(position["stop_loss"], self._costs)
                        all_trades.append({
                            "entry_ts":    position["entry_ts"],
                            "exit_ts":     ts_i.to_pydatetime(),
                            "entry_price": position["entry_price"],
                            "exit_price":  ep,
                            "pnl":         net_exit - position["net_entry"],
                            "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                            "exit_reason": "stop_loss",
                        })
                        position = None
                        last_exit_min = min_i
                        continue

                    # 마지막 캔들 강제 청산
                    if i == n - 1:
                        ep, net_exit = apply_sell_costs(close_i, self._costs)
                        all_trades.append({
                            "entry_ts":    position["entry_ts"],
                            "exit_ts":     ts_i.to_pydatetime(),
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
        logger.debug(
            f"[VWAP-FAST] {ticker} 완료: trades={kpi['total_trades']} "
            f"pnl={kpi['total_pnl']:.1f}"
        )
        return kpi

    def run_backtest(
        self,
        candles: pd.DataFrame,
        strategy: "Any" = None,
    ) -> dict[str, Any]:
        """numpy 기반 VWAP 리버전 단일 날짜 시뮬레이션."""
        if candles.empty:
            return {**self.calculate_kpi([]), "trades": []}

        candles = candles.reset_index(drop=True)
        n = len(candles)

        ts_pd   = pd.DatetimeIndex(candles["ts"].values)
        closes  = candles["close"].values.astype(np.float64)
        highs   = candles["high"].values.astype(np.float64)
        lows    = candles["low"].values.astype(np.float64)
        volumes = candles["volume"].values.astype(np.float64)
        opens   = candles["open"].values.astype(np.float64)
        minutes = (ts_pd.hour * 60 + ts_pd.minute).values.astype(np.int32)

        # 당일 누적 VWAP
        tp       = (highs + lows + closes) / 3.0
        cum_vols = np.cumsum(volumes)
        cum_tp_v = np.cumsum(tp * volumes)
        safe_v   = np.where(cum_vols > 0, cum_vols, 1.0)
        vwap_arr = cum_tp_v / safe_v

        cfg = self._config
        entry_deviation  = float(getattr(cfg, "vwap_rev_entry_deviation", -0.015))
        stop_loss_pct    = float(getattr(cfg, "vwap_rev_stop_loss_pct",   0.015))
        tp_above_vwap    = float(getattr(cfg, "vwap_rev_tp_above_vwap",   0.003))
        entry_start_min  = _parse_hhmm(str(getattr(cfg, "vwap_rev_entry_start", "09:30")), 570)
        entry_end_min    = _parse_hhmm(str(getattr(cfg, "vwap_rev_entry_end",   "14:00")), 840)
        max_trades_day   = int(getattr(cfg, "max_trades_per_day", 2))
        cooldown_min_cfg = int(getattr(cfg, "cooldown_minutes",   0))
        force_close_min  = _parse_hhmm(str(getattr(cfg, "force_close_time", "15:10")), 910)

        trades: list[dict] = []
        position: dict | None = None
        trade_count    = 0
        last_exit_min: int | None = None

        for i in range(n):
            close_i = closes[i]
            high_i  = highs[i]
            low_i   = lows[i]
            vol_i   = volumes[i]
            min_i   = int(minutes[i])
            vwap_i  = float(vwap_arr[i])
            ts_i    = ts_pd[i]

            if position is not None and min_i >= force_close_min:
                ep, net_exit = apply_sell_costs(close_i, self._costs)
                trades.append({
                    "entry_ts":    position["entry_ts"],
                    "exit_ts":     ts_i.to_pydatetime(),
                    "entry_price": position["entry_price"],
                    "exit_price":  ep,
                    "pnl":         net_exit - position["net_entry"],
                    "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                    "exit_reason": "forced_close",
                })
                position = None
                last_exit_min = min_i
                continue

            if position is None:
                cooldown_ok = (
                    last_exit_min is None
                    or cooldown_min_cfg <= 0
                    or (min_i - last_exit_min) >= cooldown_min_cfg
                )
                in_window  = (entry_start_min <= min_i <= entry_end_min)
                if i > 0 and cum_vols[i - 1] > 0:
                    avg_bar_vol = cum_vols[i - 1] / i
                    vol_ok = vol_i >= avg_bar_vol
                else:
                    vol_ok = True
                price_ok = vwap_i > 0 and close_i <= vwap_i * (1.0 + entry_deviation)

                if (
                    trade_count < max_trades_day
                    and cooldown_ok
                    and in_window
                    and price_ok
                    and vol_ok
                ):
                    trade_count += 1
                    entry_price, net_entry = apply_buy_costs(close_i, self._costs)
                    stop_loss = entry_price * (1.0 - stop_loss_pct)
                    position = {
                        "entry_ts":    ts_i.to_pydatetime(),
                        "entry_price": entry_price,
                        "net_entry":   net_entry,
                        "stop_loss":   stop_loss,
                    }
                    if strategy is not None:
                        strategy.on_entry()
            else:
                tp_target = vwap_i * (1.0 + tp_above_vwap)
                if high_i >= tp_target:
                    ep, net_exit = apply_sell_costs(tp_target, self._costs)
                    trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep,
                        "pnl":         net_exit - position["net_entry"],
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "vwap_exit",
                    })
                    position = None
                    last_exit_min = min_i
                    if strategy is not None:
                        strategy.on_exit()
                    continue

                if low_i <= position["stop_loss"]:
                    ep, net_exit = apply_sell_costs(position["stop_loss"], self._costs)
                    trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep,
                        "pnl":         net_exit - position["net_entry"],
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "stop_loss",
                    })
                    position = None
                    last_exit_min = min_i
                    if strategy is not None:
                        strategy.on_exit()
                    continue

                if i == n - 1:
                    ep, net_exit = apply_sell_costs(close_i, self._costs)
                    trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep,
                        "pnl":         net_exit - position["net_entry"],
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "forced_close",
                    })
                    position = None
                    last_exit_min = min_i
                    if strategy is not None:
                        strategy.on_exit()

        if strategy is not None:
            strategy.set_backtest_time(None)
        kpi = self.calculate_kpi(trades)
        kpi["trades"] = trades
        logger.debug(
            f"[VWAP-FAST] 단일 완료: trades={kpi['total_trades']} "
            f"pnl={kpi['total_pnl']:.1f}"
        )
        return kpi


# ---------------------------------------------------------------------------
# PullbackFastBacktester — 눌림목 전략 전용 numpy 백테스터
# ---------------------------------------------------------------------------

class PullbackFastBacktester(Backtester):
    """눌림목 전략 전용 numpy 가속 백테스터.

    진입 조건
    ---------
    - 당일 09:05 이후 전일종가 대비 surge_pct 이상 상승한 적 있음 (급등 감지)
    - 급등 이후 close ≤ day_high × (1 - pullback_depth) (눌림 확인)
    - close > prev_close × (1 + min_above_close_pct) (추세 유지)
    - close ≥ closes[i-1] (양봉, 반등 확인)
    - entry_start ≤ min_i ≤ entry_end
    - prev_volume ≥ min_volume

    청산 조건 (우선순위 순)
    ---------------------
    1. TP 도달 (high ≥ tp_price = day_high × (1+tp_above_high)) → tp_exit
    2. 손절 (low ≤ stop_loss = day_high × (1-sl_from_high))     → stop_loss
    3. 마지막 캔들 강제 청산                                      → forced_close
    """

    async def run_multi_day_cached(
        self,
        ticker: str,
        all_candles: pd.DataFrame,
        strategy: "Any",
    ) -> dict[str, Any]:
        """눌림목 고속 멀티데이 백테스트."""
        if all_candles.empty:
            return {**self.calculate_kpi([]), "trades": []}

        ts_pd       = pd.DatetimeIndex(all_candles["ts"])
        all_closes  = all_candles["close"].values.astype(np.float64)
        all_highs   = all_candles["high"].values.astype(np.float64)
        all_lows    = all_candles["low"].values.astype(np.float64)
        all_volumes = all_candles["volume"].values.astype(np.float64)
        all_minutes = (ts_pd.hour * 60 + ts_pd.minute).values.astype(np.int32)

        day_ord    = (ts_pd.asi8 // (86_400 * 10 ** 9)).astype(np.int64)
        day_starts = np.where(np.diff(day_ord, prepend=day_ord[0] - 1) != 0)[0]
        day_ends   = np.append(day_starts[1:], len(day_ord))
        n_days     = len(day_starts)

        cfg = self._config
        surge_pct       = float(getattr(cfg, "pb_surge_pct", 0.05))
        pullback_depth  = float(getattr(cfg, "pb_pullback_depth", 0.02))
        min_above_close = float(getattr(cfg, "pb_min_above_close_pct", 0.01))
        sl_from_high    = float(getattr(cfg, "pb_sl_from_high_pct", 0.05))
        tp_above_high   = float(getattr(cfg, "pb_tp_above_high_pct", 0.01))
        entry_start_min = _parse_hhmm(str(getattr(cfg, "pb_entry_start", "09:30")), 570)
        entry_end_min   = _parse_hhmm(str(getattr(cfg, "pb_entry_end", "13:00")), 780)
        min_volume      = int(getattr(cfg, "pb_min_volume", 50000))
        signal_block    = _parse_hhmm(str(getattr(cfg, "signal_block_until", "09:05")), 545)

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

            if n == 0:
                prev_close  = 0.0
                prev_volume = 0.0
                continue

            vol_ok = prev_volume >= min_volume

            position: dict | None = None
            surge_detected  = False
            day_high_val    = 0.0
            trade_count     = 0

            for i in range(n):
                close_i = closes[i]
                high_i  = highs[i]
                low_i   = lows[i]
                min_i   = int(mins[i])

                # 급등 추적 (09:05 이후)
                if min_i >= signal_block and prev_close > 0:
                    if high_i > prev_close * (1.0 + surge_pct):
                        surge_detected = True
                    if surge_detected and high_i > day_high_val:
                        day_high_val = float(high_i)

                if position is None:
                    if (
                        surge_detected
                        and vol_ok
                        and trade_count < 1
                        and entry_start_min <= min_i <= entry_end_min
                        and day_high_val > 0
                        and close_i <= day_high_val * (1.0 - pullback_depth)
                        and prev_close > 0
                        and close_i > prev_close * (1.0 + min_above_close)
                        and (i == 0 or close_i >= closes[i - 1])
                    ):
                        trade_count += 1
                        entry_price, net_entry = apply_buy_costs(close_i, self._costs)
                        stop_loss = day_high_val * (1.0 - sl_from_high)
                        tp_price  = day_high_val * (1.0 + tp_above_high)
                        position = {
                            "entry_ts":    ts_pd[s + i].to_pydatetime(),
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

            prev_close  = float(closes[-1])
            prev_volume = float(np.sum(volumes))

        kpi = self.calculate_kpi(all_trades)
        kpi["trades"] = all_trades
        return kpi

    def run_backtest(
        self,
        candles: pd.DataFrame,
        strategy: "Any" = None,
    ) -> dict[str, Any]:
        """numpy 기반 눌림목 단일 날짜 시뮬레이션."""
        if candles.empty:
            return {**self.calculate_kpi([]), "trades": []}

        candles = candles.reset_index(drop=True)
        n = len(candles)

        ts_pd   = pd.DatetimeIndex(candles["ts"].values)
        closes  = candles["close"].values.astype(np.float64)
        highs   = candles["high"].values.astype(np.float64)
        lows    = candles["low"].values.astype(np.float64)
        minutes = (ts_pd.hour * 60 + ts_pd.minute).values.astype(np.int32)

        cfg = self._config
        surge_pct       = float(getattr(cfg, "pb_surge_pct", 0.05))
        pullback_depth  = float(getattr(cfg, "pb_pullback_depth", 0.02))
        min_above_close = float(getattr(cfg, "pb_min_above_close_pct", 0.01))
        sl_from_high    = float(getattr(cfg, "pb_sl_from_high_pct", 0.05))
        tp_above_high   = float(getattr(cfg, "pb_tp_above_high_pct", 0.01))
        entry_start_min = _parse_hhmm(str(getattr(cfg, "pb_entry_start", "09:30")), 570)
        entry_end_min   = _parse_hhmm(str(getattr(cfg, "pb_entry_end", "13:00")), 780)
        min_volume      = int(getattr(cfg, "pb_min_volume", 50000))
        signal_block    = _parse_hhmm(str(getattr(cfg, "signal_block_until", "09:05")), 545)

        prev_close  = float(getattr(strategy, "_prev_day_close", 0.0)) if strategy else 0.0
        prev_volume = float(getattr(strategy, "_prev_day_volume", 0)) if strategy else 0.0

        vol_ok = prev_volume >= min_volume

        trades: list[dict] = []
        position: dict | None = None
        surge_detected = False
        day_high_val   = 0.0
        trade_count    = 0

        for i in range(n):
            close_i = closes[i]
            high_i  = highs[i]
            low_i   = lows[i]
            min_i   = int(minutes[i])
            ts_i    = ts_pd[i]

            if min_i >= signal_block and prev_close > 0:
                if high_i > prev_close * (1.0 + surge_pct):
                    surge_detected = True
                if surge_detected and high_i > day_high_val:
                    day_high_val = float(high_i)

            if position is None:
                if (
                    surge_detected
                    and vol_ok
                    and trade_count < 1
                    and entry_start_min <= min_i <= entry_end_min
                    and day_high_val > 0
                    and close_i <= day_high_val * (1.0 - pullback_depth)
                    and prev_close > 0
                    and close_i > prev_close * (1.0 + min_above_close)
                    and (i == 0 or close_i >= closes[i - 1])
                ):
                    trade_count += 1
                    entry_price, net_entry = apply_buy_costs(close_i, self._costs)
                    stop_loss = day_high_val * (1.0 - sl_from_high)
                    tp_price  = day_high_val * (1.0 + tp_above_high)
                    if strategy is not None:
                        strategy.on_entry()
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
                    trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep,
                        "pnl":         net_exit - position["net_entry"],
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "tp_exit",
                    })
                    position = None
                    if strategy is not None:
                        strategy.on_exit()
                elif low_i <= position["stop_loss"]:
                    ep, net_exit = apply_sell_costs(position["stop_loss"], self._costs)
                    trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep,
                        "pnl":         net_exit - position["net_entry"],
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "stop_loss",
                    })
                    position = None
                    if strategy is not None:
                        strategy.on_exit()
                elif i == n - 1:
                    ep, net_exit = apply_sell_costs(close_i, self._costs)
                    trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep,
                        "pnl":         net_exit - position["net_entry"],
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "forced_close",
                    })
                    position = None
                    if strategy is not None:
                        strategy.on_exit()

        kpi = self.calculate_kpi(trades)
        kpi["trades"] = trades
        return kpi


# ---------------------------------------------------------------------------
# VolumeSpikeBacktester — 거래량 폭발 전략 전용 numpy 백테스터
# ---------------------------------------------------------------------------

class VolumeSpikeBacktester(Backtester):
    """거래량 폭발 전략 전용 numpy 가속 백테스터.

    진입 조건
    ---------
    - 진입 시간: entry_start(09:30) ~ entry_end(13:00)
    - 거래량 폭발: volumes[i] >= mean(volumes[i-lookback:i]) * spike_ratio
    - 절대 거래량: volumes[i] >= min_spike_volume
    - 양봉: close > open
    - 전일 거래량: prev_volume >= min_prev_volume
    - 당일 1회만 진입

    청산 조건 (우선순위 순)
    ---------------------
    1. TP 도달 (high >= tp_price = entry * (1+tp_pct))   → tp_exit
    2. 손절 (low  <= stop_loss = entry * (1-sl_pct))     → stop_loss
    3. 마지막 캔들 강제 청산                               → forced_close
    """

    async def run_multi_day_cached(
        self,
        ticker: str,
        all_candles: pd.DataFrame,
        strategy: "Any" = None,
    ) -> dict[str, Any]:
        """거래량 폭발 멀티데이 고속 백테스트."""
        if all_candles.empty:
            return {**self.calculate_kpi([]), "trades": []}
        self._current_ticker = ticker

        # ── 종목당 1회 numpy 변환 ────────────────────────────────────────────
        ts_pd       = pd.DatetimeIndex(all_candles["ts"])
        all_closes  = all_candles["close"].values.astype(np.float64)
        all_highs   = all_candles["high"].values.astype(np.float64)
        all_lows    = all_candles["low"].values.astype(np.float64)
        all_volumes = all_candles["volume"].values.astype(np.float64)
        all_opens   = all_candles["open"].values.astype(np.float64)
        all_minutes = (ts_pd.hour * 60 + ts_pd.minute).values.astype(np.int32)

        # 날짜 경계
        day_ord    = (ts_pd.asi8 // (86_400 * 10 ** 9)).astype(np.int64)
        day_starts = np.where(np.diff(day_ord, prepend=day_ord[0] - 1) != 0)[0]
        day_ends   = np.append(day_starts[1:], len(day_ord))
        n_days     = len(day_starts)

        # ── 설정값 (조합당 1회) ───────────────────────────────────────────────
        cfg = self._config
        lookback         = int(getattr(cfg, "vs_lookback_minutes", 10))
        spike_ratio      = float(getattr(cfg, "vs_spike_ratio", 5.0))
        sl_pct           = float(getattr(cfg, "vs_sl_pct", 0.02))
        tp_pct           = float(getattr(cfg, "vs_tp_pct", 0.03))
        entry_start_min  = _parse_hhmm(str(getattr(cfg, "vs_entry_start", "09:30")), 570)
        entry_end_min    = _parse_hhmm(str(getattr(cfg, "vs_entry_end", "13:00")), 780)
        min_prev_volume  = float(getattr(cfg, "vs_min_prev_volume", 50000))
        min_spike_volume = float(getattr(cfg, "vs_min_spike_volume", 10000))
        force_close_min  = _parse_hhmm(str(getattr(cfg, "force_close_time", "15:10")), 910)

        all_trades: list[dict] = []
        prev_volume = 0.0

        for di in range(n_days):
            s = int(day_starts[di])
            e = int(day_ends[di])

            closes  = all_closes[s:e]
            highs   = all_highs[s:e]
            lows    = all_lows[s:e]
            volumes = all_volumes[s:e]
            opens   = all_opens[s:e]
            mins    = all_minutes[s:e]
            n       = e - s

            if n == 0:
                prev_volume = 0.0
                continue

            # 전일 거래량 필터
            if min_prev_volume > 0 and prev_volume > 0 and prev_volume < min_prev_volume:
                prev_volume = float(np.sum(volumes))
                continue

            # ── 당일 거래 루프 ────────────────────────────────────────────
            position: dict | None = None
            trade_count    = 0
            last_exit_min: int | None = None

            for i in range(n):
                close_i = closes[i]
                high_i  = highs[i]
                low_i   = lows[i]
                vol_i   = volumes[i]
                open_i  = opens[i]
                min_i   = int(mins[i])
                ts_i    = ts_pd[s + i]

                # 강제 청산 시간
                if position is not None and min_i >= force_close_min:
                    ep, net_exit = apply_sell_costs(close_i, self._costs)
                    all_trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep,
                        "pnl":         net_exit - position["net_entry"],
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "forced_close",
                    })
                    position = None
                    last_exit_min = min_i
                    continue

                if position is None:
                    if (
                        trade_count < 1
                        and entry_start_min <= min_i <= entry_end_min
                        and vol_i >= min_spike_volume
                        and close_i > open_i                         # 양봉
                    ):
                        # 거래량 급증 확인 (직전 lookback 분봉 평균 대비)
                        if i >= lookback:
                            avg_vol = float(np.mean(volumes[i - lookback : i]))
                            spike_ok = avg_vol > 0 and vol_i >= avg_vol * spike_ratio
                        else:
                            spike_ok = False

                        if spike_ok:
                            trade_count += 1
                            entry_price, net_entry = apply_buy_costs(close_i, self._costs)
                            stop_loss = entry_price * (1.0 - sl_pct)
                            tp_price  = entry_price * (1.0 + tp_pct)
                            position = {
                                "entry_ts":    ts_i.to_pydatetime(),
                                "entry_price": entry_price,
                                "net_entry":   net_entry,
                                "stop_loss":   stop_loss,
                                "tp_price":    tp_price,
                            }

                else:
                    # TP
                    if high_i >= position["tp_price"]:
                        ep, net_exit = apply_sell_costs(position["tp_price"], self._costs)
                        all_trades.append({
                            "entry_ts":    position["entry_ts"],
                            "exit_ts":     ts_i.to_pydatetime(),
                            "entry_price": position["entry_price"],
                            "exit_price":  ep,
                            "pnl":         net_exit - position["net_entry"],
                            "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                            "exit_reason": "tp_exit",
                        })
                        position = None
                        last_exit_min = min_i
                        continue

                    # SL
                    if low_i <= position["stop_loss"]:
                        ep, net_exit = apply_sell_costs(position["stop_loss"], self._costs)
                        all_trades.append({
                            "entry_ts":    position["entry_ts"],
                            "exit_ts":     ts_i.to_pydatetime(),
                            "entry_price": position["entry_price"],
                            "exit_price":  ep,
                            "pnl":         net_exit - position["net_entry"],
                            "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                            "exit_reason": "stop_loss",
                        })
                        position = None
                        last_exit_min = min_i
                        continue

                    # 마지막 캔들 강제 청산
                    if i == n - 1:
                        ep, net_exit = apply_sell_costs(close_i, self._costs)
                        all_trades.append({
                            "entry_ts":    position["entry_ts"],
                            "exit_ts":     ts_i.to_pydatetime(),
                            "entry_price": position["entry_price"],
                            "exit_price":  ep,
                            "pnl":         net_exit - position["net_entry"],
                            "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                            "exit_reason": "forced_close",
                        })
                        position = None
                        last_exit_min = min_i

            prev_volume = float(np.sum(volumes))

        kpi = self.calculate_kpi(all_trades)
        kpi["trades"] = all_trades
        logger.debug(
            f"[VS-FAST] {ticker} 완료: trades={kpi['total_trades']} "
            f"pnl={kpi['total_pnl']:.1f}"
        )
        return kpi

    def run_backtest(
        self,
        candles: pd.DataFrame,
        strategy: "Any" = None,
    ) -> dict[str, Any]:
        """numpy 기반 거래량 폭발 단일 날짜 시뮬레이션."""
        if candles.empty:
            return {**self.calculate_kpi([]), "trades": []}

        candles = candles.reset_index(drop=True)
        n = len(candles)

        ts_pd   = pd.DatetimeIndex(candles["ts"].values)
        closes  = candles["close"].values.astype(np.float64)
        highs   = candles["high"].values.astype(np.float64)
        lows    = candles["low"].values.astype(np.float64)
        volumes = candles["volume"].values.astype(np.float64)
        opens   = candles["open"].values.astype(np.float64)
        minutes = (ts_pd.hour * 60 + ts_pd.minute).values.astype(np.int32)

        cfg = self._config
        lookback         = int(getattr(cfg, "vs_lookback_minutes", 10))
        spike_ratio      = float(getattr(cfg, "vs_spike_ratio", 5.0))
        sl_pct           = float(getattr(cfg, "vs_sl_pct", 0.02))
        tp_pct           = float(getattr(cfg, "vs_tp_pct", 0.03))
        entry_start_min  = _parse_hhmm(str(getattr(cfg, "vs_entry_start", "09:30")), 570)
        entry_end_min    = _parse_hhmm(str(getattr(cfg, "vs_entry_end", "13:00")), 780)
        min_prev_volume  = float(getattr(cfg, "vs_min_prev_volume", 50000))
        min_spike_volume = float(getattr(cfg, "vs_min_spike_volume", 10000))
        force_close_min  = _parse_hhmm(str(getattr(cfg, "force_close_time", "15:10")), 910)

        prev_volume = (
            float(getattr(strategy, "_prev_day_volume", 0)) if strategy else 0.0
        )
        if min_prev_volume > 0 and prev_volume > 0 and prev_volume < min_prev_volume:
            return {**self.calculate_kpi([]), "trades": []}

        trades: list[dict] = []
        position: dict | None = None
        trade_count    = 0
        last_exit_min: int | None = None

        for i in range(n):
            close_i = closes[i]
            high_i  = highs[i]
            low_i   = lows[i]
            vol_i   = volumes[i]
            open_i  = opens[i]
            min_i   = int(minutes[i])
            ts_i    = ts_pd[i]

            if position is not None and min_i >= force_close_min:
                ep, net_exit = apply_sell_costs(close_i, self._costs)
                trades.append({
                    "entry_ts":    position["entry_ts"],
                    "exit_ts":     ts_i.to_pydatetime(),
                    "entry_price": position["entry_price"],
                    "exit_price":  ep,
                    "pnl":         net_exit - position["net_entry"],
                    "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                    "exit_reason": "forced_close",
                })
                position = None
                last_exit_min = min_i
                continue

            if position is None:
                if (
                    trade_count < 1
                    and entry_start_min <= min_i <= entry_end_min
                    and vol_i >= min_spike_volume
                    and close_i > open_i
                ):
                    if i >= lookback:
                        avg_vol  = float(np.mean(volumes[i - lookback : i]))
                        spike_ok = avg_vol > 0 and vol_i >= avg_vol * spike_ratio
                    else:
                        spike_ok = False

                    if spike_ok:
                        trade_count += 1
                        entry_price, net_entry = apply_buy_costs(close_i, self._costs)
                        stop_loss = entry_price * (1.0 - sl_pct)
                        tp_price  = entry_price * (1.0 + tp_pct)
                        if strategy is not None:
                            strategy.on_entry()
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
                    trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep,
                        "pnl":         net_exit - position["net_entry"],
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "tp_exit",
                    })
                    position = None
                    last_exit_min = min_i
                    if strategy is not None:
                        strategy.on_exit()
                    continue

                if low_i <= position["stop_loss"]:
                    ep, net_exit = apply_sell_costs(position["stop_loss"], self._costs)
                    trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep,
                        "pnl":         net_exit - position["net_entry"],
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "stop_loss",
                    })
                    position = None
                    last_exit_min = min_i
                    if strategy is not None:
                        strategy.on_exit()
                    continue

                if i == n - 1:
                    ep, net_exit = apply_sell_costs(close_i, self._costs)
                    trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep,
                        "pnl":         net_exit - position["net_entry"],
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "forced_close",
                    })
                    position = None
                    last_exit_min = min_i
                    if strategy is not None:
                        strategy.on_exit()

        if strategy is not None:
            strategy.set_backtest_time(None)
        kpi = self.calculate_kpi(trades)
        kpi["trades"] = trades
        logger.debug(
            f"[VS-FAST] 단일 완료: trades={kpi['total_trades']} "
            f"pnl={kpi['total_pnl']:.1f}"
        )
        return kpi


# ---------------------------------------------------------------------------
# VolatilityBreakoutFastBacktester — 래리 윌리엄스 변동성 돌파 전략
# ---------------------------------------------------------------------------

class VolatilityBreakoutFastBacktester(Backtester):
    """변동성 돌파 전략 전용 numpy 가속 백테스터.

    핵심 공식
    ---------
    target_price = 당일시가 + (전일고가 - 전일저가) × k_value

    진입 조건
    ---------
    - 09:00 ~ entry_deadline 사이에 close >= target_price
    - 당일 첫 1회만 진입
    - 레인지 유효성: min_range_pct ≤ range_size/prev_close ≤ max_range_pct
    - 전일 거래량: prev_volume >= min_prev_volume
    - (선택) 거래량 확인: vol_i >= 당일 평균 분봉 거래량 × 2.0

    청산 조건 (우선순위)
    --------------------
    1. 강제 청산 시간(15:10) 도달    → forced_close
    2. TP 도달 (tp_pct > 0)         → tp_exit
    3. 트레일링 스톱 (use_trailing)  → trailing_stop
    4. SL 도달                      → stop_loss
    5. 마지막 캔들                   → forced_close
    """

    async def run_multi_day_cached(
        self,
        ticker: str,
        all_candles: pd.DataFrame,
        strategy: "Any" = None,
    ) -> dict[str, Any]:
        """변동성 돌파 멀티데이 고속 백테스트."""
        if all_candles.empty:
            return {**self.calculate_kpi([]), "trades": []}
        self._current_ticker = ticker

        # ── 종목당 1회 numpy 변환 ─────────────────────────────────────────
        ts_pd       = pd.DatetimeIndex(all_candles["ts"])
        all_closes  = all_candles["close"].values.astype(np.float64)
        all_highs   = all_candles["high"].values.astype(np.float64)
        all_lows    = all_candles["low"].values.astype(np.float64)
        all_volumes = all_candles["volume"].values.astype(np.float64)
        all_opens   = all_candles["open"].values.astype(np.float64)
        all_minutes = (ts_pd.hour * 60 + ts_pd.minute).values.astype(np.int32)

        # 날짜 경계
        day_ord    = (ts_pd.asi8 // (86_400 * 10 ** 9)).astype(np.int64)
        day_starts = np.where(np.diff(day_ord, prepend=day_ord[0] - 1) != 0)[0]
        day_ends   = np.append(day_starts[1:], len(day_ord))
        n_days     = len(day_starts)

        # ── 설정값 캐시 ──────────────────────────────────────────────────
        cfg = self._config
        k_value          = float(getattr(cfg, "vb_k_value",          0.5))
        entry_deadline   = _parse_hhmm(str(getattr(cfg, "vb_entry_deadline", "14:00")), 840)
        sl_mode          = str(getattr(cfg, "vb_sl_mode",            "open"))
        sl_pct           = float(getattr(cfg, "vb_sl_pct",           0.02))
        tp_pct           = float(getattr(cfg, "vb_tp_pct",           0.03))
        use_trailing     = bool(getattr(cfg, "vb_use_trailing",       True))
        trail_pct        = float(getattr(cfg, "vb_trail_pct",        0.02))
        use_vol_confirm  = bool(getattr(cfg, "vb_use_volume_confirm", True))
        min_range_pct    = float(getattr(cfg, "vb_min_range_pct",    0.015))
        max_range_pct    = float(getattr(cfg, "vb_max_range_pct",    0.10))
        min_prev_volume  = float(getattr(cfg, "vb_min_prev_volume",  50000))
        signal_block     = _parse_hhmm(str(getattr(cfg, "signal_block_until", "09:05")), 545)
        force_close_min  = _parse_hhmm(str(getattr(cfg, "force_close_time", "15:10")), 910)

        all_trades: list[dict] = []
        prev_close  = 0.0
        prev_volume = 0.0
        prev_high   = 0.0
        prev_low    = 0.0

        for di in range(n_days):
            s = int(day_starts[di])
            e = int(day_ends[di])

            closes  = all_closes[s:e]
            highs   = all_highs[s:e]
            lows    = all_lows[s:e]
            volumes = all_volumes[s:e]
            opens   = all_opens[s:e]
            mins    = all_minutes[s:e]
            n       = e - s

            if n == 0:
                continue

            # ── 전일 데이터 없으면 skip (레인지 계산 불가) ─────────────
            if prev_close <= 0:
                prev_close  = float(closes[-1])
                prev_high   = float(np.max(highs))
                prev_low    = float(np.min(lows))
                prev_volume = float(np.sum(volumes))
                continue

            # ── 전일 거래량 필터 ──────────────────────────────────────────
            if min_prev_volume > 0 and prev_volume < min_prev_volume:
                prev_close  = float(closes[-1])
                prev_high   = float(np.max(highs))
                prev_low    = float(np.min(lows))
                prev_volume = float(np.sum(volumes))
                continue

            # ── 레인지 계산 및 유효성 검사 ───────────────────────────────
            range_size = prev_high - prev_low
            range_pct  = range_size / prev_close if prev_close > 0 else 0.0
            if range_pct < min_range_pct or range_pct > max_range_pct:
                prev_close  = float(closes[-1])
                prev_high   = float(np.max(highs))
                prev_low    = float(np.min(lows))
                prev_volume = float(np.sum(volumes))
                continue

            # ── 당일 시가 + target 계산 ──────────────────────────────────
            today_open    = float(opens[0])
            target_price  = today_open + range_size * k_value
            cum_vols      = np.cumsum(volumes)

            # ── 당일 거래 루프 ────────────────────────────────────────────
            position: dict | None = None
            entered_today = False
            last_exit_min: int | None = None

            for i in range(n):
                close_i = closes[i]
                high_i  = highs[i]
                low_i   = lows[i]
                vol_i   = volumes[i]
                min_i   = int(mins[i])
                ts_i    = ts_pd[s + i]

                # ── 강제 청산 시간 ─────────────────────────────────────────
                if position is not None and min_i >= force_close_min:
                    ep, net_exit = apply_sell_costs(close_i, self._costs)
                    all_trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep,
                        "pnl":         net_exit - position["net_entry"],
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "forced_close",
                    })
                    position = None
                    last_exit_min = min_i
                    continue

                if position is None:
                    if (
                        not entered_today
                        and signal_block <= min_i <= entry_deadline
                        and close_i >= target_price
                    ):
                        # 거래량 확인 (선택)
                        if use_vol_confirm and i > 0 and cum_vols[i - 1] > 0:
                            avg_bar_vol = cum_vols[i - 1] / i
                            vol_ok = vol_i >= avg_bar_vol * 2.0
                        else:
                            vol_ok = True

                        if vol_ok:
                            entered_today = True
                            entry_price, net_entry = apply_buy_costs(close_i, self._costs)

                            if sl_mode == "open":
                                stop_loss = today_open
                            else:
                                stop_loss = entry_price * (1.0 - sl_pct)

                            tp_price = entry_price * (1.0 + tp_pct) if tp_pct > 0 else 0.0
                            position = {
                                "entry_ts":    ts_i.to_pydatetime(),
                                "entry_price": entry_price,
                                "net_entry":   net_entry,
                                "stop_loss":   stop_loss,
                                "tp_price":    tp_price,
                                "peak_price":  entry_price,
                            }

                else:
                    # 고점 갱신 (트레일링용)
                    if high_i > position["peak_price"]:
                        position["peak_price"] = high_i

                    # 1. TP
                    if tp_pct > 0 and high_i >= position["tp_price"]:
                        ep, net_exit = apply_sell_costs(position["tp_price"], self._costs)
                        all_trades.append({
                            "entry_ts":    position["entry_ts"],
                            "exit_ts":     ts_i.to_pydatetime(),
                            "entry_price": position["entry_price"],
                            "exit_price":  ep,
                            "pnl":         net_exit - position["net_entry"],
                            "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                            "exit_reason": "tp_exit",
                        })
                        position = None
                        last_exit_min = min_i
                        continue

                    # 2. 트레일링 스톱
                    if use_trailing:
                        trail_stop = position["peak_price"] * (1.0 - trail_pct)
                        if low_i <= trail_stop:
                            ep, net_exit = apply_sell_costs(trail_stop, self._costs)
                            all_trades.append({
                                "entry_ts":    position["entry_ts"],
                                "exit_ts":     ts_i.to_pydatetime(),
                                "entry_price": position["entry_price"],
                                "exit_price":  ep,
                                "pnl":         net_exit - position["net_entry"],
                                "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                                "exit_reason": "trailing_stop",
                            })
                            position = None
                            last_exit_min = min_i
                            continue

                    # 3. SL
                    if low_i <= position["stop_loss"]:
                        ep, net_exit = apply_sell_costs(position["stop_loss"], self._costs)
                        all_trades.append({
                            "entry_ts":    position["entry_ts"],
                            "exit_ts":     ts_i.to_pydatetime(),
                            "entry_price": position["entry_price"],
                            "exit_price":  ep,
                            "pnl":         net_exit - position["net_entry"],
                            "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                            "exit_reason": "stop_loss",
                        })
                        position = None
                        last_exit_min = min_i
                        continue

                    # 4. 마지막 캔들 강제 청산
                    if i == n - 1:
                        ep, net_exit = apply_sell_costs(close_i, self._costs)
                        all_trades.append({
                            "entry_ts":    position["entry_ts"],
                            "exit_ts":     ts_i.to_pydatetime(),
                            "entry_price": position["entry_price"],
                            "exit_price":  ep,
                            "pnl":         net_exit - position["net_entry"],
                            "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                            "exit_reason": "forced_close",
                        })
                        position = None
                        last_exit_min = min_i

            prev_close  = float(closes[-1])
            prev_high   = float(np.max(highs))
            prev_low    = float(np.min(lows))
            prev_volume = float(np.sum(volumes))

        kpi = self.calculate_kpi(all_trades)
        kpi["trades"] = all_trades
        logger.debug(
            f"[VB-FAST] {ticker} 완료: trades={kpi['total_trades']} "
            f"pnl={kpi['total_pnl']:.1f}"
        )
        return kpi

    def run_backtest(
        self,
        candles: pd.DataFrame,
        strategy: "Any" = None,
    ) -> dict[str, Any]:
        """numpy 기반 변동성 돌파 단일 날짜 시뮬레이션 (strategy에서 prev_day 정보 수신)."""
        if candles.empty:
            return {**self.calculate_kpi([]), "trades": []}

        candles = candles.reset_index(drop=True)
        n = len(candles)

        ts_pd   = pd.DatetimeIndex(candles["ts"].values)
        closes  = candles["close"].values.astype(np.float64)
        highs   = candles["high"].values.astype(np.float64)
        lows    = candles["low"].values.astype(np.float64)
        volumes = candles["volume"].values.astype(np.float64)
        opens   = candles["open"].values.astype(np.float64)
        minutes = (ts_pd.hour * 60 + ts_pd.minute).values.astype(np.int32)

        cfg = self._config
        k_value         = float(getattr(cfg, "vb_k_value",          0.5))
        entry_deadline  = _parse_hhmm(str(getattr(cfg, "vb_entry_deadline", "14:00")), 840)
        sl_mode         = str(getattr(cfg, "vb_sl_mode",            "open"))
        sl_pct          = float(getattr(cfg, "vb_sl_pct",           0.02))
        tp_pct          = float(getattr(cfg, "vb_tp_pct",           0.03))
        use_trailing    = bool(getattr(cfg, "vb_use_trailing",       True))
        trail_pct       = float(getattr(cfg, "vb_trail_pct",        0.02))
        use_vol_confirm = bool(getattr(cfg, "vb_use_volume_confirm", True))
        signal_block    = _parse_hhmm(str(getattr(cfg, "signal_block_until", "09:05")), 545)
        force_close_min = _parse_hhmm(str(getattr(cfg, "force_close_time", "15:10")), 910)

        prev_high  = float(getattr(strategy, "_prev_high",  0.0)) if strategy else 0.0
        prev_low   = float(getattr(strategy, "_prev_low",   0.0)) if strategy else 0.0
        prev_close = float(getattr(strategy, "_prev_close", 0.0)) if strategy else 0.0

        if prev_close <= 0 or prev_high <= 0:
            return {**self.calculate_kpi([]), "trades": []}

        range_size   = prev_high - prev_low
        today_open   = float(opens[0]) if n > 0 else 0.0
        target_price = today_open + range_size * k_value
        cum_vols     = np.cumsum(volumes)

        trades: list[dict] = []
        position: dict | None = None
        entered_today = False
        last_exit_min: int | None = None

        for i in range(n):
            close_i = closes[i]
            high_i  = highs[i]
            low_i   = lows[i]
            vol_i   = volumes[i]
            min_i   = int(minutes[i])
            ts_i    = ts_pd[i]

            if position is not None and min_i >= force_close_min:
                ep, net_exit = apply_sell_costs(close_i, self._costs)
                trades.append({
                    "entry_ts":    position["entry_ts"],
                    "exit_ts":     ts_i.to_pydatetime(),
                    "entry_price": position["entry_price"],
                    "exit_price":  ep,
                    "pnl":         net_exit - position["net_entry"],
                    "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                    "exit_reason": "forced_close",
                })
                position = None
                last_exit_min = min_i
                continue

            if position is None:
                if (
                    not entered_today
                    and signal_block <= min_i <= entry_deadline
                    and close_i >= target_price
                ):
                    if use_vol_confirm and i > 0 and cum_vols[i - 1] > 0:
                        avg_bar_vol = cum_vols[i - 1] / i
                        vol_ok = vol_i >= avg_bar_vol * 2.0
                    else:
                        vol_ok = True

                    if vol_ok:
                        entered_today = True
                        entry_price, net_entry = apply_buy_costs(close_i, self._costs)

                        if sl_mode == "open":
                            stop_loss = today_open
                        else:
                            stop_loss = entry_price * (1.0 - sl_pct)

                        tp_price = entry_price * (1.0 + tp_pct) if tp_pct > 0 else 0.0
                        position = {
                            "entry_ts":    ts_i.to_pydatetime(),
                            "entry_price": entry_price,
                            "net_entry":   net_entry,
                            "stop_loss":   stop_loss,
                            "tp_price":    tp_price,
                            "peak_price":  entry_price,
                        }
                        if strategy is not None:
                            strategy.on_entry()
            else:
                if high_i > position["peak_price"]:
                    position["peak_price"] = high_i

                if tp_pct > 0 and high_i >= position["tp_price"]:
                    ep, net_exit = apply_sell_costs(position["tp_price"], self._costs)
                    trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep,
                        "pnl":         net_exit - position["net_entry"],
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "tp_exit",
                    })
                    position = None
                    last_exit_min = min_i
                    if strategy is not None:
                        strategy.on_exit()
                    continue

                if use_trailing:
                    trail_stop = position["peak_price"] * (1.0 - trail_pct)
                    if low_i <= trail_stop:
                        ep, net_exit = apply_sell_costs(trail_stop, self._costs)
                        trades.append({
                            "entry_ts":    position["entry_ts"],
                            "exit_ts":     ts_i.to_pydatetime(),
                            "entry_price": position["entry_price"],
                            "exit_price":  ep,
                            "pnl":         net_exit - position["net_entry"],
                            "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                            "exit_reason": "trailing_stop",
                        })
                        position = None
                        last_exit_min = min_i
                        if strategy is not None:
                            strategy.on_exit()
                        continue

                if low_i <= position["stop_loss"]:
                    ep, net_exit = apply_sell_costs(position["stop_loss"], self._costs)
                    trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep,
                        "pnl":         net_exit - position["net_entry"],
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "stop_loss",
                    })
                    position = None
                    last_exit_min = min_i
                    if strategy is not None:
                        strategy.on_exit()
                    continue

                if i == n - 1:
                    ep, net_exit = apply_sell_costs(close_i, self._costs)
                    trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep,
                        "pnl":         net_exit - position["net_entry"],
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "forced_close",
                    })
                    position = None
                    last_exit_min = min_i
                    if strategy is not None:
                        strategy.on_exit()

        if strategy is not None:
            strategy.set_backtest_time(None)
        kpi = self.calculate_kpi(trades)
        kpi["trades"] = trades
        logger.debug(
            f"[VB-FAST] 단일 완료: trades={kpi['total_trades']} "
            f"pnl={kpi['total_pnl']:.1f}"
        )
        return kpi


# ---------------------------------------------------------------------------
# Gap & Go 전략 전용 numpy 가속 백테스터
# ---------------------------------------------------------------------------

class GapAndGoFastBacktester(Backtester):
    """갭앤고(Gap & Go) 전략 전용 numpy 가속 백테스터.

    갭업으로 시작한 종목의 첫 5분봉(09:00~09:04)이 양봉이면 매수 진입.
    이전 gap_pullback 전략과 별개 — 전략 로직/파라미터 완전 분리.

    진입 조건
    ---------
    - 당일 시가 > 전일 종가 × (1 + gap_min_pct)  AND  < 전일 종가 × (1 + gap_max_pct)
    - 09:00~09:04 첫 5분봉: 양봉 (close > open) + 몸통비율 >= body_ratio_min
    - 거래량 (선택): 첫 봉 거래량 >= 전일 평균 5분봉 거래량 × volume_ratio

    진입 방식
    ---------
    - close     : 09:05 즉시 진입 (첫 봉 종가 기준가)
    - high_break: 09:05~entry_deadline 사이 첫 봉 고가 돌파 시 진입

    청산 조건 (우선순위 순)
    -----------------------
    1. TP 도달 (high >= tp_price)                       → tp_exit
    2. 트레일링 스톱 (close < peak × (1-trail_pct))     → trailing_stop  (trail_only)
    3. 손절 (low <= stop_loss)                          → stop_loss
    4. 강제 청산 (15:10 이후 또는 마지막 캔들)           → forced_close
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    async def run_multi_day_cached(
        self,
        ticker: str,
        all_candles: pd.DataFrame,
        strategy: "Any" = None,
    ) -> dict[str, Any]:
        """갭앤고 전용 고속 멀티데이 백테스트."""
        if all_candles.empty:
            return {**self.calculate_kpi([]), "trades": []}
        self._current_ticker = ticker

        # ── 종목당 1회 numpy 변환 ──────────────────────────────────────────
        ts_pd       = pd.DatetimeIndex(all_candles["ts"])
        all_closes  = all_candles["close"].values.astype(np.float64)
        all_highs   = all_candles["high"].values.astype(np.float64)
        all_lows    = all_candles["low"].values.astype(np.float64)
        all_volumes = all_candles["volume"].values.astype(np.float64)
        all_opens   = all_candles["open"].values.astype(np.float64)
        all_minutes = (ts_pd.hour * 60 + ts_pd.minute).values.astype(np.int32)

        day_ord    = (ts_pd.asi8 // (86_400 * 10 ** 9)).astype(np.int64)
        day_starts = np.where(np.diff(day_ord, prepend=day_ord[0] - 1) != 0)[0]
        day_ends   = np.append(day_starts[1:], len(day_ord))
        n_days     = len(day_starts)

        # ── 설정 (조합당 1회) ──────────────────────────────────────────────
        cfg = self._config
        gap_min_pct      = float(getattr(cfg, "gg_gap_min_pct", 0.02))
        gap_max_pct      = float(getattr(cfg, "gg_gap_max_pct", 0.15))
        body_ratio_min   = float(getattr(cfg, "gg_body_ratio_min", 0.5))
        entry_mode       = str(getattr(cfg, "gg_entry_mode", "close"))
        entry_deadline   = _parse_hhmm(str(getattr(cfg, "gg_entry_deadline", "09:30")), 570)
        sl_mode          = str(getattr(cfg, "gg_sl_mode", "first_bar_low"))
        sl_pct           = float(getattr(cfg, "gg_sl_pct", 0.02))
        tp_pct           = float(getattr(cfg, "gg_tp_pct", 0.05))
        use_trailing     = bool(getattr(cfg, "gg_use_trailing", False))
        trail_pct        = float(getattr(cfg, "gg_trail_pct", 0.02))
        use_volume       = bool(getattr(cfg, "gg_use_volume", True))
        volume_ratio     = float(getattr(cfg, "gg_volume_ratio", 2.0))

        range_start_min  = 540   # 09:00
        range_end_min    = 544   # 09:04
        force_close_min  = 15 * 60 + 10  # 15:10

        all_trades: list[dict] = []
        prev_close  = 0.0
        prev_volume = 0.0
        prev_n_bars = 0

        for di in range(n_days):
            s = int(day_starts[di])
            e = int(day_ends[di])

            closes  = all_closes[s:e]
            highs   = all_highs[s:e]
            lows    = all_lows[s:e]
            volumes = all_volumes[s:e]
            opens   = all_opens[s:e]
            mins    = all_minutes[s:e]
            n       = e - s

            # ── 첫날 (전일 데이터 없음) 스킵 ──────────────────────────────
            if prev_close <= 0:
                prev_close  = float(closes[-1])
                prev_volume = float(np.sum(volumes))
                prev_n_bars = n
                continue

            # ── 당일 시가 (09:00 봉 open) ──────────────────────────────────
            open09_idxs = np.where(mins == range_start_min)[0]
            if len(open09_idxs) == 0:
                prev_close  = float(closes[-1])
                prev_volume = float(np.sum(volumes))
                prev_n_bars = n
                continue
            day_open = float(opens[open09_idxs[0]])

            # ── 갭업 체크 ──────────────────────────────────────────────────
            gap_pct_val = (day_open - prev_close) / prev_close
            if gap_pct_val < gap_min_pct or gap_pct_val >= gap_max_pct:
                prev_close  = float(closes[-1])
                prev_volume = float(np.sum(volumes))
                prev_n_bars = n
                continue

            # ── 첫 5분봉 집계 (09:00~09:04) ────────────────────────────────
            first_bar_mask = (mins >= range_start_min) & (mins <= range_end_min)
            if not np.any(first_bar_mask):
                prev_close  = float(closes[-1])
                prev_volume = float(np.sum(volumes))
                prev_n_bars = n
                continue

            fb_idxs  = np.where(first_bar_mask)[0]
            fb_open  = float(opens[fb_idxs[0]])
            fb_close = float(closes[fb_idxs[-1]])
            fb_high  = float(np.max(highs[first_bar_mask]))
            fb_low   = float(np.min(lows[first_bar_mask]))
            fb_vol   = float(np.sum(volumes[first_bar_mask]))

            # ── 양봉 체크 ──────────────────────────────────────────────────
            if fb_close <= fb_open:
                prev_close  = float(closes[-1])
                prev_volume = float(np.sum(volumes))
                prev_n_bars = n
                continue

            # ── 몸통비율 체크 ──────────────────────────────────────────────
            fb_range = fb_high - fb_low
            if fb_range > 0 and (fb_close - fb_open) / fb_range < body_ratio_min:
                prev_close  = float(closes[-1])
                prev_volume = float(np.sum(volumes))
                prev_n_bars = n
                continue

            # ── 거래량 필터 ────────────────────────────────────────────────
            # 전일 평균 5분봉 거래량 = prev_volume / (prev_n_bars / 5)
            if use_volume and prev_volume > 0 and prev_n_bars > 0:
                avg_5min = prev_volume / prev_n_bars * 5
                if fb_vol < avg_5min * volume_ratio:
                    prev_close  = float(closes[-1])
                    prev_volume = float(np.sum(volumes))
                    prev_n_bars = n
                    continue

            # ── 진입 ───────────────────────────────────────────────────────
            entry_price = 0.0
            entry_ts    = None
            entry_i     = -1

            if entry_mode == "close":
                # 09:05 즉시 진입 (첫 봉 종가 기준가)
                after_idxs = np.where(mins > range_end_min)[0]
                if len(after_idxs) == 0:
                    prev_close  = float(closes[-1])
                    prev_volume = float(np.sum(volumes))
                    prev_n_bars = n
                    continue
                entry_i     = int(after_idxs[0])
                entry_price = fb_close
                entry_ts    = ts_pd[s + entry_i].to_pydatetime()
            else:
                # high_break: 09:05~entry_deadline 사이 close > fb_high 시 진입
                scan_idxs = np.where((mins > range_end_min) & (mins <= entry_deadline))[0]
                for idx in scan_idxs:
                    if closes[idx] > fb_high:
                        entry_i     = int(idx)
                        entry_price = float(closes[idx])
                        entry_ts    = ts_pd[s + idx].to_pydatetime()
                        break

            if entry_i < 0:
                prev_close  = float(closes[-1])
                prev_volume = float(np.sum(volumes))
                prev_n_bars = n
                continue

            # ── 비용 적용 ──────────────────────────────────────────────────
            ep, net_entry = apply_buy_costs(entry_price, self._costs)

            # ── 손절가 ─────────────────────────────────────────────────────
            if sl_mode == "first_bar_low":
                stop_loss = fb_low
            elif sl_mode == "prev_close":
                stop_loss = prev_close
            else:  # fixed_2pct
                stop_loss = ep * (1.0 - sl_pct)

            # ── 익절가 / 트레일링 설정 ─────────────────────────────────────
            tp_price   = 0.0 if (use_trailing or tp_pct <= 0) else ep * (1.0 + tp_pct)
            peak_price = ep

            # ── 포지션 관리 루프 ───────────────────────────────────────────
            exited = False
            for i in range(entry_i + 1, n):
                close_i = closes[i]
                high_i  = highs[i]
                low_i   = lows[i]
                min_i   = int(mins[i])

                if use_trailing and high_i > peak_price:
                    peak_price = high_i

                # 1. TP
                if tp_price > 0 and high_i >= tp_price:
                    ep_out, net_exit = apply_sell_costs(tp_price, self._costs)
                    all_trades.append({
                        "entry_ts":    entry_ts,
                        "exit_ts":     ts_pd[s + i].to_pydatetime(),
                        "entry_price": ep,
                        "exit_price":  ep_out,
                        "pnl":         net_exit - net_entry,
                        "pnl_pct":     (net_exit - net_entry) / net_entry,
                        "exit_reason": "tp_exit",
                    })
                    exited = True
                    break

                # 2. 트레일링 스톱
                if use_trailing and close_i < peak_price * (1.0 - trail_pct):
                    ep_out, net_exit = apply_sell_costs(close_i, self._costs)
                    all_trades.append({
                        "entry_ts":    entry_ts,
                        "exit_ts":     ts_pd[s + i].to_pydatetime(),
                        "entry_price": ep,
                        "exit_price":  ep_out,
                        "pnl":         net_exit - net_entry,
                        "pnl_pct":     (net_exit - net_entry) / net_entry,
                        "exit_reason": "trailing_stop",
                    })
                    exited = True
                    break

                # 3. 손절
                if low_i <= stop_loss:
                    ep_out, net_exit = apply_sell_costs(stop_loss, self._costs)
                    all_trades.append({
                        "entry_ts":    entry_ts,
                        "exit_ts":     ts_pd[s + i].to_pydatetime(),
                        "entry_price": ep,
                        "exit_price":  ep_out,
                        "pnl":         net_exit - net_entry,
                        "pnl_pct":     (net_exit - net_entry) / net_entry,
                        "exit_reason": "stop_loss",
                    })
                    exited = True
                    break

                # 4. 강제 청산 (15:10 이후 또는 마지막 캔들)
                if min_i >= force_close_min or i == n - 1:
                    ep_out, net_exit = apply_sell_costs(close_i, self._costs)
                    all_trades.append({
                        "entry_ts":    entry_ts,
                        "exit_ts":     ts_pd[s + i].to_pydatetime(),
                        "entry_price": ep,
                        "exit_price":  ep_out,
                        "pnl":         net_exit - net_entry,
                        "pnl_pct":     (net_exit - net_entry) / net_entry,
                        "exit_reason": "forced_close",
                    })
                    exited = True
                    break

            # 진입 캔들이 마지막이었을 때 (edge case)
            if not exited:
                ep_out, net_exit = apply_sell_costs(float(closes[-1]), self._costs)
                all_trades.append({
                    "entry_ts":    entry_ts,
                    "exit_ts":     ts_pd[s + n - 1].to_pydatetime(),
                    "entry_price": ep,
                    "exit_price":  ep_out,
                    "pnl":         net_exit - net_entry,
                    "pnl_pct":     (net_exit - net_entry) / net_entry,
                    "exit_reason": "forced_close",
                })

            prev_close  = float(closes[-1])
            prev_volume = float(np.sum(volumes))
            prev_n_bars = n

        kpi = self.calculate_kpi(all_trades)
        kpi["trades"] = all_trades
        return kpi

    def run_backtest(
        self,
        candles: pd.DataFrame,
        strategy: "Any" = None,
    ) -> dict[str, Any]:
        """단일 날짜 시뮬레이션 — run_multi_day_cached 위임."""
        import asyncio as _asyncio
        return _asyncio.run(self.run_multi_day_cached("_single", candles, strategy))


# ---------------------------------------------------------------------------
# VIBreakoutFastBacktester — VI(변동성완화장치) 돌파 전략 전용 numpy 백테스터
# ---------------------------------------------------------------------------

class VIBreakoutFastBacktester(Backtester):
    """VI 돌파 전략 전용 numpy 가속 백테스터.

    분봉 기반 VI 추정
    -----------------
    - 정적 VI: high >= prev_close × (1 + vi_static_trigger_pct) 시 발동 추정
    - VI 발동 분봉의 open을 'VI 직전가(vi_pre_price)'로 사용
      (해당 분봉이 시작했을 때 가격 ≈ VI 발동 직전 마지막 체결가)
    - 발동 분봉은 단일가 매매 중이므로 진입 스킵
    - 다음 분봉 이후 close > vi_pre_price × (1 + vi_breakout_pct) 시 진입

    진입 조건
    ---------
    - 09:05 ≤ min_i ≤ entry_deadline
    - 상승 방향 VI만 (하락 VI 제외)
    - 당일 1회만 진입
    - 전일 거래량: prev_volume >= min_prev_volume
    - (선택) 진입 분봉 거래량 ≥ 직전 10분 평균 × volume_ratio

    청산 조건 (우선순위)
    --------------------
    1. 강제 청산 시간(15:10) 도달    → forced_close
    2. TP 도달 (tp_pct > 0)         → tp_exit
    3. 트레일링 스톱 (use_trailing)  → trailing_stop
    4. SL 도달                      → stop_loss
    5. 마지막 캔들                   → forced_close
    """

    async def run_multi_day_cached(
        self,
        ticker: str,
        all_candles: pd.DataFrame,
        strategy: "Any" = None,
    ) -> dict[str, Any]:
        """VI 돌파 멀티데이 고속 백테스트."""
        if all_candles.empty:
            return {**self.calculate_kpi([]), "trades": []}
        self._current_ticker = ticker

        # ── 종목당 1회 numpy 변환 ──────────────────────────────────────────
        ts_pd       = pd.DatetimeIndex(all_candles["ts"])
        all_closes  = all_candles["close"].values.astype(np.float64)
        all_highs   = all_candles["high"].values.astype(np.float64)
        all_lows    = all_candles["low"].values.astype(np.float64)
        all_volumes = all_candles["volume"].values.astype(np.float64)
        all_opens   = all_candles["open"].values.astype(np.float64)
        all_minutes = (ts_pd.hour * 60 + ts_pd.minute).values.astype(np.int32)

        # 날짜 경계
        day_ord    = (ts_pd.asi8 // (86_400 * 10 ** 9)).astype(np.int64)
        day_starts = np.where(np.diff(day_ord, prepend=day_ord[0] - 1) != 0)[0]
        day_ends   = np.append(day_starts[1:], len(day_ord))
        n_days     = len(day_starts)

        # ── 설정값 캐시 ──────────────────────────────────────────────────
        cfg = self._config
        vi_static_trigger_pct = float(getattr(cfg, "vi_static_trigger_pct", 0.095))
        vi_breakout_pct       = float(getattr(cfg, "vi_breakout_pct",       0.005))
        sl_pct                = float(getattr(cfg, "vi_sl_pct",             0.015))
        tp_pct                = float(getattr(cfg, "vi_tp_pct",             0.03))
        use_trailing          = bool(getattr(cfg, "vi_use_trailing",        False))
        trail_pct             = float(getattr(cfg, "vi_trail_pct",          0.015))
        entry_deadline        = _parse_hhmm(str(getattr(cfg, "vi_entry_deadline", "13:00")), 780)
        use_volume            = bool(getattr(cfg, "vi_use_volume",          True))
        volume_ratio          = float(getattr(cfg, "vi_volume_ratio",       2.0))
        min_prev_volume       = float(getattr(cfg, "vi_min_prev_volume",    50000))
        signal_block          = _parse_hhmm(str(getattr(cfg, "signal_block_until", "09:05")), 545)
        force_close_min       = _parse_hhmm(str(getattr(cfg, "force_close_time", "15:10")), 910)
        vol_lookback          = 10  # 직전 10분 평균 거래량 기준

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
            opens   = all_opens[s:e]
            mins    = all_minutes[s:e]
            n       = e - s

            if n == 0:
                prev_close  = 0.0
                prev_volume = 0.0
                continue

            # 첫날 (전일 데이터 없음) 스킵
            if prev_close <= 0:
                prev_close  = float(closes[-1])
                prev_volume = float(np.sum(volumes))
                continue

            # 전일 거래량 필터
            if min_prev_volume > 0 and prev_volume > 0 and prev_volume < min_prev_volume:
                prev_close  = float(closes[-1])
                prev_volume = float(np.sum(volumes))
                continue

            # ── 당일 VI 추정 상태 ─────────────────────────────────────────
            vi_triggered   = False
            vi_pre_price   = 0.0
            vi_trigger_bar = -1  # VI 발동 분봉 인덱스 (발동 분봉은 진입 스킵)

            # ── 당일 거래 루프 ─────────────────────────────────────────────
            position: dict | None = None
            entered_today = False
            last_exit_min: int | None = None

            for i in range(n):
                close_i = closes[i]
                high_i  = highs[i]
                low_i   = lows[i]
                vol_i   = volumes[i]
                open_i  = opens[i]
                min_i   = int(mins[i])
                ts_i    = ts_pd[s + i]

                # ── 강제 청산 시간 ─────────────────────────────────────────
                if position is not None and min_i >= force_close_min:
                    ep, net_exit = apply_sell_costs(close_i, self._costs)
                    all_trades.append({
                        "entry_ts":    position["entry_ts"],
                        "exit_ts":     ts_i.to_pydatetime(),
                        "entry_price": position["entry_price"],
                        "exit_price":  ep,
                        "pnl":         net_exit - position["net_entry"],
                        "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                        "exit_reason": "forced_close",
                    })
                    position = None
                    last_exit_min = min_i
                    continue

                # ── VI 발동 추정 (포지션 없음, 미진입, 09:05 이후) ──────────
                if (
                    not vi_triggered
                    and position is None
                    and not entered_today
                    and min_i >= signal_block
                    and prev_close > 0
                ):
                    pct_change = (high_i - prev_close) / prev_close
                    if pct_change >= vi_static_trigger_pct:
                        vi_triggered   = True
                        vi_pre_price   = float(open_i)  # VI 발동 직전 분봉 시가
                        vi_trigger_bar = i
                        # 발동 분봉 자체는 단일가 매매 중 → 진입 스킵

                # ── 진입 탐색 ─────────────────────────────────────────────
                if position is None:
                    if (
                        vi_triggered
                        and not entered_today
                        and i > vi_trigger_bar          # 발동 분봉 다음 분봉부터
                        and signal_block <= min_i <= entry_deadline
                        and vi_pre_price > 0
                    ):
                        breakout_threshold = vi_pre_price * (1.0 + vi_breakout_pct)
                        if close_i > breakout_threshold:
                            # 거래량 필터 (선택)
                            if use_volume:
                                if i >= vol_lookback:
                                    avg_vol = float(np.mean(volumes[i - vol_lookback : i]))
                                    vol_ok = avg_vol > 0 and vol_i >= avg_vol * volume_ratio
                                else:
                                    vol_ok = False
                            else:
                                vol_ok = True

                            if vol_ok:
                                entered_today = True
                                entry_price, net_entry = apply_buy_costs(close_i, self._costs)
                                stop_loss = entry_price * (1.0 - sl_pct)
                                tp_price  = entry_price * (1.0 + tp_pct) if tp_pct > 0 else 0.0
                                position = {
                                    "entry_ts":    ts_i.to_pydatetime(),
                                    "entry_price": entry_price,
                                    "net_entry":   net_entry,
                                    "stop_loss":   stop_loss,
                                    "tp_price":    tp_price,
                                    "peak_price":  entry_price,
                                }

                # ── 청산 처리 ─────────────────────────────────────────────
                else:
                    # 고점 갱신 (트레일링용)
                    if use_trailing and high_i > position["peak_price"]:
                        position["peak_price"] = high_i

                    # 1. TP
                    if tp_pct > 0 and high_i >= position["tp_price"]:
                        ep, net_exit = apply_sell_costs(position["tp_price"], self._costs)
                        all_trades.append({
                            "entry_ts":    position["entry_ts"],
                            "exit_ts":     ts_i.to_pydatetime(),
                            "entry_price": position["entry_price"],
                            "exit_price":  ep,
                            "pnl":         net_exit - position["net_entry"],
                            "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                            "exit_reason": "tp_exit",
                        })
                        position = None
                        last_exit_min = min_i
                        continue

                    # 2. 트레일링 스톱
                    if use_trailing:
                        trail_stop = position["peak_price"] * (1.0 - trail_pct)
                        if low_i <= trail_stop:
                            ep, net_exit = apply_sell_costs(trail_stop, self._costs)
                            all_trades.append({
                                "entry_ts":    position["entry_ts"],
                                "exit_ts":     ts_i.to_pydatetime(),
                                "entry_price": position["entry_price"],
                                "exit_price":  ep,
                                "pnl":         net_exit - position["net_entry"],
                                "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                                "exit_reason": "trailing_stop",
                            })
                            position = None
                            last_exit_min = min_i
                            continue

                    # 3. SL
                    if low_i <= position["stop_loss"]:
                        ep, net_exit = apply_sell_costs(position["stop_loss"], self._costs)
                        all_trades.append({
                            "entry_ts":    position["entry_ts"],
                            "exit_ts":     ts_i.to_pydatetime(),
                            "entry_price": position["entry_price"],
                            "exit_price":  ep,
                            "pnl":         net_exit - position["net_entry"],
                            "pnl_pct":     (net_exit - position["net_entry"]) / position["net_entry"],
                            "exit_reason": "stop_loss",
                        })
                        position = None
                        last_exit_min = min_i
                        continue

                    # 4. 마지막 캔들 강제 청산
                    if i == n - 1:
                        ep, net_exit = apply_sell_costs(close_i, self._costs)
                        all_trades.append({
                            "entry_ts":    position["entry_ts"],
                            "exit_ts":     ts_i.to_pydatetime(),
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
        logger.debug(
            f"[VI-FAST] {ticker} 완료: trades={kpi['total_trades']} "
            f"pnl={kpi['total_pnl']:.1f}"
        )
        return kpi

    def run_backtest(
        self,
        candles: pd.DataFrame,
        strategy: "Any" = None,
    ) -> dict[str, Any]:
        """단일 날짜 시뮬레이션 — run_multi_day_cached 위임."""
        import asyncio as _asyncio
        return _asyncio.run(self.run_multi_day_cached("_single", candles, strategy))
