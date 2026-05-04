"""core/indicators.py — 공통 기술 지표.

일봉 또는 분봉 DataFrame으로 ATR, ATR% 등을 계산한다.
ticker_atr DB에서 최신 ATR%를 조회하고 ATR 기반 손절가를 산출하는 유틸도 제공한다.

ADX/ATR은 Wilder smoothing(RMA) 기반 직접 구현 — pandas_ta 의존 제거로 EXE
크기·빌드 시간을 줄이기 위해. 알고리즘은 pandas_ta(mamode='rma')와 동등.
"""

import sqlite3

import numpy as np
import pandas as pd


def _wilder_rma(s: pd.Series, length: int) -> pd.Series:
    """ATR용 RMA — pandas_ta atr(presma=True)의 동작 복제.

    인덱스 ``length-1``에 ``s.iloc[0:length].mean()`` (NaN-skip 평균)을 주입하고,
    이후 ``rma[i] = rma[i-1]*(length-1)/length + s[i]/length`` 점화식 적용.
    그 앞 인덱스는 NaN. 입력 NaN은 RMA를 그 시점에서 진전시키지 않는다.
    """
    s = s.astype(float)
    out = pd.Series(np.nan, index=s.index, dtype=float)
    if len(s) < length:
        return out

    init_val = s.iloc[0:length].mean()  # skipna=True (기본)
    if pd.isna(init_val):
        return out
    init_pos = length - 1
    out.iloc[init_pos] = init_val

    factor = (length - 1) / length
    inv = 1.0 / length
    prev = init_val
    arr = s.to_numpy()
    out_arr = out.to_numpy(copy=True)
    for i in range(init_pos + 1, len(s)):
        cur = arr[i]
        if np.isnan(cur):
            out_arr[i] = prev
        else:
            prev = prev * factor + cur * inv
            out_arr[i] = prev
    return pd.Series(out_arr, index=s.index, dtype=float)


def _true_range(
    high: pd.Series, low: pd.Series, close: pd.Series, prenan: bool = False,
) -> pd.Series:
    """True Range. ``DataFrame.max(axis=1)``이 NaN을 무시하므로, 기본 동작은
    첫 행에 ``high-low``가 들어간다 (pandas_ta prenan=False와 동일).

    ``prenan=True``면 첫 행을 NaN으로 강제한다 (pandas_ta가 ADX 내부 ATR 호출 시
    사용하는 모드).
    """
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    if prenan:
        tr.iloc[0] = np.nan
    return tr


def wilder_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14,
    prenan: bool = False,
) -> pd.Series:
    """Wilder ATR(length) — pandas_ta.atr(mamode='rma', prenan=...)와 동등."""
    tr = _true_range(
        high.astype(float), low.astype(float), close.astype(float), prenan=prenan,
    )
    return _wilder_rma(tr, length)


def _zero_small(s: pd.Series) -> pd.Series:
    """pandas_ta.utils.zero 동등 — |x| < eps면 0, NaN/그 외는 그대로."""
    eps = np.finfo(float).eps
    return s.where((s.abs() >= eps) | s.isna(), 0.0)


def wilder_adx(
    high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14,
) -> pd.DataFrame:
    """ADX(length) — pandas_ta.adx(mamode='rma', talib=False)와 동등.

    pandas_ta는 ATR에는 presma=True(진짜 Wilder)를 적용하지만, DM/DX smoothing에는
    단순 ``ewm(alpha=1/length, adjust=False)``를 사용한다. 그 동작을 그대로 따른다.

    Returns:
        DataFrame with ``ADX_{length}``, ``DMP_{length}``, ``DMN_{length}``.
    """
    high = high.astype(float)
    low = low.astype(float)
    close = close.astype(float)

    # pandas_ta.adx는 내부 ATR을 prenan=True로 호출 — 첫 행 TR을 NaN으로 강제
    atr = wilder_atr(high, low, close, length=length, prenan=True)
    k = 100.0 / atr

    up = high.diff()
    dn = -low.diff()
    pos = ((up > dn) & (up > 0)) * up
    neg = ((dn > up) & (dn > 0)) * dn
    # boolean × NaN = NaN — 첫 행 NaN 유지
    pos = pos.where(up.notna(), np.nan)
    neg = neg.where(dn.notna(), np.nan)
    pos = _zero_small(pos)
    neg = _zero_small(neg)

    alpha = 1.0 / length
    dmp = k * pos.ewm(alpha=alpha, adjust=False).mean()
    dmn = k * neg.ewm(alpha=alpha, adjust=False).mean()

    dx = 100.0 * (dmp - dmn).abs() / (dmp + dmn)
    adx = dx.ewm(alpha=alpha, adjust=False).mean()

    return pd.DataFrame({
        f"ADX_{length}": adx,
        f"DMP_{length}": dmp,
        f"DMN_{length}": dmn,
    })


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
    return wilder_atr(
        daily_df["high"], daily_df["low"], daily_df["close"], length=length,
    )


def calculate_atr_pct(atr: pd.Series, close: pd.Series) -> pd.Series:
    """ATR을 종가 대비 % 비율로 변환."""
    return (atr / close) * 100


def get_latest_atr(
    db_path: str,
    ticker: str,
    as_of_date: str | None = None,
) -> float | None:
    """ticker_atr 테이블에서 (as_of_date 이하) 가장 최신 ATR%를 조회.

    Args:
        db_path: SQLite DB 경로
        ticker:  종목 코드
        as_of_date: YYYYMMDD 또는 YYYY-MM-DD (None이면 전체 기간 중 최신)

    Returns:
        atr_pct 비율 (예: 0.034 == 3.4%). 조회 실패 시 None.
        DB는 % 단위(3.4)로 저장하므로 /100 후 반환한다.
    """
    try:
        conn = sqlite3.connect(db_path)
    except Exception:
        return None
    try:
        if as_of_date:
            # ticker_atr.dt는 YYYY-MM-DD 형식 (scripts/calculate_atr.py 집계 결과)
            # YYYYMMDD 입력도 지원: 8자리이면 하이픈 삽입
            if len(as_of_date) == 8 and as_of_date.isdigit():
                as_of_date = f"{as_of_date[:4]}-{as_of_date[4:6]}-{as_of_date[6:]}"
            row = conn.execute(
                "SELECT atr_pct FROM ticker_atr "
                "WHERE ticker=? AND dt<=? "
                "ORDER BY dt DESC LIMIT 1",
                (ticker, as_of_date),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT atr_pct FROM ticker_atr "
                "WHERE ticker=? ORDER BY dt DESC LIMIT 1",
                (ticker,),
            ).fetchone()
    finally:
        conn.close()

    if row and row[0] is not None:
        return float(row[0]) / 100.0
    return None


def calculate_atr_stop_loss(
    entry_price: float,
    atr_pct: float,
    multiplier: float = 1.5,
    min_pct: float = 0.015,
    max_pct: float = 0.08,
) -> float:
    """ATR 기반 손절가 계산.

    손절폭 = atr_pct × multiplier (min_pct / max_pct로 클램프).

    Args:
        entry_price: 진입가
        atr_pct:     ATR% 비율 (0.034 = 3.4%)
        multiplier:  ATR 배수
        min_pct:     최소 손절폭 (하한, 비율)
        max_pct:     최대 손절폭 (상한, 비율)

    Returns:
        손절가 (원, float).
    """
    stop_pct = atr_pct * multiplier
    stop_pct = max(min_pct, min(max_pct, stop_pct))
    return entry_price * (1 - stop_pct)


def calculate_atr_tp1(
    entry_price: float,
    atr_pct: float,
    multiplier: float = 3.0,
    min_pct: float = 0.03,
    max_pct: float = 0.25,
) -> float:
    """ATR 기반 TP1(1차 익절) 목표가 계산.

    TP1 상승폭 = atr_pct × multiplier (min_pct / max_pct 클램프).
    """
    tp_pct = atr_pct * multiplier
    tp_pct = max(min_pct, min(max_pct, tp_pct))
    return entry_price * (1 + tp_pct)


def calculate_atr_trailing_stop(
    peak_price: float,
    atr_pct: float,
    multiplier: float = 2.5,
    min_pct: float = 0.02,
    max_pct: float = 0.10,
) -> float:
    """Chandelier 트레일링 스톱 — peak - ATR × multiplier.

    Args:
        peak_price: 포지션 보유 중 최고가
        atr_pct:    ATR% 비율
        multiplier: ATR 배수
        min_pct:    트레일 폭 하한 (비율)
        max_pct:    트레일 폭 상한 (비율)

    Returns:
        트레일링 스톱 가격.
    """
    trail_pct = atr_pct * multiplier
    trail_pct = max(min_pct, min(max_pct, trail_pct))
    return peak_price * (1 - trail_pct)
