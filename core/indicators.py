"""core/indicators.py — 공통 기술 지표 (pandas_ta 기반).

일봉 또는 분봉 DataFrame으로 ATR, ATR% 등을 계산한다.
ticker_atr DB에서 최신 ATR%를 조회하고 ATR 기반 손절가를 산출하는 유틸도 제공한다.
"""

import sqlite3

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
