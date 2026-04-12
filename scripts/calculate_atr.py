"""scripts/calculate_atr.py — universe 전종목 일별 ATR 계산 → DB 저장.

intraday_candles(1분봉)에서 일봉을 집계해 ATR(14) 계산 → ticker_atr 저장.
백테스트와 실시간 엔진 양쪽에서 미리 계산된 ATR을 사용할 수 있다.

사용:
    python scripts/calculate_atr.py
"""

import asyncio
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import AppConfig
from core.indicators import calculate_atr, calculate_atr_pct
from data.db_manager import DbManager


def aggregate_daily_from_minute(db_path: str, ticker: str) -> pd.DataFrame:
    """intraday_candles(tf='1m')에서 일봉 집계.

    Returns:
        DataFrame columns = [dt, high, low, close, volume]
        dt는 YYYY-MM-DD 형식 문자열 (substr(ts,1,10)).
    """
    conn = sqlite3.connect(db_path)
    try:
        # high/low/volume는 직접 집계, close는 각 날짜의 마지막 1분봉
        rows = conn.execute(
            """
            WITH daily AS (
                SELECT
                    substr(ts, 1, 10) AS dt,
                    MIN(low)  AS low,
                    MAX(high) AS high,
                    SUM(volume) AS volume,
                    MAX(ts) AS last_ts
                FROM intraday_candles
                WHERE ticker = ? AND tf = '1m'
                GROUP BY substr(ts, 1, 10)
            )
            SELECT d.dt, d.high, d.low, c.close, d.volume
            FROM daily d
            JOIN intraday_candles c
              ON c.ticker = ? AND c.tf = '1m' AND c.ts = d.last_ts
            ORDER BY d.dt
            """,
            (ticker, ticker),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return pd.DataFrame(columns=["dt", "high", "low", "close", "volume"])

    return pd.DataFrame(rows, columns=["dt", "high", "low", "close", "volume"])


async def main() -> int:
    cfg = AppConfig.from_yaml()
    db_path = cfg.db_path

    # SCHEMA_SQL이 ticker_atr 테이블을 생성하도록 init 호출
    db = DbManager(db_path)
    await db.init()
    await db.close()

    uni = yaml.safe_load(open("config/universe.yaml", encoding="utf-8"))
    stocks = uni.get("stocks", [])
    print(f"universe: {len(stocks)}종목\n")

    conn = sqlite3.connect(db_path)
    try:
        inserted_total = 0
        skipped = 0

        for idx, stock in enumerate(stocks, 1):
            ticker = stock["ticker"]
            name = stock.get("name", "")

            daily_df = aggregate_daily_from_minute(db_path, ticker)
            if len(daily_df) < 15:
                print(f"  [{idx:>2}/{len(stocks)}] {ticker} {name}: 데이터 부족 ({len(daily_df)}일)")
                skipped += 1
                continue

            atr = calculate_atr(daily_df, length=14)
            atr_pct = calculate_atr_pct(atr, daily_df["close"])

            rows = []
            for i in range(len(atr)):
                if pd.notna(atr.iloc[i]):
                    rows.append(
                        (
                            ticker,
                            daily_df["dt"].iloc[i],
                            float(atr.iloc[i]),
                            float(atr_pct.iloc[i]),
                            float(daily_df["close"].iloc[i]),
                        )
                    )

            if rows:
                conn.executemany(
                    "INSERT OR REPLACE INTO ticker_atr "
                    "(ticker, dt, atr, atr_pct, close) VALUES (?, ?, ?, ?, ?)",
                    rows,
                )
                conn.commit()
                inserted_total += len(rows)

            latest_atr_pct = atr_pct.dropna().iloc[-1] if len(atr_pct.dropna()) > 0 else 0.0
            print(
                f"  [{idx:>2}/{len(stocks)}] {ticker} {name}: "
                f"ATR% = {latest_atr_pct:.2f}% ({len(rows)}일)"
            )

        print(f"\n[OK] 총 {inserted_total}건 삽입 (스킵 {skipped}종목)")

        # 전체 통계
        total, tickers, min_pct, max_pct, avg_pct = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT ticker), "
            "MIN(atr_pct), MAX(atr_pct), AVG(atr_pct) FROM ticker_atr"
        ).fetchone()
        print(f"\nATR 분포: 총 {total}건 ({tickers}종목)")
        print(f"  ATR%: 최소 {min_pct:.2f}% / 최대 {max_pct:.2f}% / 평균 {avg_pct:.2f}%")

        # 변동성 랭킹
        ticker_atrs = conn.execute(
            "SELECT ticker, AVG(atr_pct) AS a FROM ticker_atr "
            "GROUP BY ticker ORDER BY a"
        ).fetchall()

        print("\n변동성 낮은 5종목:")
        for t, p in ticker_atrs[:5]:
            print(f"  {t}  {p:>5.2f}%")
        print("\n변동성 높은 5종목:")
        for t, p in ticker_atrs[-5:]:
            print(f"  {t}  {p:>5.2f}%")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
