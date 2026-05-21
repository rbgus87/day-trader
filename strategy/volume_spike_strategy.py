"""strategy/volume_spike_strategy.py — 거래량 폭발(Volume Spike) 전략.

평소 대비 거래량이 급증하는 분봉 = 이벤트(뉴스/공시/대량주문) 발생 신호.
양봉(close > open) 확인 후 추세 방향으로 진입.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from loguru import logger

from config.settings import TradingConfig
from strategy.base_strategy import BaseStrategy, Signal


class VolumeSpikeStrategy(BaseStrategy):
    """거래량 폭발 전략 — 평소 대비 N배 급증 양봉에서 진입."""

    def __init__(self, config: TradingConfig) -> None:
        super().__init__()
        self._config = config

        self._lookback        = int(getattr(config, "vs_lookback_minutes", 10))
        self._spike_ratio     = float(getattr(config, "vs_spike_ratio", 5.0))
        self._sl_pct          = float(getattr(config, "vs_sl_pct", 0.02))
        self._tp_pct          = float(getattr(config, "vs_tp_pct", 0.03))
        self._entry_start     = str(getattr(config, "vs_entry_start", "09:30"))
        self._entry_end       = str(getattr(config, "vs_entry_end", "13:00"))
        self._min_prev_volume = int(getattr(config, "vs_min_prev_volume", 50000))
        self._min_spike_volume = int(getattr(config, "vs_min_spike_volume", 10000))

        # backtester._setup_strategy_day 에서 주입
        self._prev_day_close: float = 0.0
        self._prev_day_volume: int = 0

        self.configure_multi_trade(max_trades=1, cooldown_minutes=999)

    # ──────────────────────────────── 데이터 주입 ────────────────────────────

    def set_prev_day_data(self, high: float, volume: int, close: float = 0.0) -> None:
        self._prev_day_volume = volume
        self._prev_day_close = close

    def set_prev_day_volume(self, volume: int) -> None:
        self._prev_day_volume = volume

    def set_prev_day_candles(self, candles: "pd.DataFrame | None") -> None:
        pass

    # ──────────────────────────────── 급증 감지 ──────────────────────────────

    @staticmethod
    def detect_spike(
        volumes: "pd.Series",
        idx: int,
        lookback: int,
        spike_ratio: float,
    ) -> bool:
        """idx 분봉 거래량이 직전 lookback 평균 대비 spike_ratio 이상인지 확인."""
        if idx < lookback:
            return False
        avg_vol = volumes.iloc[idx - lookback : idx].mean()
        if avg_vol <= 0:
            return False
        return float(volumes.iloc[idx]) >= avg_vol * spike_ratio

    # ──────────────────────────────── 시그널 생성 ────────────────────────────

    def generate_signal(
        self,
        candles: pd.DataFrame,
        ticker: str = "",
        **kwargs: Any,
    ) -> Signal | None:
        if not getattr(self._config, "vs_enabled", False):
            return None
        if self._prev_day_volume < self._min_prev_volume:
            return None
        if candles.empty or len(candles) <= self._lookback:
            return None
        if not self.can_trade():
            return None

        now = self.get_backtest_time()
        if now is None:
            latest_ts = candles.iloc[-1].get("ts")
            if latest_ts is not None:
                now = latest_ts.time() if hasattr(latest_ts, "time") else None
        if now is None:
            return None

        from datetime import time
        try:
            h, m = map(int, self._entry_start.split(":"))
            start_t = time(h, m)
            h, m = map(int, self._entry_end.split(":"))
            end_t = time(h, m)
        except Exception:
            return None

        now_t = now if isinstance(now, time) else (now.time() if hasattr(now, "time") else None)
        if now_t is None or not (start_t <= now_t <= end_t):
            return None

        idx = len(candles) - 1
        latest = candles.iloc[-1]
        vol_i   = float(latest["volume"])
        close_i = float(latest["close"])
        open_i  = float(latest["open"])

        if vol_i < self._min_spike_volume:
            return None
        if not self.detect_spike(candles["volume"], idx, self._lookback, self._spike_ratio):
            return None
        if close_i <= open_i:
            return None

        logger.debug(
            f"[VS] 거래량 폭발 신호 {ticker}  vol={vol_i:,.0f}  "
            f"close={close_i:.1f}"
        )
        return Signal(
            ticker=ticker,
            side="buy",
            price=close_i,
            strategy="volume_spike",
            reason="volume_spike",
            context={"vol": vol_i, "spike_ratio": self._spike_ratio},
        )

    def get_stop_loss(self, entry_price: float) -> float:
        return entry_price * (1.0 - self._sl_pct)

    def get_take_profit(self, entry_price: float) -> float:
        return entry_price * (1.0 + self._tp_pct)
