"""scripts/analyze_watchlist_amount.py — 오늘 watchlist 80종목 거래대금 분포.

조건검색 결과의 enrichment 단계에서 amount=prev_close*prev_volume으로 정렬해
top 80을 watchlist로 선정. 80위 거래대금이 N억 이상이면 HTS 조건식의 거래대금
임계값을 N억으로 상향해도 80개 watchlist에 영향 없음.

소스:
  1) intraday_candles에 5/6 분봉이 있는 종목: SUM(close*volume)으로 정확한 거래대금
  2) 신규 추가 종목(분봉 미수집): 로그의 prev_high*prev_vol로 상한 추정 (~3% 과대)
"""

import re
import sqlite3
import statistics
from pathlib import Path


def main(target_date: str = "2026-05-07", prev_date: str = "2026-05-06") -> None:
    log_path = Path("logs/day.log")
    log = log_path.read_text(encoding="utf-8", errors="ignore")
    lines = log.splitlines()

    # 첫 [COND] 감시 종목 갱신 직후 OHLCV-DBG 로그에서 80개 watchlist 추출
    start_marker = f"{target_date} 08:35:47.821"
    end_marker = f"{target_date} 09:00"
    pat = re.compile(r"\[OHLCV-DBG\]\s+(\S+)\s+prev_high=([\d.]+)\s+prev_vol=(\d+)")

    rows: list[tuple[str, float, int]] = []
    seen: set[str] = set()
    in_window = False
    for line in lines:
        if start_marker in line:
            in_window = True
            continue
        if not in_window:
            continue
        if line.startswith(end_marker):
            break
        m = pat.search(line)
        if m:
            tk = m.group(1)
            if tk in seen:
                continue
            seen.add(tk)
            rows.append((tk, float(m.group(2)), int(m.group(3))))

    conn = sqlite3.connect("daytrader.db")
    items: list[tuple[str, float, str]] = []
    for tk, ph, pv in rows:
        r = conn.execute(
            "SELECT SUM(close * volume) FROM intraday_candles "
            "WHERE ticker=? AND tf='1m' AND date(ts)=?",
            (tk, prev_date),
        ).fetchone()
        if r and r[0] and r[0] > 0:
            items.append((tk, float(r[0]), "exact"))
        else:
            items.append((tk, ph * pv, "estimate"))
    conn.close()

    items.sort(key=lambda x: x[1], reverse=True)

    def eok(x: float) -> float:
        return x / 1e8

    n_exact = sum(1 for x in items if x[2] == "exact")
    n_est = sum(1 for x in items if x[2] == "estimate")
    print(f"[1] watchlist {len(items)} (exact={n_exact}, estimate={n_est})")

    print("\n[2] Quantile (eok=billion KRW)")
    for p in [1, 5, 10, 20, 40, 60, 70, 75, 80]:
        if p <= len(items):
            tk, amt, src = items[p-1]
            print(f"   {p:>3}: {eok(amt):>9,.0f}  ({tk}, {src})")

    print("\n[3] Threshold pass count (in current 80)")
    for th_e in [20, 30, 40, 50, 60, 70, 80, 100, 150]:
        th = th_e * 1e8
        passed = sum(1 for x in items if x[1] >= th)
        print(f"   >={th_e:>3} eok: {passed:>3}")

    print("\n[4] Bottom 21 (rank 60..80)")
    for i in range(59, len(items)):
        tk, amt, src = items[i]
        print(f"   {i+1}: {tk}  {eok(amt):>8,.1f}  ({src})")

    print("\n[5] Stats")
    amts = [x[1] for x in items]
    print(f"   mean:   {eok(sum(amts)/len(amts)):>9,.0f}")
    print(f"   median: {eok(statistics.median(amts)):>9,.0f}")
    print(f"   min:    {eok(min(amts)):>9,.0f}")
    print(f"   max:    {eok(max(amts)):>9,.0f}")


if __name__ == "__main__":
    main()
