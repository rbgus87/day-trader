"""tests/test_indicators.py — core.indicators 단위 테스트."""

import pandas as pd

from core.indicators import calculate_atr, calculate_atr_pct


def test_atr_basic():
    """ATR 계산 기본 동작 — 충분한 데이터가 있으면 마지막 값이 양수."""
    df = pd.DataFrame(
        {
            "high": [10, 11, 12, 11, 13, 14, 15, 14, 16, 15, 17, 16, 18, 17, 19],
            "low": [8, 9, 10, 9, 11, 12, 13, 12, 14, 13, 15, 14, 16, 15, 17],
            "close": [9, 10, 11, 10, 12, 13, 14, 13, 15, 14, 16, 15, 17, 16, 18],
        }
    )
    atr = calculate_atr(df, length=14)
    assert len(atr) == len(df)
    # 마지막 값은 유효
    assert pd.notna(atr.iloc[-1])
    assert atr.iloc[-1] > 0


def test_atr_pct():
    """ATR%는 ATR/close*100."""
    atr = pd.Series([1.0, 1.5, 2.0])
    close = pd.Series([100.0, 100.0, 100.0])
    pct = calculate_atr_pct(atr, close)
    assert pct.iloc[0] == 1.0
    assert pct.iloc[1] == 1.5
    assert pct.iloc[2] == 2.0


def test_atr_insufficient_data():
    """length+1 미만이면 빈 Series."""
    df = pd.DataFrame({"high": [10, 11], "low": [8, 9], "close": [9, 10]})
    atr = calculate_atr(df, length=14)
    assert len(atr) == 0
