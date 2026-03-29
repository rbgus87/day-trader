"""strategy/pullback_strategy.py — 눌림목 매매 전략 (F-STR-04)."""

import pandas as pd
from loguru import logger

from strategy.base_strategy import BaseStrategy, Signal
from config.settings import TradingConfig


class PullbackStrategy(BaseStrategy):
    """당일 +3% 이상 종목의 5분 이평 터치 후 음봉→양봉 전환 시 진입.

    진입 조건:
      1. 당일 시가 대비 현재가 +3% 이상
      2. 현재가가 5캔들 이동평균 ±0.5% 이내 (이평 터치)
      3. 직전 캔들 음봉 → 현재 캔들 양봉 전환
      4. 20캔들 이동평균 정배열 (상승 중)
    손절: 진입가 * (1 + pullback_stop_loss_pct) = -1.5%
    익절: 진입가 * (1 + tp1_pct) = +2%
    """

    MA5_WINDOW = 5
    MA20_WINDOW = 20
    MA_TOUCH_BAND = 0.005  # ±0.5%

    def __init__(self, config: TradingConfig):
        self._config = config
        self._open_price: float | None = None
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

        if candles is None or len(candles) < self.MA20_WINDOW + 1:
            return None

        current_price = tick["price"]

        # 조건 1: 당일 +3% 이상
        gain = (current_price - self._open_price) / self._open_price
        if gain < self._config.pullback_min_gain_pct:
            logger.debug(f"눌림목: 시가 대비 상승 부족 gain={gain:.2%} < {self._config.pullback_min_gain_pct:.2%}")
            return None

        # 조건 2: 5캔들 이평 터치 (현재가가 MA5 ±0.5% 이내)
        ma5 = candles["close"].iloc[-self.MA5_WINDOW:].mean()
        distance = abs(current_price - ma5) / ma5
        if distance > self.MA_TOUCH_BAND:
            logger.debug(f"눌림목: 5MA 터치 미달 distance={distance:.2%}")
            return None

        # 조건 3: 직전 캔들 음봉 → 현재 캔들 양봉 전환
        prev = candles.iloc[-2]
        curr = candles.iloc[-1]
        prev_bearish = prev["close"] < prev["open"]
        curr_bullish = curr["close"] > curr["open"]
        if not (prev_bearish and curr_bullish):
            logger.debug("눌림목: 음봉→양봉 전환 패턴 미충족")
            return None

        # 조건 4: 20캔들 이동평균 정배열 (MA20이 상승 중)
        ma20_series = candles["close"].rolling(self.MA20_WINDOW).mean().dropna()
        if len(ma20_series) < 2:
            return None
        ma20_ascending = ma20_series.iloc[-1] > ma20_series.iloc[-2]
        if not ma20_ascending:
            logger.debug("눌림목: MA20 정배열 미충족")
            return None

        ticker = tick.get("ticker", "")
        logger.info(
            f"눌림목 매수 신호: {ticker} price={current_price} "
            f"gain={gain:.2%} ma5={ma5:.0f} ma20={ma20_series.iloc[-1]:.0f}"
        )

        return Signal(
            ticker=ticker,
            side="buy",
            price=current_price,
            strategy="pullback",
            reason=f"5MA({ma5:,.0f}) 터치 후 음봉→양봉 전환, MA20 정배열",
        )

    def get_stop_loss(self, entry_price: float) -> float:
        """손절가: 진입가 * (1 - 1.5%)."""
        return entry_price * (1 + self._config.pullback_stop_loss_pct)

    def get_take_profit(self, entry_price: float) -> tuple[float, float]:
        """익절가: (tp1=+2%, tp2=0 트레일링 스톱으로 관리)."""
        tp1 = entry_price * (1 + self._config.tp1_pct)
        tp2 = 0
        return tp1, tp2

    def reset(self) -> None:
        """일일 초기화."""
        super().reset()
        self._open_price = None
