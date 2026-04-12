"""core/indicators.py — 공통 기술 지표 (pandas_ta 기반).

일봉 또는 분봉 DataFrame으로 ATR, ATR% 등을 계산한다.
"""

import pandas as pd
import pandas_ta as ta


def calculate_atr(daily_df: pd.DataFrame, length: int = 14) -> pd.Series:
    """일봉 DataFrame에서 ATR(원단위) 계산.

    Args:
        daily_df: columns 최소 high, low, close 포함
        length:   ATR 기간 (기본 14)

    Returns:
        ATR 값이 담긴 Series. 데이터 부족 시 빈 Series 반환.
    """
    if len(daily_df) < length + 1:
        return pd.Series(dtype=float)

    atr = ta.atr(
        high=daily_df["high"],
        low=daily_df["low"],
        close=daily_df["close"],
        length=length,
    )
    # pandas_ta가 None을 반환할 가능성에 대한 방어
    if atr is None:
        return pd.Series(dtype=float)
    return atr


def calculate_atr_pct(atr: pd.Series, close: pd.Series) -> pd.Series:
    """ATR을 종가 대비 % 비율로 변환."""
    return (atr / close) * 100
