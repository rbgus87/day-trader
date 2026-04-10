"""strategy/flow_strategy.py — 수급추종 전략 Phase 1 (거래량 급증 기반).

체결강도 데이터가 확보되기 전까지 거래량 급증 + 가격 상승 + VWAP 필터로
기관/외국인 수급 쏠림을 근사 포착한다.
"""

from datetime import time

import pandas as pd
from loguru import logger

from config.settings import TradingConfig
from strategy.base_strategy import BaseStrategy, Signal


class FlowStrategy(BaseStrategy):
    """수급추종 전략 — 거래량 급증 + 가격 상승 + VWAP 필터."""

    def __init__(self, config: TradingConfig) -> None:
        super().__init__()
        self._config = config
        self._volume_history: list[int] = []
        self._volume_surge_ratio = config.flow_volume_surge_ratio
        self._vwap_filter = config.flow_vwap_filter
        self.configure_multi_trade(
            max_trades=config.max_trades_per_day,
            cooldown_minutes=config.cooldown_minutes,
        )
        # 시간 제한
        self.BLOCK_UNTIL = time(9, 30)
        self.MARKET_CLOSE = time(14, 30)

    def on_candle_5m(self, candle: dict) -> None:
        """5분봉 완성 시 거래량 히스토리 업데이트."""
        self._volume_history.append(int(candle.get("volume", 0)))
        if len(self._volume_history) > 20:
            self._volume_history = self._volume_history[-20:]

    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        if not self.can_trade():
            return None

        # 최소 4개 5분봉 필요 (20분 평균)
        if len(self._volume_history) < 4:
            return None

        # 거래량 급증 체크
        avg_vol = sum(self._volume_history[-4:]) / 4
        current_vol = self._volume_history[-1] if self._volume_history else 0
        if avg_vol <= 0 or current_vol < avg_vol * self._volume_surge_ratio:
            return None

        # 캔들 데이터 확인
        if candles is None or len(candles) < 2:
            return None

        # 가격 상승 확인 (현재 캔들 > 이전 캔들)
        if candles.iloc[-1]["close"] <= candles.iloc[-2]["close"]:
            return None

        # VWAP 필터
        if self._vwap_filter and "vwap" in candles.columns:
            vwap = candles.iloc[-1].get("vwap")
            if vwap and vwap > 0 and candles.iloc[-1]["close"] <= vwap:
                return None

        # 당일 시가 대비 상승
        if candles.iloc[-1]["close"] <= candles.iloc[0]["open"]:
            return None

        # 양봉 확인
        if candles.iloc[-1]["close"] <= candles.iloc[-1]["open"]:
            return None

        surge_ratio = current_vol / avg_vol
        return Signal(
            ticker=tick["ticker"],
            side="buy",
            price=tick["price"],
            strategy="flow",
            reason=f"거래량 급증 {surge_ratio:.1f}배 + VWAP 상회",
        )

    def get_stop_loss(self, entry_price: float) -> float:
        return entry_price * (1 + self._config.flow_stop_loss_pct)

    def get_take_profit(self, entry_price: float) -> tuple[float, float]:
        tp1 = entry_price * (1 + self._config.tp1_pct)
        return tp1, 0

    def reset(self) -> None:
        super().reset()
        self._volume_history.clear()
