"""scripts/analyze_bear_amount.py — 약세장 거래대금 분포 추정.

DB의 89 종목(universe 41 + 조건검색 추가) intraday_candles로 종목별 일별
거래대금을 계산하고, KOSPI 일봉 등락 기준으로 시장 국면별 분포 비교.

목적: "약세장에서 80위 거래대금이 200억 이상 유지되는가?"의 대리 답.
80개 정확값은 DB에 없으나, 활동성 상위 89종목의 일별 분포로 비율 추정.
"""

import sqlite3
import statistics
from collections import defaultdict


def main(db_path: str = "daytrader.db") -> None:
    conn = sqlite3.connect(db_path)

    # 1) KOSPI 일봉: 일별 close → 등락률 (전일 대비) → 시장 국면 분류
    rows = conn.execute(
        "SELECT dt, close FROM index_candles "
        "WHERE index_code='001' ORDER BY dt"
    ).fetchall()
    # YYYYMMDD → YYYY-MM-DD
    kospi: list[tuple[str, float]] = []
    for dt, close in rows:
        d = f"{dt[:4]}-{dt[4:6]}-{dt[6:]}" if len(dt) == 8 else dt
        kospi.append((d, float(close)))

    # 일별 등락률 + 5일 MA 비교로 국면 분류
    daily_ret: dict[str, float] = {}
    for i in range(1, len(kospi)):
        prev = kospi[i-1][1]
        cur = kospi[i][1]
        if prev > 0:
            daily_ret[kospi[i][0]] = (cur - prev) / prev * 100  # %

    # 2) 종목별 일별 거래대금 (intraday_candles, sum(close*volume))
    print("[1] 일별 거래대금 집계 중 (89종목 × 259일)...")
    daily_amount: dict[str, dict[str, float]] = defaultdict(dict)  # {date: {ticker: amount}}
    cur = conn.execute(
        "SELECT ticker, date(ts) AS d, SUM(close * volume) AS amt "
        "FROM intraday_candles WHERE tf='1m' "
        "GROUP BY ticker, date(ts)"
    )
    for ticker, d, amt in cur:
        if amt and amt > 0:
            daily_amount[d][ticker] = float(amt)
    conn.close()

    # 3) 분석 윈도우: 데이터가 있는 날짜만 (2025-04-01~2026-05-06)
    target_dates = sorted(daily_amount.keys())
    print(f"   대상 거래일: {len(target_dates)}일 ({target_dates[0]} ~ {target_dates[-1]})")

    # 4) 시장 국면 분류 (전일 등락률 기준)
    BEAR = -1.0   # 일별 -1% 이하
    BULL = +1.0   # 일별 +1% 이상
    bucket: dict[str, list[str]] = {"강세(+1%↑)": [], "약세(-1%↓)": [], "횡보": []}
    no_kospi = 0
    for d in target_dates:
        r = daily_ret.get(d)
        if r is None:
            no_kospi += 1
            continue
        if r >= BULL:
            bucket["강세(+1%↑)"].append(d)
        elif r <= BEAR:
            bucket["약세(-1%↓)"].append(d)
        else:
            bucket["횡보"].append(d)
    print(f"   KOSPI 등락률 매칭 누락: {no_kospi}일")
    print("   국면별 일수:")
    for k, v in bucket.items():
        print(f"     {k}: {len(v)}일")

    # 5) 국면별 일별 분포: top N (있는 종목 중 거래대금 순위)
    print("\n[2] 국면별 일별 거래대금 분포 (단위: 억원)")
    print(f"   {'국면':<14} {'평균종목수':>8} {'top1':>10} {'top5':>10} {'top10':>10} {'top30':>10} {'min':>10} {'중앙값':>10}")
    summary = {}
    for label, dates in bucket.items():
        per_day_stats = []
        for d in dates:
            tickers = daily_amount.get(d, {})
            if not tickers:
                continue
            sorted_amts = sorted(tickers.values(), reverse=True)
            n = len(sorted_amts)
            stats = {
                "n": n,
                "top1": sorted_amts[0],
                "top5": sorted_amts[4] if n >= 5 else sorted_amts[-1],
                "top10": sorted_amts[9] if n >= 10 else sorted_amts[-1],
                "top30": sorted_amts[29] if n >= 30 else sorted_amts[-1],
                "min": sorted_amts[-1],
                "median": statistics.median(sorted_amts),
            }
            per_day_stats.append(stats)
        if not per_day_stats:
            continue
        avg = lambda key: sum(s[key] for s in per_day_stats) / len(per_day_stats)
        s = {
            "n_avg": avg("n"),
            "top1": avg("top1"),
            "top5": avg("top5"),
            "top10": avg("top10"),
            "top30": avg("top30"),
            "min": avg("min"),
            "median": avg("median"),
        }
        summary[label] = s
        eok = lambda x: x / 1e8
        print(
            f"   {label:<14} {s['n_avg']:>8.1f} "
            f"{eok(s['top1']):>10,.0f} {eok(s['top5']):>10,.0f} "
            f"{eok(s['top10']):>10,.0f} {eok(s['top30']):>10,.0f} "
            f"{eok(s['min']):>10,.0f} {eok(s['median']):>10,.0f}"
        )

    # 6) 약세/강세 비율 (top 30위 기준 — 80위에 가장 가까운 우리 데이터 한계)
    if "약세(-1%↓)" in summary and "강세(+1%↑)" in summary:
        bear = summary["약세(-1%↓)"]
        bull = summary["강세(+1%↑)"]
        print("\n[3] 약세/강세 비율 (강세=1.0 기준)")
        for k in ["top1", "top5", "top10", "top30", "min", "median"]:
            ratio = bear[k] / bull[k] if bull[k] > 0 else 0
            print(f"   {k:>10}: {ratio:.3f}")

    # 7) 가장 강한 약세일 5건의 분포 직접 확인
    bear_dates_sorted = sorted(
        bucket.get("약세(-1%↓)", []),
        key=lambda d: daily_ret.get(d, 0)
    )[:8]
    print("\n[4] 가장 강한 약세일 8건 — 89종목 중 분포")
    print(f"   {'date':<12} {'KOSPI%':>7} {'n':>4} {'top1':>9} {'top5':>9} {'top10':>9} {'top30':>9} {'min':>9}")
    for d in bear_dates_sorted:
        tickers = daily_amount.get(d, {})
        if not tickers:
            continue
        sorted_amts = sorted(tickers.values(), reverse=True)
        n = len(sorted_amts)
        eok = lambda x: x / 1e8
        get = lambda i: sorted_amts[i] if n > i else sorted_amts[-1]
        print(
            f"   {d:<12} {daily_ret[d]:>+6.2f}% {n:>4} "
            f"{eok(get(0)):>9,.0f} {eok(get(4)):>9,.0f} "
            f"{eok(get(9)):>9,.0f} {eok(get(29)):>9,.0f} "
            f"{eok(sorted_amts[-1]):>9,.0f}"
        )

    # 8) 5/7 80위(626억) 기준 약세장 추정
    print("\n[5] 5/7 80위(626억) 기준 약세장 추정")
    if "약세(-1%↓)" in summary and "강세(+1%↑)" in summary:
        # min 기준 비율 (89종목 중 최하위 = 89위)
        ratio_min = summary["약세(-1%↓)"]["min"] / summary["강세(+1%↑)"]["min"]
        ratio_top30 = summary["약세(-1%↓)"]["top30"] / summary["강세(+1%↑)"]["top30"]
        print(f"   89종목 최하위 거래대금 약세/강세 비율: {ratio_min:.3f}")
        print(f"   89종목 30위 거래대금 약세/강세 비율:   {ratio_top30:.3f}")
        # 80위 추정
        print(f"   가정: 80위도 동일 비율 변동 → 80위 약세 추정")
        print(f"     min 비율 적용:    626억 × {ratio_min:.3f} = {626*ratio_min:.0f}억")
        print(f"     top30 비율 적용:  626억 × {ratio_top30:.3f} = {626*ratio_top30:.0f}억")


if __name__ == "__main__":
    main()
