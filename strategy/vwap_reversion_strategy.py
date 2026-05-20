"""strategy/vwap_reversion_strategy.py — VWAP 리버전(평균회귀) 전략.

09:30~14:00 사이에 현재가가 당일 VWAP 대비 N% 이하로 하락(과매도) 시 진입,
VWAP 복귀(현재가 >= VWAP × (1 + tp_above_vwap)) 시 청산.
손절은 진입가 대비 고정 비율.

설계 원칙
---------
- ORB(09:05~09:30)과 시간대 비중복: 진입창 09:30~14:00
- 모멘텀(추세추종)과 반대 성격(평균회귀) → 시장 국면 분산
- 당일 VWAP = Σ(typical_price × volume) / Σ(volume)
  typical_price = (high + low + close) / 3

파라미터 (config.yaml strategy.vwap_reversion)
----------------------------------------------
entry_deviation  : VWAP 대비 진입 하락폭 (음수, 예: -0.015 = -1.5%)
stop_loss_pct    : 진입가 대비 고정 손절폭 (예: 0.015 = 1.5%)
tp_above_vwap    : 익절 VWAP 초과폭 (예: 0.003 = +0.3%)
entry_start      : 진입 허용 시작 시각 (기본 "09:30")
entry_end        : 진입 허용 종료 시각 (기본 "14:00")
min_volume       : 전일 최소 거래량 (주, 기본 50000)
max_daily_drop   : 당일 허용 최대 등락률 (기본 -0.07 = -7%)
"""
from __future__ import annotations

import re
from datetime import datetime, time

import pandas as pd
from loguru import logger

from config.settings import TradingConfig
from strategy.base_strategy import BaseStrategy, Signal


def _hhmm_to_time(s: str, default: time) -> time:
    m = re.match(r"(\d+):(\d+)", str(s))
    return time(int(m.group(1)), int(m.group(2))) if m else default


class VWAPReversionStrategy(BaseStrategy):
    """VWAP 리버전 전략 — 과매도 진입 후 VWAP 복귀 청산."""

    def __init__(self, config: TradingConfig) -> None:
        super().__init__()
        self._config = config

        # 전일 데이터 (backtester._setup_strategy_day 에서 주입)
        self._prev_day_volume: int = 0
        self._prev_day_close: float = 0.0
        self._prev_day_high: float = 0.0

        # 파라미터
        self._entry_deviation: float = float(getattr(config, "vwap_rev_entry_deviation", -0.015))
        self._stop_loss_pct:   float = float(getattr(config, "vwap_rev_stop_loss_pct",   0.015))
        self._tp_above_vwap:   float = float(getattr(config, "vwap_rev_tp_above_vwap",   0.003))
        self._min_prev_volume: int   = int(getattr(config,   "vwap_rev_min_prev_volume",  50000))
        self._max_daily_drop:  float = float(getattr(config, "vwap_rev_max_daily_drop",  -0.07))

        self._entry_start: time = _hhmm_to_time(
            getattr(config, "vwap_rev_entry_start", "09:30"), time(9, 30)
        )
        self._entry_end: time = _hhmm_to_time(
            getattr(config, "vwap_rev_entry_end", "14:00"), time(14, 0)
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
        self._prev_day_high = high

    def set_prev_day_volume(self, volume: int) -> None:
        self._prev_day_volume = volume

    def set_prev_day_candles(self, candles: pd.DataFrame | None) -> None:
        pass

    def reset(self) -> None:
        super().reset()

    # ──────────────────────────── VWAP 계산 ─────────────────────────────────

    def _compute_vwap(self, candles: pd.DataFrame) -> float:
        """당일 분봉 DataFrame에서 누적 VWAP 계산.

        typical_price = (high + low + close) / 3
        VWAP = Σ(typical_price × volume) / Σ(volume)
        """
        if candles.empty:
            return 0.0
        vol = candles["volume"].values
        cum_vol = float(vol.sum())
        if cum_vol <= 0:
            return 0.0
        tp = (
            candles["high"].values
            + candles["low"].values
            + candles["close"].values
        ) / 3.0
        return float((tp * vol).sum() / cum_vol)

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

        now = self._backtest_time if self._backtest_time else datetime.now().time()
        if not (self._entry_start <= now <= self._entry_end):
            return None

        # 전일 거래량 필터
        if self._min_prev_volume > 0 and self._prev_day_volume > 0:
            if self._prev_day_volume < self._min_prev_volume:
                return None

        price = float(tick.get("price", 0.0))
        if price <= 0:
            return None

        if candles.empty:
            return None

        vwap = self._compute_vwap(candles)
        if vwap <= 0:
            return None

        # 진입 조건: 현재가 <= VWAP × (1 + entry_deviation)  [entry_deviation < 0]
        threshold = vwap * (1.0 + self._entry_deviation)
        if price > threshold:
            return None

        deviation_pct = (price - vwap) / vwap

        logger.debug(
            f"[VWAP-REV] 진입 신호: vwap={vwap:.1f} price={price:.1f} "
            f"dev={deviation_pct:.3%} threshold={threshold:.1f}"
        )

        return Signal(
            ticker=tick.get("ticker", ""),
            side="buy",
            price=price,
            strategy="vwap_reversion",
            reason="vwap_reversion_entry",
            context={
                "vwap":          vwap,
                "deviation_pct": deviation_pct,
                "threshold":     threshold,
            },
        )

    # ──────────────────────────── 손절 / 익절 ───────────────────────────────

    def get_stop_loss(self, entry_price: float) -> float:
        """고정 비율 손절."""
        return entry_price * (1.0 - self._stop_loss_pct)

    def get_take_profit(self, entry_price: float) -> float:
        """익절은 VWAP 복귀 기반(동적)이라 엔진에서 직접 처리.
        실거래 엔진 호환을 위해 fallback 값 반환 (손절폭 × 2)."""
        return entry_price * (1.0 + self._stop_loss_pct * 2.0)
