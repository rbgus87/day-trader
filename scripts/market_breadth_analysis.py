"""scripts/market_breadth_analysis.py — 시장 breadth 분석.

41종목 intraday_candles + index_candles 기반으로 일별 breadth 지표 계산:
  - advance_ratio: 전일 대비 상승 종목 비율
  - breakout_count: 전일 고가 돌파 종목 수
  - avg_return: 41종목 평균 등락률
  - index_return: KOSPI/KOSDAQ 지수 등락률
  - divergence: avg_return - index_return
"""

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import AppConfig
import yaml

OLD_START = "2025-04-01"
OLD_END   = "2026-04-10"
NEW_START = "2026-04-11"
NEW_END   = "2026-05-12"

REPORT_PATH = Path("reports/market_breadth_analysis.md")


# ---------------------------------------------------------------------------
# 데이터 로드
# ---------------------------------------------------------------------------

def load_daily_ohlcv(con: sqlite3.Connection, tickers: list[str]) -> pd.DataFrame:
    """1분봉에서 일별 OHLCV 집계.

    intraday_candles에서 ticker × date 기준으로 집계:
        open  = 해당 날짜 첫 1m 봉 open
        high  = 해당 날짜 1m 봉 high의 최대값
        low   = 해당 날짜 1m 봉 low의 최소값
        close = 해당 날짜 마지막 1m 봉 close
    """
    placeholders = ",".join("?" * len(tickers))
    query = f"""
    SELECT
        ticker,
        substr(ts, 1, 10) AS date,
        MAX(high)          AS high,
        MIN(low)           AS low,
        SUM(volume)        AS volume
    FROM intraday_candles
    WHERE tf='1m' AND ticker IN ({placeholders})
    GROUP BY ticker, substr(ts, 1, 10)
    """
    df_agg = pd.read_sql_query(query, con, params=tickers)

    # 첫 open, 마지막 close는 별도 쿼리 (GROUP BY로 직접 구하기 어려움)
    # open: 각 date별 MIN(ts)의 open
    query_open = f"""
    SELECT ic.ticker, substr(ic.ts, 1, 10) AS date, ic.open
    FROM intraday_candles ic
    INNER JOIN (
        SELECT ticker, substr(ts, 1, 10) AS date, MIN(ts) AS min_ts
        FROM intraday_candles
        WHERE tf='1m' AND ticker IN ({placeholders})
        GROUP BY ticker, substr(ts, 1, 10)
    ) first ON ic.ticker = first.ticker AND ic.ts = first.min_ts AND tf='1m'
    """
    df_open = pd.read_sql_query(query_open, con, params=tickers)
    df_open = df_open.rename(columns={"open": "day_open"})

    # close: 각 date별 MAX(ts)의 close
    query_close = f"""
    SELECT ic.ticker, substr(ic.ts, 1, 10) AS date, ic.close
    FROM intraday_candles ic
    INNER JOIN (
        SELECT ticker, substr(ts, 1, 10) AS date, MAX(ts) AS max_ts
        FROM intraday_candles
        WHERE tf='1m' AND ticker IN ({placeholders})
        GROUP BY ticker, substr(ts, 1, 10)
    ) last ON ic.ticker = last.ticker AND ic.ts = last.max_ts AND tf='1m'
    """
    df_close = pd.read_sql_query(query_close, con, params=tickers)
    df_close = df_close.rename(columns={"close": "day_close"})

    df = df_agg.merge(df_open[["ticker", "date", "day_open"]], on=["ticker", "date"])
    df = df.merge(df_close[["ticker", "date", "day_close"]], on=["ticker", "date"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)
    return df


def load_index_candles(con: sqlite3.Connection) -> pd.DataFrame:
    """index_candles에서 KOSPI/KOSDAQ 일봉 로드."""
    df = pd.read_sql_query(
        "SELECT index_code, dt, open, close FROM index_candles ORDER BY dt",
        con
    )
    df["dt"] = pd.to_datetime(df["dt"])
    return df


# ---------------------------------------------------------------------------
# breadth 계산
# ---------------------------------------------------------------------------

def compute_breadth(daily: pd.DataFrame, index_df: pd.DataFrame,
                    ticker_to_market: dict) -> pd.DataFrame:
    """일별 breadth 지표 계산."""
    tickers = daily["ticker"].unique()
    dates = sorted(daily["date"].unique())

    # 각 종목의 종가 pivot
    close_pivot = daily.pivot(index="date", columns="ticker", values="day_close")
    high_pivot  = daily.pivot(index="date", columns="ticker", values="high")

    # 지수 데이터 인덱싱
    kospi  = index_df[index_df["index_code"] == "001"].set_index("dt")["close"]
    kosdaq = index_df[index_df["index_code"] == "101"].set_index("dt")["close"]

    rows = []
    for i, date in enumerate(dates):
        if i == 0:
            continue  # 전일 없으면 스킵

        prev_date = dates[i - 1]

        # 전일 종가 / 고가 존재하는 종목만
        curr_close = close_pivot.loc[date] if date in close_pivot.index else pd.Series(dtype=float)
        prev_close = close_pivot.loc[prev_date] if prev_date in close_pivot.index else pd.Series(dtype=float)
        curr_high  = high_pivot.loc[date] if date in high_pivot.index else pd.Series(dtype=float)
        prev_high  = high_pivot.loc[prev_date] if prev_date in high_pivot.index else pd.Series(dtype=float)

        common = curr_close.index.intersection(prev_close.index)
        if len(common) < 10:
            continue

        curr_c = curr_close[common].dropna()
        prev_c = prev_close[common].dropna()
        valid  = curr_c.index.intersection(prev_c.index)

        if len(valid) < 10:
            continue

        returns = (curr_c[valid] / prev_c[valid] - 1)
        advance_ratio = (returns > 0).sum() / len(valid)
        avg_return = returns.mean()

        # 전일 고가 돌파 종목 수
        common_h = curr_high.index.intersection(prev_high.index)
        breakout_n = 0
        if len(common_h) > 0:
            c_h = curr_high[common_h].dropna()
            p_h = prev_high[common_h].dropna()
            v_h = c_h.index.intersection(p_h.index)
            if len(v_h) > 0:
                breakout_n = (c_h[v_h] > p_h[v_h]).sum()

        # 지수 등락률 (KOSPI 기준: 유니버스가 혼재하면 단순 KOSPI 사용)
        kospi_ret = float("nan")
        if date in kospi.index and prev_date in kospi.index and kospi[prev_date] > 0:
            kospi_ret = kospi[date] / kospi[prev_date] - 1

        kosdaq_ret = float("nan")
        if date in kosdaq.index and prev_date in kosdaq.index and kosdaq[prev_date] > 0:
            kosdaq_ret = kosdaq[date] / kosdaq[prev_date] - 1

        # 지수 대비 괴리 (종목 평균 - 지수)
        index_ret = kospi_ret if not np.isnan(kospi_ret) else kosdaq_ret
        divergence = avg_return - index_ret if not np.isnan(index_ret) else float("nan")

        rows.append({
            "date": date,
            "n_stocks": len(valid),
            "advance_ratio": advance_ratio,
            "breakout_count": int(breakout_n),
            "avg_return": avg_return,
            "kospi_ret": kospi_ret,
            "kosdaq_ret": kosdaq_ret,
            "divergence": divergence,
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 진입 성공/실패 매핑
# ---------------------------------------------------------------------------

def load_trade_dates(db_path: str) -> dict:
    """backtester 거래 기록 대신, Scenario A의 결과에서 날짜별 PnL 매핑.

    DB trades 테이블에서 exit_date × pnl 로드.
    """
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("SELECT date(exit_ts), SUM(pnl), COUNT(*) FROM trades GROUP BY date(exit_ts)")
    rows = cur.fetchall()
    con.close()
    return {row[0]: {"pnl": row[1] or 0, "n": row[2] or 0} for row in rows if row[0]}


# ---------------------------------------------------------------------------
# 리포트 생성
# ---------------------------------------------------------------------------

def classify_period(date: pd.Timestamp) -> str:
    if date < pd.Timestamp(NEW_START):
        return "old"
    elif date <= pd.Timestamp(NEW_END):
        return "new"
    return "other"


def write_report(breadth: pd.DataFrame):
    REPORT_PATH.parent.mkdir(exist_ok=True)

    old = breadth[breadth["date"] < pd.Timestamp(NEW_START)].copy()
    new = breadth[(breadth["date"] >= pd.Timestamp(NEW_START)) &
                  (breadth["date"] <= pd.Timestamp(NEW_END))].copy()

    def stats(df: pd.DataFrame, col: str) -> str:
        if df.empty or col not in df.columns:
            return "N/A"
        s = df[col].dropna()
        return f"평균={s.mean():.3f}, 중앙={s.median():.3f}, std={s.std():.3f}, min={s.min():.3f}, max={s.max():.3f}"

    lines = [
        "# 시장 Breadth 분석",
        "",
        f"> 기간: {OLD_START}~{OLD_END} (기존) / {NEW_START}~{NEW_END} (확장)",
        f"> 유니버스: 백테스트 41종목",
        "",
        "## 지표 정의",
        "",
        "| 지표 | 정의 |",
        "|------|------|",
        "| advance_ratio | 전일 종가 대비 상승 종목 비율 (0~1) |",
        "| breakout_count | 전일 고가 돌파 종목 수 |",
        "| avg_return | 41종목 단순 평균 등락률 |",
        "| kospi_ret | KOSPI 지수 등락률 |",
        "| divergence | avg_return - kospi_ret (음수 = 종목군이 지수보다 부진) |",
        "",
        "---",
        "",
        "## 1. 기존 구간 vs 확장 구간 통계",
        "",
        f"### 기존 구간 ({OLD_START} ~ {OLD_END}, {len(old)}일)",
        "",
        f"- advance_ratio: {stats(old, 'advance_ratio')}",
        f"- breakout_count: {stats(old, 'breakout_count')}",
        f"- avg_return: {stats(old, 'avg_return')}",
        f"- kospi_ret: {stats(old, 'kospi_ret')}",
        f"- divergence: {stats(old, 'divergence')}",
        "",
        f"### 확장 구간 ({NEW_START} ~ {NEW_END}, {len(new)}일)",
        "",
        f"- advance_ratio: {stats(new, 'advance_ratio')}",
        f"- breakout_count: {stats(new, 'breakout_count')}",
        f"- avg_return: {stats(new, 'avg_return')}",
        f"- kospi_ret: {stats(new, 'kospi_ret')}",
        f"- divergence: {stats(new, 'divergence')}",
        "",
        "---",
        "",
        "## 2. 확장 구간 일별 상세",
        "",
        "| 날짜 | advance_ratio | breakout_count | avg_return | kospi_ret | divergence |",
        "|------|--------------|---------------|-----------|-----------|-----------|",
    ]

    for _, row in new.sort_values("date").iterrows():
        div_str = f"{row['divergence']:.3f}" if not np.isnan(row['divergence']) else "N/A"
        lines.append(
            f"| {row['date'].strftime('%Y-%m-%d')} "
            f"| {row['advance_ratio']:.2f} "
            f"| {int(row['breakout_count']):>3} "
            f"| {row['avg_return']:+.3f} "
            f"| {row['kospi_ret']:+.3f} "
            f"| {div_str} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 3. 분위별 분석 (기존 구간 기준)",
        "",
    ]

    # advance_ratio 하위 30% 임계값 계산 (기존 구간 기준)
    for col, label in [
        ("advance_ratio", "advance_ratio"),
        ("divergence", "divergence (avg - KOSPI)"),
        ("breakout_count", "breakout_count"),
    ]:
        if col not in old.columns:
            continue
        q30 = old[col].quantile(0.30)
        q70 = old[col].quantile(0.70)
        lines += [
            f"### {label}",
            f"- 기존 구간 30% 분위: {q30:.3f}",
            f"- 기존 구간 70% 분위: {q70:.3f}",
            f"- 확장 구간 평균: {new[col].mean():.3f} (기존 30%분위={'이하' if new[col].mean() <= q30 else '초과'})",
            "",
        ]

    # 임계값 탐색 — advance_ratio < threshold인 날 vs 아닌 날의 특성
    lines += [
        "---",
        "",
        "## 4. 필터 임계값 탐색",
        "",
        "기존 구간에서 advance_ratio, divergence 임계값으로 날짜를 분류:",
        "",
        "| 임계값 | 차단 일수(기존) | 차단 일수(확장) | 확장 커버리지 |",
        "|--------|---------------|---------------|-------------|",
    ]

    total_old = len(old)
    total_new = len(new)

    for thresh_ar in [0.30, 0.35, 0.40, 0.45]:
        block_old = (old["advance_ratio"] < thresh_ar).sum()
        block_new = (new["advance_ratio"] < thresh_ar).sum()
        coverage = block_new / total_new if total_new > 0 else 0
        lines.append(
            f"| advance_ratio < {thresh_ar:.2f} | {block_old}/{total_old} ({block_old/total_old*100:.0f}%) "
            f"| {block_new}/{total_new} ({block_new/total_new*100:.0f}%) "
            f"| {coverage:.0%} |"
        )

    lines.append("")
    for thresh_div in [-0.010, -0.015, -0.020, -0.025]:
        block_old = (old["divergence"] < thresh_div).sum()
        block_new = (new["divergence"] < thresh_div).sum()
        coverage = block_new / total_new if total_new > 0 else 0
        lines.append(
            f"| divergence < {thresh_div:.3f} | {block_old}/{total_old} ({block_old/total_old*100:.0f}%) "
            f"| {block_new}/{total_new} ({block_new/total_new*100:.0f}%) "
            f"| {coverage:.0%} |"
        )

    lines += [
        "",
        "---",
        "",
        "## 5. 결론 및 필터 설계안",
        "",
        "*(분석 결과 기반으로 작성)*",
        "",
    ]

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"[REPORT] {REPORT_PATH}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print(" 시장 Breadth 분석")
    print("=" * 60)

    cfg = AppConfig.from_yaml()
    uni = yaml.safe_load(open("config/universe_backtest.yaml", encoding="utf-8"))
    stocks = uni.get("stocks", [])
    tickers = [s["ticker"] for s in stocks]
    ticker_to_market = {s["ticker"]: s.get("market", "unknown") for s in stocks}

    con = sqlite3.connect(cfg.db_path)

    print("[1] 일별 OHLCV 집계 중...")
    daily = load_daily_ohlcv(con, tickers)
    print(f"    종목수: {daily['ticker'].nunique()}, 기간: {daily['date'].min().date()} ~ {daily['date'].max().date()}")

    print("[2] 지수 데이터 로드 중...")
    index_df = load_index_candles(con)
    con.close()

    print("[3] Breadth 지표 계산 중...")
    breadth = compute_breadth(daily, index_df, ticker_to_market)
    print(f"    일수: {len(breadth)}")

    # 콘솔 요약
    old = breadth[breadth["date"] < pd.Timestamp(NEW_START)]
    new = breadth[(breadth["date"] >= pd.Timestamp(NEW_START)) &
                  (breadth["date"] <= pd.Timestamp(NEW_END))]

    print()
    print(f"{'지표':<20} {'기존 평균':>12} {'확장 평균':>12}  {'차이':>8}")
    print("-" * 56)
    for col, label in [
        ("advance_ratio",  "advance_ratio"),
        ("breakout_count", "breakout_count"),
        ("avg_return",     "avg_return"),
        ("kospi_ret",      "kospi_ret"),
        ("divergence",     "divergence"),
    ]:
        o_m = old[col].mean()
        n_m = new[col].mean()
        diff = n_m - o_m
        print(f"{label:<20} {o_m:>12.4f} {n_m:>12.4f}  {diff:>+8.4f}")

    print()
    print("[4] 리포트 작성 중...")
    write_report(breadth)
    print("완료.")


if __name__ == "__main__":
    main()
