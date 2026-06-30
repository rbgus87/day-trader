"""scripts/verify_flow_volume.py — flow_volume DB 주입 경로 검증.

실행: python scripts/verify_flow_volume.py
DB에 전일 분봉이 있는 종목으로 _check_flow_volume() 호출 시 None이 아닌 float 반환 여부 확인.
"""
import asyncio
import sqlite3
from datetime import date, time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from config.settings import AppConfig
from strategy.momentum_strategy import MomentumStrategy


async def main():
    cfg = AppConfig.from_yaml()
    db_path = cfg.db_path
    today_str = date.today().isoformat()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # 전일 분봉이 있는 종목 1개 찾기
    row = conn.execute(
        "SELECT ticker, MAX(date(ts)) as prev_dt FROM intraday_candles "
        "WHERE tf='1m' AND date(ts) < ? "
        "GROUP BY ticker LIMIT 1",
        (today_str,),
    ).fetchone()

    if not row:
        print("DB에 전일 분봉 없음 — 15:35 수집 후 재시도")
        conn.close()
        return

    ticker = row["ticker"]
    prev_dt = row["prev_dt"]
    print(f"테스트 종목: {ticker}, 전일: {prev_dt}")

    rows = conn.execute(
        "SELECT ts, open, high, low, close, volume FROM intraday_candles "
        "WHERE ticker=? AND tf='1m' AND date(ts)=? ORDER BY ts ASC",
        (ticker, prev_dt),
    ).fetchall()
    conn.close()

    if not rows:
        print("전일 분봉 조회 실패")
        return

    df = pd.DataFrame([dict(r) for r in rows])
    df["ts"] = pd.to_datetime(df["ts"])
    print(f"  전일 분봉 수: {len(df)}개  ({df['ts'].iloc[0]} ~ {df['ts'].iloc[-1]})")

    # 전략 객체에 주입
    trading_cfg = cfg.trading
    # flow_ratio 활성화
    object.__setattr__(trading_cfg, "flow_ratio", 2.0)
    object.__setattr__(trading_cfg, "flow_window_min", 5)

    strat = MomentumStrategy(trading_cfg)
    strat.set_ticker(ticker)
    strat.set_prev_day_data(high=10000.0, volume=500000, close=9500.0)
    strat.set_prev_day_candles(df)

    assert strat._prev_day_candles is not None, "set_prev_day_candles 주입 실패"

    # 당일 5분 분봉 생성 (9:10 기준 — 전일 09:05~09:10 비교)
    cur_rows = []
    for i in range(10):
        cur_rows.append({
            "ticker": ticker, "tf": "1m",
            "ts": f"2026-06-30T09:{i:02d}:00",
            "open": 10200, "high": 10300, "low": 10150,
            "close": 10250, "volume": 5000,
        })
    candles = pd.DataFrame(cur_rows)

    result = strat._check_flow_volume(candles, time(9, 10), ticker)
    if result is None:
        print("  결과: None (전일 09:05~09:10 분봉 없음 또는 설정 오류)")
    else:
        ok, flow_vol, baseline_vol, ratio = result
        print(f"  결과: ok={ok}, flow_vol={flow_vol:,}, baseline_vol={baseline_vol:,}, ratio={ratio:.2f}x")
        print("  [OK] flow_volume DB 주입 경로 정상 작동")


if __name__ == "__main__":
    asyncio.run(main())
