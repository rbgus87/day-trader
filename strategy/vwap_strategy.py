"""DEPRECATED: 백테스트 결과 4종목 전부 PF<1.0, 2026-03-30 폐기.

strategy/vwap_strategy.py — VWAP 회귀 전략 (F-STR-02).
비교 백테스트용으로 보존. 실전 파이프라인에서는 사용하지 않음.

매수 조건:
  1. 가격이 VWAP-1σ 아래로 터치한 후 VWAP 위로 반등
  2. RSI(14) 40~60 범위

손절: entry_price * (1 + vwap_stop_loss_pct)  → -1.2%
익절: (entry_price * (1 + tp1_pct), 0)        → +2.0%
"""

import pandas as pd
import numpy as np
from loguru import logger

from strategy.base_strategy import BaseStrategy, Signal
from config.settings import TradingConfig


# ---------------------------------------------------------------------------
# 보조 함수
# ---------------------------------------------------------------------------

def _calc_vwap_and_std(candles: pd.DataFrame) -> tuple[float, float]:
    """당일 캔들 데이터로 VWAP 및 가격 표준편차(σ) 계산.

    Parameters
    ----------
    candles : pd.DataFrame
        컬럼: open, high, low, close, volume (최소 1행)

    Returns
    -------
    vwap : float
    std  : float   — VWAP 상하 밴드 폭에 사용
    """
    tp = (candles["high"] + candles["low"] + candles["close"]) / 3
    cum_tp_vol = (tp * candles["volume"]).cumsum()
    cum_vol = candles["volume"].cumsum()

    # 누적 거래량이 0이면 0 반환 방지
    cum_vol = cum_vol.replace(0, np.nan)
    vwap_series = cum_tp_vol / cum_vol

    vwap = float(vwap_series.iloc[-1]) if not vwap_series.empty else float(tp.mean())

    # σ: 전형가격(typical price)의 표준편차
    std = float(tp.std(ddof=1)) if len(tp) >= 2 else 0.0

    return vwap, std


def _calc_rsi(closes: pd.Series, period: int = 14) -> float:
    """Wilder 평활 방식 RSI 계산.

    pandas_ta 가 설치되어 있으면 이를 우선 사용하고,
    없으면 순수 Python/pandas 로 계산합니다.
    """
    # pandas_ta 우선 시도
    try:
        import pandas_ta as ta  # noqa: PLC0415
        result = ta.rsi(closes, length=period)
        if result is not None and not result.empty:
            val = result.iloc[-1]
            if not np.isnan(val):
                return float(val)
    except Exception:
        pass

    # 단순 RSI 폴백
    if len(closes) < period + 1:
        return 50.0  # 데이터 부족 → 중립

    delta = closes.diff().dropna()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    # 초기 평균
    avg_gain = gain.iloc[:period].mean()
    avg_loss = loss.iloc[:period].mean()

    # Wilder 평활
    for g, l in zip(gain.iloc[period:], loss.iloc[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


# ---------------------------------------------------------------------------
# 전략 클래스
# ---------------------------------------------------------------------------

class VwapStrategy(BaseStrategy):
    """VWAP 하단 터치 후 반등 매수 전략."""

    def __init__(self, config: TradingConfig):
        self._config = config
        self._touched_lower_band: bool = False
        self.configure_multi_trade(
            max_trades=config.max_trades_per_day,
            cooldown_minutes=config.cooldown_minutes,
        )

    # ------------------------------------------------------------------
    # BaseStrategy 인터페이스
    # ------------------------------------------------------------------

    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        """VWAP 반등 신호 생성.

        Parameters
        ----------
        candles : pd.DataFrame
            당일 분봉 데이터. 컬럼: open, high, low, close, volume
        tick : dict
            최신 틱. 키: ticker, price
        """
        if not self.can_trade():
            return None

        if candles is None or candles.empty or len(candles) < 2:
            return None

        current_price: float = float(tick["price"])
        vwap, std = _calc_vwap_and_std(candles)

        lower_band = vwap - std
        upper_band = vwap + std  # noqa: F841  (향후 익절 참조용)

        # --- 1단계: VWAP-1σ 터치 감지 ---
        # 직전 캔들의 저가(low)가 lower_band 이하이면 "터치"로 판정
        prev_low = float(candles.iloc[-1]["low"])
        if prev_low <= lower_band:
            self._touched_lower_band = True

        if not self._touched_lower_band:
            return None

        # --- 2단계: 현재 가격이 VWAP 위로 반등했는지 확인 ---
        if current_price <= vwap:
            return None

        # --- 3단계: RSI 필터 (40~60) ---
        closes = candles["close"]
        rsi = _calc_rsi(closes)

        if not (self._config.vwap_rsi_low <= rsi <= self._config.vwap_rsi_high):
            logger.debug(
                f"VWAP 반등 감지, RSI 범위 벗어남: RSI={rsi:.1f} "
                f"(허용: {self._config.vwap_rsi_low}~{self._config.vwap_rsi_high})"
            )
            return None

        # 신호 발생
        logger.info(
            f"VWAP 매수 신호: {tick['ticker']} price={current_price:,.0f} "
            f"VWAP={vwap:,.0f} σ={std:,.1f} RSI={rsi:.1f}"
        )

        return Signal(
            ticker=tick["ticker"],
            side="buy",
            price=current_price,
            strategy="vwap",
            reason=f"VWAP({vwap:,.0f}) 하단 터치 후 반등 RSI={rsi:.1f}",
        )

    def get_stop_loss(self, entry_price: float) -> float:
        """손절가: entry_price * (1 + vwap_stop_loss_pct) = -1.2%."""
        return entry_price * (1 + self._config.vwap_stop_loss_pct)

    def get_take_profit(self, entry_price: float) -> tuple[float, float]:
        """(tp1, tp2). tp1 = entry_price * (1 + tp1_pct) = +2.0%. tp2 = 트레일링."""
        tp1 = entry_price * (1 + self._config.tp1_pct)
        tp2 = 0.0  # 트레일링 스톱으로 관리
        return tp1, tp2

    # ------------------------------------------------------------------
    # 유틸리티
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """일일 초기화."""
        super().reset()
        self._touched_lower_band = False
