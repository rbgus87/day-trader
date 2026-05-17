"""backtest/universe_simulator.py — 동적 유니버스 시뮬레이터.

날짜별로 스크리너가 선정했을 종목을 일봉 데이터로 재현한다.
ticker_daily_ohlcv 테이블(collect_broad_daily.py로 수집)에서 읽는다.

필터 조건 (screener/candidate_collector.py 와 동일):
    1. 전일 거래대금(volume × close) 상위
    2. ATR(14) >= min_atr_pct (기본 4%)
    3. 전일 종가 >= 전일 고가 × 0.97  (돌파 임박)
    4. 전일 상한가 제외 (close >= open × 1.29)
    5. 전일 거래대금 < min_turnover 제외 (30억)
    6. 상위 top_n 종목 선정 (기본 80종목)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd
from loguru import logger

_DB_PATH = Path(__file__).parent.parent / "daytrader.db"

TICKER_DAILY_DDL = """
CREATE TABLE IF NOT EXISTS ticker_daily_ohlcv (
    ticker    TEXT NOT NULL,
    dt        TEXT NOT NULL,
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL,
    volume    INTEGER,
    turnover  INTEGER,
    PRIMARY KEY (ticker, dt)
);
CREATE INDEX IF NOT EXISTS idx_tdoly_dt ON ticker_daily_ohlcv(dt);
CREATE INDEX IF NOT EXISTS idx_tdoly_ticker ON ticker_daily_ohlcv(ticker);
"""


def ensure_daily_table(db_path: str) -> None:
    """ticker_daily_ohlcv 테이블이 없으면 생성한다."""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(TICKER_DAILY_DDL)
        conn.commit()
    finally:
        conn.close()


def _calc_atr_pct(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray,
                  length: int = 14) -> float | None:
    """일봉 배열에서 ATR% 계산 (마지막 length일 평균 TR / 마지막 close)."""
    n = len(closes)
    if n < length + 1:
        return None
    h = highs[-length - 1:]
    l = lows[-length - 1:]
    c = closes[-length - 1:]
    tr = np.maximum(
        h[1:] - l[1:],
        np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])),
    )
    if len(tr) < length:
        return None
    atr = float(np.mean(tr[-length:]))
    last_close = float(c[-1])
    return atr / last_close if last_close > 0 else None


class UniverseSimulator:
    """날짜별 동적 유니버스를 시뮬레이션한다."""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or str(_DB_PATH)

    def _load_all_daily(self, tickers: Sequence[str]) -> dict[str, pd.DataFrame]:
        """ticker_daily_ohlcv에서 지정 종목 전체 일봉 로드."""
        if not tickers:
            return {}
        placeholders = ",".join("?" * len(tickers))
        conn = sqlite3.connect(self._db_path)
        try:
            cur = conn.execute(
                f"SELECT ticker, dt, open, high, low, close, volume, turnover "
                f"FROM ticker_daily_ohlcv WHERE ticker IN ({placeholders}) "
                f"ORDER BY ticker, dt ASC",
                list(tickers),
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        if not rows:
            return {}

        df_all = pd.DataFrame(
            rows, columns=["ticker", "dt", "open", "high", "low", "close", "volume", "turnover"],
        )
        result: dict[str, pd.DataFrame] = {}
        for ticker, grp in df_all.groupby("ticker", sort=False):
            result[str(ticker)] = grp.reset_index(drop=True)
        return result

    def _business_dates(self, start_dt: str, end_dt: str) -> list[str]:
        """index_candles(KOSPI)에서 영업일 목록 조회."""
        conn = sqlite3.connect(self._db_path)
        try:
            cur = conn.execute(
                "SELECT DISTINCT dt FROM index_candles "
                "WHERE index_code='001' AND dt >= ? AND dt <= ? ORDER BY dt ASC",
                (start_dt, end_dt),
            )
            return [r[0] for r in cur.fetchall()]
        finally:
            conn.close()

    def _screen_single(
        self,
        date: str,          # YYYYMMDD
        broad_pool: Sequence[str],
        daily_data: dict[str, pd.DataFrame],
        top_n: int,
        min_atr_pct: float,
        min_turnover: int,
    ) -> list[str]:
        candidates: list[dict] = []

        for ticker in broad_pool:
            df = daily_data.get(ticker)
            if df is None or df.empty:
                continue

            # 전일 = date 이전 최신 행
            prev_df = df[df["dt"] < date]
            if len(prev_df) < 15:   # ATR 계산 최소
                continue

            prev = prev_df.iloc[-1]
            prev_close = float(prev["close"])
            prev_high = float(prev["high"])
            prev_open = float(prev.get("open", 0) or 0)
            prev_turnover = int(prev.get("turnover", 0) or 0)

            # 필터 1: 거래대금 >= 30억
            if prev_turnover < min_turnover:
                continue

            # 필터 2: 돌파 임박 (전일 종가 >= 전일 고가 × 0.97)
            if prev_high > 0 and prev_close < prev_high * 0.97:
                continue

            # 필터 3: 전일 상한가 제외
            if prev_open > 0 and prev_close >= prev_open * 1.29:
                continue

            # 필터 4: ATR(14) >= 4%
            closes = prev_df["close"].values.astype(np.float64)
            highs  = prev_df["high"].values.astype(np.float64)
            lows   = prev_df["low"].values.astype(np.float64)
            atr_pct = _calc_atr_pct(closes, highs, lows)
            if atr_pct is None or atr_pct < min_atr_pct:
                continue

            candidates.append({"ticker": ticker, "turnover": prev_turnover})

        candidates.sort(key=lambda x: x["turnover"], reverse=True)
        return [c["ticker"] for c in candidates[:top_n]]

    def simulate_daily_universe(
        self,
        date: str,
        broad_pool: Sequence[str],
        top_n: int = 80,
        min_atr_pct: float = 0.04,
        min_turnover: int = 3_000_000_000,
    ) -> list[str]:
        """주어진 날짜에 스크리너가 선정했을 종목 반환.

        Args:
            date: YYYYMMDD 또는 YYYY-MM-DD
            broad_pool: 전체 후보 종목 리스트

        Returns:
            선정 종목 리스트 (거래대금 내림차순)
        """
        date = date.replace("-", "")
        daily_data = self._load_all_daily(broad_pool)
        return self._screen_single(date, broad_pool, daily_data, top_n, min_atr_pct, min_turnover)

    def simulate_period(
        self,
        start_date: str,
        end_date: str,
        broad_pool: Sequence[str],
        top_n: int = 80,
        min_atr_pct: float = 0.04,
        min_turnover: int = 3_000_000_000,
    ) -> dict[str, list[str]]:
        """기간 내 모든 영업일의 유니버스 반환.

        Args:
            start_date: YYYYMMDD 또는 YYYY-MM-DD (포함)
            end_date:   YYYYMMDD 또는 YYYY-MM-DD (포함)
            broad_pool: 전체 후보 종목

        Returns:
            {"20250401": ["005930", ...], ...}
        """
        start_dt = start_date.replace("-", "")
        end_dt   = end_date.replace("-", "")

        dates = self._business_dates(start_dt, end_dt)
        if not dates:
            logger.warning(f"영업일 없음: {start_dt}~{end_dt} (index_candles 확인)")
            return {}

        logger.info(f"일봉 데이터 로드 중 ({len(broad_pool)}종목)...")
        daily_data = self._load_all_daily(broad_pool)
        logger.info(f"로드 완료: {len(daily_data)}종목")

        result: dict[str, list[str]] = {}
        for idx, date in enumerate(dates, 1):
            universe = self._screen_single(date, broad_pool, daily_data, top_n, min_atr_pct, min_turnover)
            result[date] = universe
            if idx % 20 == 0 or idx == len(dates):
                avg = sum(len(v) for v in result.values()) / len(result)
                logger.info(f"유니버스 시뮬 {idx}/{len(dates)} — 일평균 {avg:.1f}종목")

        return result
