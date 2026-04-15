"""scripts/check_db_integrity.py - 일일 정합성 검증.

매매 종료 후 DB 기록이 서로 정합한지 확인. 이상 항목 있으면
exit 1. 운영 매뉴얼 (docs/runbook.md)에서 "일일 운영 종료 후" 체크.

검증 항목:
1. trades.sum(pnl) == daily_pnl.total_pnl (해당 날짜)
2. 미청산 positions (status='open') - 장 종료 후 0건이 정상
3. 장부 정합 (ticker별 buy 수량 == sell 수량 누적)
4. 도메인 검증:
   - exit_reason ∈ {NULL, stop_loss, tp1_hit, trailing_stop,
     forced_close, rebuild_stop, time_stop(legacy)}
   - order_type ∈ {NULL, market, limit}
   - strategy ≠ 'unknown' (매도 거래)

사용:
    python scripts/check_db_integrity.py                 # 오늘
    python scripts/check_db_integrity.py --date 2026-04-15
    python scripts/check_db_integrity.py --all           # 전체 기간
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path("daytrader.db")

VALID_EXIT_REASONS = {
    None, "stop_loss", "tp1_hit", "trailing_stop",
    "forced_close", "rebuild_stop", "time_stop",  # time_stop은 legacy
}
VALID_ORDER_TYPES = {None, "market", "limit"}


class Check:
    def __init__(self):
        self.issues: list[str] = []
        self.warns: list[str] = []
        self.passed: list[str] = []

    def ok(self, msg: str) -> None:
        self.passed.append(msg)

    def warn(self, msg: str) -> None:
        self.warns.append(msg)

    def fail(self, msg: str) -> None:
        self.issues.append(msg)

    def print_report(self) -> int:
        print("=" * 70)
        status = "[OK]" if not self.issues else f"[FAIL] 불일치 {len(self.issues)}건"
        if self.warns:
            status += f" (WARN {len(self.warns)}건)"
        print(f" 검증 결과: {status}")
        print("=" * 70)
        for p in self.passed:
            print(f"  [OK] {p}")
        for w in self.warns:
            print(f"  [WARN] {w}")
        for i in self.issues:
            print(f"  [FAIL] {i}")
        return 0 if not self.issues else 1


def check_daily_pnl_match(conn, date: str, chk: Check) -> None:
    t = conn.execute(
        "SELECT COALESCE(SUM(pnl), 0) FROM trades "
        "WHERE side='sell' AND date(traded_at)=?", (date,),
    ).fetchone()[0]
    d_row = conn.execute(
        "SELECT total_pnl FROM daily_pnl WHERE date=?", (date,),
    ).fetchone()
    d = d_row[0] if d_row else None
    has_trades = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE date(traded_at)=?", (date,),
    ).fetchone()[0] > 0
    if not has_trades:
        chk.ok(f"{date}: trades 없음 (비거래일)")
        return
    if d is None:
        chk.fail(f"{date}: trades 있으나 daily_pnl 엔트리 없음")
        return
    if abs(t - d) < 0.01:
        chk.ok(f"{date}: trades.sum(pnl)={t:+,.0f} == daily_pnl.total_pnl={d:+,.0f}")
    else:
        chk.fail(f"{date}: trades.sum={t:+,.0f} vs daily_pnl={d:+,.0f} diff={t - d:+,.0f}")


def check_open_positions(conn, chk: Check) -> None:
    rows = conn.execute(
        "SELECT ticker, strategy, opened_at FROM positions WHERE status='open'"
    ).fetchall()
    if not rows:
        chk.ok("미청산 positions: 0건")
    else:
        for t, s, o in rows:
            chk.fail(f"미청산 position: {t} ({s}) opened_at={o}")


def check_cumulative_inventory(conn, chk: Check) -> None:
    """전체 기간 누적: ticker별 SUM(buy) == SUM(sell) 이어야 최종 정합."""
    rows = conn.execute(
        "SELECT ticker, "
        "SUM(CASE WHEN side='buy' THEN qty ELSE 0 END) AS buy_qty, "
        "SUM(CASE WHEN side='sell' THEN qty ELSE 0 END) AS sell_qty "
        "FROM trades GROUP BY ticker"
    ).fetchall()
    unbalanced = [(t, b, s) for t, b, s in rows if b != s]
    if not unbalanced:
        chk.ok("누적 장부 정합: 모든 ticker buy==sell")
    else:
        for t, b, s in unbalanced:
            chk.fail(f"누적 장부 미정합: {t} buy={b}, sell={s}, 잔여={b - s}")


def check_inventory_balance(conn, date: str, chk: Check) -> None:
    """ticker별 buy 수량 - sell 수량 = 0이어야 정상 (당일 내).

    오버나이트 포지션은 누적 정합 체크(check_cumulative_inventory)에서 검증.
    """
    rows = conn.execute(
        "SELECT ticker, "
        "SUM(CASE WHEN side='buy' THEN qty ELSE 0 END) AS buy_qty, "
        "SUM(CASE WHEN side='sell' THEN qty ELSE 0 END) AS sell_qty "
        "FROM trades WHERE date(traded_at)=? GROUP BY ticker",
        (date,),
    ).fetchall()
    unbalanced = [(t, b, s) for t, b, s in rows if b != s]
    if not unbalanced:
        chk.ok(f"{date}: 당일 장부 정합 - 모든 ticker buy==sell")
    else:
        for t, b, s in unbalanced:
            # 당일 불균형은 WARN 수준 (오버나이트 포지션). 누적 정합은 별도 체크.
            chk.warn(f"{date}: {t} 당일 불균형 buy={b}, sell={s}, 잔여={b - s} (오버나이트 가능)")


def check_domain_values(conn, date: str | None, chk: Check) -> None:
    where = "WHERE date(traded_at)=?" if date else ""
    params = (date,) if date else ()

    exit_reasons = {r[0] for r in conn.execute(
        f"SELECT DISTINCT exit_reason FROM trades {where}", params,
    ).fetchall()}
    bad_reasons = exit_reasons - VALID_EXIT_REASONS
    if bad_reasons:
        chk.fail(f"exit_reason 도메인 위반: {bad_reasons}")
    else:
        chk.ok(f"exit_reason 도메인 OK: {sorted(r for r in exit_reasons if r)}")

    order_types = {r[0] for r in conn.execute(
        f"SELECT DISTINCT order_type FROM trades {where}", params,
    ).fetchall()}
    bad_types = order_types - VALID_ORDER_TYPES
    if bad_types:
        chk.fail(f"order_type 도메인 위반: {bad_types}")
    else:
        chk.ok(f"order_type 도메인 OK: {sorted(t for t in order_types if t)}")

    unknown_cnt = conn.execute(
        f"SELECT COUNT(*) FROM trades {where} {'AND' if date else 'WHERE'} "
        "side='sell' AND (strategy='unknown' OR strategy IS NULL OR strategy='')",
        params,
    ).fetchone()[0]
    if unknown_cnt:
        chk.fail(f"매도 거래 중 strategy 미기재: {unknown_cnt}건")
    else:
        chk.ok("매도 거래 strategy 모두 명시")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--all", action="store_true", help="전체 기간 일일 정합 + 도메인")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"[ERROR] DB 없음: {DB_PATH}")
        return 1

    conn = sqlite3.connect(DB_PATH)
    chk = Check()
    try:
        if args.all:
            dates = [r[0] for r in conn.execute(
                "SELECT DISTINCT date(traded_at) FROM trades ORDER BY 1"
            ).fetchall() if r[0]]
            for d in dates:
                check_daily_pnl_match(conn, d, chk)
                check_inventory_balance(conn, d, chk)
            check_domain_values(conn, None, chk)
            check_cumulative_inventory(conn, chk)
        else:
            check_daily_pnl_match(conn, args.date, chk)
            check_inventory_balance(conn, args.date, chk)
            check_domain_values(conn, args.date, chk)
            check_cumulative_inventory(conn, chk)
        check_open_positions(conn, chk)
    finally:
        conn.close()

    return chk.print_report()


if __name__ == "__main__":
    sys.exit(main())
