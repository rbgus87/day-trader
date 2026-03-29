"""strategy/momentum_strategy.py — 모멘텀 브레이크아웃 v2 (F-STR-03).

전일 고점 돌파 → 리테스트 → 재돌파 확인 후 진입.
3단계 상태 머신 + 동적 손절 + VWAP 방향 필터.
"""

from datetime import datetime, time

import pandas as pd
from loguru import logger

from config.settings import TradingConfig
from strategy.base_strategy import BaseStrategy, Signal

STATE_WAITING = "waiting"
STATE_RETEST = "retest"
STATE_CONFIRMED = "confirmed"


class MomentumStrategy(BaseStrategy):
    """전일 고점 돌파 + 리테스트 + 재돌파 확인 후 매수 (v2)."""

    def __init__(self, config: TradingConfig) -> None:
        self._config = config
        self._prev_day_high: float = 0.0
        self._prev_day_volume: int = 0
        self._state: str = STATE_WAITING
        self._breakout_price: float = 0.0
        self._breakout_time: datetime | None = None
        self._retest_low: float = 0.0
        self.configure_multi_trade(
            max_trades=config.max_trades_per_day,
            cooldown_minutes=config.cooldown_minutes,
        )

    # ------------------------------------------------------------------
    # 전일 기준값 설정
    # ------------------------------------------------------------------

    def set_prev_day_data(self, high: float, volume: int) -> None:
        """전일 고가·거래량 기준값 저장."""
        self._prev_day_high = high
        self._prev_day_volume = volume

    # ------------------------------------------------------------------
    # 상태 머신
    # ------------------------------------------------------------------

    def _current_time(self) -> datetime:
        """현재 시각 (백테스트 모드 지원)."""
        if self._backtest_time:
            return datetime.combine(datetime.now().date(), self._backtest_time)
        return datetime.now()

    def _reset_state(self) -> None:
        """상태 머신 초기화."""
        self._state = STATE_WAITING
        self._breakout_price = 0.0
        self._breakout_time = None
        self._retest_low = 0.0

    # ------------------------------------------------------------------
    # 추상 메서드 구현
    # ------------------------------------------------------------------

    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        """매수 신호 생성 (3단계 상태 머신).

        STATE_WAITING:
          현재가 > 전일 고점 → 돌파가 기록, STATE_RETEST 전환
        STATE_RETEST:
          타임아웃(30분) 체크
          전일 고점 ±retest_band 이내 되돌림 → retest_low 기록
          리테스트 후 재돌파 + 양봉 → STATE_CONFIRMED
        STATE_CONFIRMED:
          매수 Signal 발생
        """
        if not self.can_trade():
            return None

        if candles is None or candles.empty:
            return None

        current_price: float = tick["price"]

        # 거래량 필터 (캔들 누적 거래량 >= 전일 × volume_ratio)
        cum_volume: float = candles["volume"].sum()
        required_volume: float = self._prev_day_volume * self._config.momentum_volume_ratio
        if cum_volume < required_volume:
            return None

        # ── STATE_WAITING: 돌파 대기 ──
        if self._state == STATE_WAITING:
            if current_price > self._prev_day_high:
                self._breakout_price = current_price
                self._breakout_time = self._current_time()
                self._retest_low = current_price
                self._state = STATE_RETEST
                logger.debug(
                    f"[Momentum] 돌파 감지: {tick['ticker']} price={current_price} "
                    f"prev_high={self._prev_day_high}"
                )
            return None

        # ── STATE_RETEST: 리테스트 대기 ──
        if self._state == STATE_RETEST:
            # 타임아웃 체크
            if self._breakout_time:
                elapsed = (self._current_time() - self._breakout_time).total_seconds() / 60
                if elapsed > self._config.momentum_retest_timeout_min:
                    logger.debug(f"[Momentum] 리테스트 타임아웃 ({elapsed:.0f}분)")
                    self._reset_state()
                    return None

            retest_band = self._prev_day_high * self._config.momentum_retest_band_pct
            upper = self._prev_day_high + retest_band

            # 되돌림 감지: 캔들의 low가 밴드 상단 이하로 내려온 적 있으면 리테스트 인정
            candle_low = candles.iloc[-1]["low"] if "low" in candles.columns else current_price
            if candle_low <= upper:
                if candle_low < self._retest_low:
                    self._retest_low = candle_low
                # current_price도 체크 (틱 기반 실시간 지원)
                elif current_price <= upper and current_price < self._retest_low:
                    self._retest_low = current_price

            # 재돌파 확인: 리테스트 후 close > prev_high + 양봉
            if self._retest_low < upper:
                # 리테스트가 일어남 (retest_low가 밴드 내로 내려온 적 있음)
                if candles.iloc[-1]["close"] > self._prev_day_high:
                    # 양봉 확인
                    if candles.iloc[-1]["close"] > candles.iloc[-1]["open"]:
                        self._state = STATE_CONFIRMED

            if self._state != STATE_CONFIRMED:
                return None

        # ── STATE_CONFIRMED: 신호 발생 ──
        if self._state == STATE_CONFIRMED:
            # VWAP 방향 필터
            if self._config.momentum_vwap_filter:
                if "vwap" in candles.columns:
                    vwap = candles.iloc[-1].get("vwap")
                    if vwap and vwap > 0 and current_price <= vwap:
                        logger.debug(f"[Momentum] VWAP 하회 차단: price={current_price} vwap={vwap}")
                        self._reset_state()
                        return None

            # 마지막 캔들 종가 > 전일 고점 확인
            if candles.iloc[-1]["close"] <= self._prev_day_high:
                self._reset_state()
                return None

            logger.info(
                f"모멘텀 v2 매수 신호: {tick['ticker']} price={current_price} "
                f"prev_high={self._prev_day_high} retest_low={self._retest_low:.0f} "
                f"cum_vol={cum_volume:,.0f}"
            )

            self._reset_state()

            return Signal(
                ticker=tick["ticker"],
                side="buy",
                price=current_price,
                strategy="momentum",
                reason=f"전일 고점({self._prev_day_high:,.0f}) 리테스트 후 재돌파 확인",
            )

        return None

    def get_stop_loss(self, entry_price: float) -> float:
        """동적 손절: max(retest_low - 0.3%, entry × (1 + momentum_stop_loss_pct))."""
        fixed_sl = entry_price * (1 + self._config.momentum_stop_loss_pct)
        if self._retest_low > 0:
            dynamic_sl = self._retest_low * (1 - 0.003)
            return max(dynamic_sl, fixed_sl)
        return fixed_sl

    def get_take_profit(self, entry_price: float) -> tuple[float, float]:
        """(tp1, tp2): tp1 = 진입가 × (1 + tp1_pct), tp2 = 0 (트레일링 스톱)."""
        tp1 = entry_price * (1 + self._config.tp1_pct)
        return tp1, 0

    def reset(self) -> None:
        """일별 리셋 (상태 머신 + 기준값 유지)."""
        super().reset()
        self._reset_state()
