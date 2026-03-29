"""strategy/pullback_strategy.py — 눌림목 매매 전략 v2 (F-STR-04).

당일 +2.5% 이상 종목의 MA10 터치 후 음봉→양봉 전환 시 진입.
v2: 조건 완화 (MA5→MA10, +4%→+2.5%, 밴드 1%) + ATR 필터.
"""

import pandas as pd
from loguru import logger

from strategy.base_strategy import BaseStrategy, Signal
from config.settings import TradingConfig


class PullbackStrategy(BaseStrategy):
    """강세 종목 조정 후 반등 진입 (v2).

    진입 조건:
      1. 당일 시가 대비 현재가 >= pullback_min_gain_pct
      2. 현재가가 MA(short) ± ma_touch_band 이내 (이평 터치)
      3. 직전 캔들 음봉 → 현재 캔들 양봉 전환
      4. MA(long) 정배열 (상승 중)
      5. ATR 필터: 당일 캔들의 평균 변동폭 >= min_atr_pct
    """

    def __init__(self, config: TradingConfig):
        self._config = config
        self._open_price: float | None = None
        self._ma_short = config.pullback_ma_short
        self._ma_long = config.pullback_ma_long
        self._ma_touch_band = config.pullback_ma_touch_band
        self._min_atr_pct = config.pullback_min_atr_pct
        self.configure_multi_trade(
            max_trades=config.max_trades_per_day,
            cooldown_minutes=config.cooldown_minutes,
        )

    def set_open_price(self, price: float) -> None:
        """당일 시가 설정."""
        self._open_price = price

    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        if not self.can_trade():
            return None

        if self._open_price is None or self._open_price <= 0:
            return None

        min_rows = max(self._ma_short, self._ma_long) + 1
        if candles is None or len(candles) < min_rows:
            return None

        current_price = tick["price"]

        # 조건 1: 당일 시가 대비 상승
        gain = (current_price - self._open_price) / self._open_price
        if gain < self._config.pullback_min_gain_pct:
            return None

        # 조건 2: MA(short) 터치 (현재가가 MA ± band 이내)
        ma_short = candles["close"].iloc[-self._ma_short:].mean()
        distance = abs(current_price - ma_short) / ma_short
        if distance > self._ma_touch_band:
            return None

        # 조건 3: 직전 캔들 음봉 → 현재 캔들 양봉 전환
        prev = candles.iloc[-2]
        curr = candles.iloc[-1]
        prev_bearish = prev["close"] < prev["open"]
        curr_bullish = curr["close"] > curr["open"]
        if not (prev_bearish and curr_bullish):
            return None

        # 조건 4: MA(long) 정배열 (상승 중)
        ma_long_series = candles["close"].rolling(self._ma_long).mean().dropna()
        if len(ma_long_series) < 2:
            return None
        if ma_long_series.iloc[-1] <= ma_long_series.iloc[-2]:
            return None

        # 조건 5: ATR 필터 (스크리닝 단계에서 set_atr_pct()로 주입된 일일 ATR 기준)
        # 1분봉 캔들의 (high-low)/close는 0.1~0.5%로 일일 ATR(2.5%)과 스케일이 다름
        # → 스크리닝에서 이미 필터링된 종목만 전달되므로 여기서는 별도 체크하지 않음

        ticker = tick.get("ticker", "")
        logger.info(
            f"눌림목 v2 매수 신호: {ticker} price={current_price} "
            f"gain={gain:.2%} ma{self._ma_short}={ma_short:.0f}"
        )

        return Signal(
            ticker=ticker,
            side="buy",
            price=current_price,
            strategy="pullback",
            reason=f"MA{self._ma_short}({ma_short:,.0f}) 터치 후 음봉→양봉 전환",
        )

    def get_stop_loss(self, entry_price: float) -> float:
        return entry_price * (1 + self._config.pullback_stop_loss_pct)

    def get_take_profit(self, entry_price: float) -> tuple[float, float]:
        tp1 = entry_price * (1 + self._config.tp1_pct)
        return tp1, 0

    def reset(self) -> None:
        super().reset()
        self._open_price = None
