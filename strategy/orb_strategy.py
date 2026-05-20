"""strategy/orb_strategy.py — Opening Range Breakout 전략.

09:00~09:05 첫 5분 분봉으로 range_high/range_low를 설정하고,
range_high 돌파 시 매수 진입한다.

진입: 09:05 ~ entry_deadline
손절: entry_price - range_size * sl_ratio
익절: entry_price + range_size * tp_ratio
강제: 15:10 (force_close_time)
"""
from __future__ import annotations

import re
from datetime import datetime, time

import pandas as pd
from loguru import logger

from config.settings import TradingConfig
from strategy.base_strategy import BaseStrategy, Signal


def _parse_time(s: str, default: time) -> time:
    m = re.match(r"(\d+):(\d+)", str(s))
    return time(int(m.group(1)), int(m.group(2))) if m else default


def _hhmm_to_min(s: str, default: int) -> int:
    try:
        h, m = map(int, str(s).split(":"))
        return h * 60 + m
    except Exception:
        return default


class ORBStrategy(BaseStrategy):
    """Opening Range Breakout 전략."""

    def __init__(self, config: TradingConfig) -> None:
        super().__init__()
        self._config = config

        # 전일 데이터 (backtester._setup_strategy_day 에서 주입)
        self._prev_day_volume: int = 0
        self._prev_day_close: float = 0.0

        # 당일 ORB 레인지 (generate_signal 최초 호출 시 자동 계산)
        self._range_high: float = 0.0
        self._range_low: float = 0.0
        self._range_size: float = 0.0
        self._range_valid: bool = False
        self._range_computed: bool = False  # 당일 이미 계산 여부

        # ORB 파라미터
        self._range_minutes: int = int(getattr(config, "orb_range_minutes", 5))
        self._min_range_pct: float = float(getattr(config, "orb_min_range_pct", 0.005))
        self._max_range_pct: float = float(getattr(config, "orb_max_range_pct", 0.05))
        self._breakout_buffer: float = float(getattr(config, "orb_breakout_buffer", 0.0))
        self._sl_ratio: float = float(getattr(config, "orb_sl_ratio", 1.0))
        self._tp_ratio: float = float(getattr(config, "orb_tp_ratio", 2.0))
        self._use_volume_filter: bool = bool(getattr(config, "orb_use_volume_filter", True))
        self._rvol_min: float = float(getattr(config, "orb_rvol_min", 1.5))

        # 진입 시간창: 09:05 ~ entry_deadline
        self._entry_deadline: time = _parse_time(
            getattr(config, "orb_entry_deadline", "10:00"), time(10, 0)
        )

        self.configure_multi_trade(
            max_trades=int(getattr(config, "max_trades_per_day", 2)),
            cooldown_minutes=int(getattr(config, "cooldown_minutes", 0)),
        )

    # ──────────────────────────── 데이터 주입 훅 ────────────────────────────

    def set_prev_day_data(self, high: float, volume: int, close: float = 0.0) -> None:
        """backtester._setup_strategy_day 공통 훅."""
        self._prev_day_volume = volume
        self._prev_day_close = close

    def set_prev_day_volume(self, volume: int) -> None:
        """backtester._setup_strategy_day ORB 전용 훅."""
        self._prev_day_volume = volume

    def set_prev_day_candles(self, candles: pd.DataFrame | None) -> None:
        """호환용 — ORB는 전일 분봉 미사용."""
        pass

    def reset(self) -> None:
        super().reset()
        self._range_high = 0.0
        self._range_low = 0.0
        self._range_size = 0.0
        self._range_valid = False
        self._range_computed = False

    # ──────────────────────────── 레인지 계산 ───────────────────────────────

    def _compute_range(self, candles: pd.DataFrame) -> None:
        """candles에서 09:00 ~ 09:00+(range_minutes-1)분 분봉을 추출하여 ORB 레인지 계산."""
        if self._range_computed:
            return
        self._range_computed = True

        if candles.empty:
            return

        df = candles.copy()
        if "ts" not in df.columns:
            return

        df["_min"] = pd.to_datetime(df["ts"]).dt.hour * 60 + pd.to_datetime(df["ts"]).dt.minute
        start_min = 9 * 60          # 09:00
        end_min = start_min + self._range_minutes - 1  # 09:04 (5분봉)

        mask = (df["_min"] >= start_min) & (df["_min"] <= end_min)
        range_df = df[mask]

        if range_df.empty:
            logger.debug("[ORB] 레인지 분봉 없음 (09:00~09:04)")
            return

        rh = float(range_df["high"].max())
        rl = float(range_df["low"].min())
        rs = rh - rl

        # 기준가: 첫 캔들 시가 (open) 또는 전일 종가
        ref_price = float(range_df.iloc[0]["open"]) if self._prev_day_close <= 0 else self._prev_day_close
        if ref_price <= 0:
            ref_price = rh

        range_pct = rs / ref_price if ref_price > 0 else 0.0
        if range_pct < self._min_range_pct:
            logger.debug(f"[ORB] 레인지 너무 좁음 ({range_pct:.3%} < {self._min_range_pct:.3%})")
            return
        if range_pct > self._max_range_pct:
            logger.debug(f"[ORB] 레인지 너무 넓음 ({range_pct:.3%} > {self._max_range_pct:.3%})")
            return

        self._range_high = rh
        self._range_low = rl
        self._range_size = rs
        self._range_valid = True
        logger.debug(f"[ORB] 레인지 확정: H={rh:.1f} L={rl:.1f} size={rs:.1f} ({range_pct:.3%})")

    # ──────────────────────────── 시그널 생성 ───────────────────────────────

    def generate_signal(
        self,
        candles: pd.DataFrame,
        tick: dict,
        *,
        breakout_price: float | None = None,
    ) -> Signal | None:
        if not self.can_trade():
            return None

        # 레인지 아직 계산 안 됐으면 계산
        self._compute_range(candles)

        if not self._range_valid:
            return None

        now = self._backtest_time if self._backtest_time else datetime.now().time()

        # 진입 시간 창: BLOCK_UNTIL(09:05) ~ entry_deadline
        if now < self.BLOCK_UNTIL or now > self._entry_deadline:
            return None

        price = float(tick.get("price", 0.0))
        if price <= 0:
            return None

        # 돌파 조건: close > range_high + range_size * breakout_buffer
        breakout_threshold = self._range_high + self._range_size * self._breakout_buffer
        if price <= breakout_threshold:
            return None

        # 거래량 필터 (선택)
        if self._use_volume_filter and self._prev_day_volume > 0:
            cum_vol = int(candles["volume"].sum()) if not candles.empty else 0
            if cum_vol < self._prev_day_volume * self._rvol_min:
                return None

        return Signal(
            ticker=tick.get("ticker", ""),
            side="buy",
            price=price,
            strategy="orb",
            reason="orb_breakout",
            context={
                "range_high": self._range_high,
                "range_low": self._range_low,
                "range_size": self._range_size,
            },
        )

    # ──────────────────────────── 손절 / 익절 ───────────────────────────────

    def get_stop_loss(self, entry_price: float) -> float:
        if self._range_valid and self._range_size > 0:
            sl = entry_price - self._range_size * self._sl_ratio
            # range_low 아래로 내려가지 않게 (약간의 여유)
            return max(sl, self._range_low * 0.99)
        return entry_price * 0.95  # fallback 5%

    def get_take_profit(self, entry_price: float) -> float:
        if self._range_valid and self._range_size > 0:
            return entry_price + self._range_size * self._tp_ratio
        return entry_price * 1.05  # fallback 5%
