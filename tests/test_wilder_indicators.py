"""tests/test_wilder_indicators.py — 자체 Wilder ADX/ATR vs pandas_ta 동등성 검증.

pandas_ta가 dev 환경에 남아 있는 동안만 회귀 게이트로 동작.
패키지 미설치 환경에서는 자동 skip — 운영 EXE에는 pandas_ta 미포함.

허용 오차: 1e-4 (절대값). Wilder smoothing 동일 알고리즘이라면 부동소수 오차 수준만
발생해야 한다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.indicators import wilder_adx, wilder_atr

ta = pytest.importorskip("pandas_ta", reason="pandas_ta 미설치 환경 — 동등성 검증 skip")


def _make_ohlc(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base = 100 + np.cumsum(rng.normal(0, 1.0, n))
    high = base + rng.uniform(0.5, 3.0, n)
    low = base - rng.uniform(0.5, 3.0, n)
    close = base + rng.uniform(-1.0, 1.0, n)
    # high/low 정합성 보정
    high = np.maximum.reduce([high, close, low + 0.01])
    low = np.minimum.reduce([low, close, high - 0.01])
    return pd.DataFrame({"high": high, "low": low, "close": close})


@pytest.mark.parametrize("seed,n,length", [
    (1, 60, 14),
    (2, 80, 14),
    (3, 200, 14),
    (4, 60, 7),
    (5, 200, 21),
])
def test_wilder_atr_matches_pandas_ta(seed, n, length):
    df = _make_ohlc(n, seed)
    expected = ta.atr(df["high"], df["low"], df["close"], length=length)
    actual = wilder_atr(df["high"], df["low"], df["close"], length=length)
    # 첫 length개는 양쪽 모두 NaN — 유효 구간만 비교
    valid = expected.notna() & actual.notna()
    assert valid.sum() > 0
    diff = (expected[valid] - actual[valid]).abs().max()
    assert diff < 1e-4, f"max diff {diff} (seed={seed}, n={n}, length={length})"


@pytest.mark.parametrize("seed,n,length", [
    (1, 60, 14),
    (2, 80, 14),
    (3, 200, 14),
    (4, 60, 7),
    (5, 200, 21),
])
def test_wilder_adx_matches_pandas_ta(seed, n, length):
    df = _make_ohlc(n, seed)
    expected = ta.adx(df["high"], df["low"], df["close"], length=length)
    actual = wilder_adx(df["high"], df["low"], df["close"], length=length)

    col = f"ADX_{length}"
    assert col in actual.columns
    valid = expected[col].notna() & actual[col].notna()
    # ADX는 RMA를 두 번 거치므로 유효 구간이 length*2 이후부터 — 데이터에 따라 짧을 수 있음
    if valid.sum() == 0:
        pytest.skip("ADX 유효 샘플 부족")
    diff = (expected[col][valid] - actual[col][valid]).abs().max()
    assert diff < 1e-4, f"ADX max diff {diff} (seed={seed}, n={n}, length={length})"

    # DMP / DMN도 유효 구간에서 일치
    for sub in (f"DMP_{length}", f"DMN_{length}"):
        if sub in expected.columns and sub in actual.columns:
            v = expected[sub].notna() & actual[sub].notna()
            if v.sum() > 0:
                d = (expected[sub][v] - actual[sub][v]).abs().max()
                assert d < 1e-4, f"{sub} max diff {d} (seed={seed}, n={n})"


def test_wilder_atr_insufficient_data_returns_all_nan():
    """length+1보다 짧은 입력은 전부 NaN 반환 — 호출자 방어 위해 빈 Series가 아닌 동일 길이."""
    df = _make_ohlc(8, seed=10)
    out = wilder_atr(df["high"], df["low"], df["close"], length=14)
    assert len(out) == len(df)
    assert out.isna().all()
