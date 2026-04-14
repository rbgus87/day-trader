"""scripts/fix_20260414_data.py — 2026-04-14 페이퍼 1일차 trades/daily_pnl 보정.

배경:
  1일차 페이퍼 운영 중 DB 기록 버그 2건 발견.
    (A) trades.exit_reason이 매도 7건 모두 'force_close'로 하드코딩됨
        → 실제로는 time_stop 6건 + forced_close(15:10) 1건
    (B) trades.strategy가 매도 행에 모두 'unknown' 으로 기록됨
        → 매수 행은 'momentum'으로 정상 기록 (오늘은 momentum 단일 전략만 운영)
    daily_pnl.strategy도 'unknown'으로 오염됨.

보정 로직:
  - trades UPDATE:
      · side='sell' AND substr(traded_at,1,10)='2026-04-14' 대상
      · strategy='unknown' → 같은 ticker의 매수 행 strategy 복사 (없으면 'momentum' 폴백)
      · exit_reason:
          · traded_at이 '2026-04-14T15:10' 접두사면 → 'forced_close'
          · 그 외 → 'time_stop' (트레이드 로그 day.log 라인 772/789/815/833/850/855에서 전부
            "시간 손절"로 확인됨. stop_loss/tp1_hit/trailing_stop 경로 없음)
  - daily_pnl UPDATE:
      · date='2026-04-14' 행의 strategy='momentum' 으로 변경
      · 총건수/pnl은 이미 정확하므로 건드리지 않음.

사용법:
  python -m scripts.fix_20260414_data --dry-run   # 변경사항만 출력
  python -m scripts.fix_20260414_data --apply     # 실제 UPDATE 실행 (자동 백업 선행)
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path("daytrader.db")
TARGET_DATE = "2026-04-14"
DEFAULT_STRATEGY_FALLBACK = "momentum"


def backup_db() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = DB_PATH.with_name(f"daytrader_pre_fix_{ts}.db")
    shutil.copy2(DB_PATH, backup_path)
    return backup_path


def fetch_corrections(conn: sqlite3.Connection) -> list[dict]:
    """매도 행별 보정 목표 값 계산. 실제 UPDATE는 별도."""
    cur = conn.cursor()

    # 매수 행에서 ticker → strategy 매핑 (오늘자)
    buy_rows = cur.execute(
        "SELECT ticker, strategy FROM trades "
        "WHERE side='buy' AND substr(traded_at,1,10)=?",
        (TARGET_DATE,),
    ).fetchall()
    strategy_by_ticker = {t: s for t, s in buy_rows if s and s != "unknown"}

    sell_rows = cur.execute(
        "SELECT id, ticker, strategy, exit_reason, traded_at FROM trades "
        "WHERE side='sell' AND substr(traded_at,1,10)=? ORDER BY traded_at",
        (TARGET_DATE,),
    ).fetchall()

    plans = []
    for tid, ticker, strategy, exit_reason, traded_at in sell_rows:
        new_strategy = strategy_by_ticker.get(ticker, DEFAULT_STRATEGY_FALLBACK)
        # 기존 값이 이미 정상적이면 그대로 유지
        if strategy and strategy != "unknown":
            new_strategy = strategy

        # 15:10~15:10:59 사이면 forced_close, 나머지는 time_stop
        is_forced_close = traded_at.startswith(f"{TARGET_DATE}T15:10")
        new_reason = "forced_close" if is_forced_close else "time_stop"

        needs_update = (
            new_strategy != strategy or new_reason != exit_reason
        )
        plans.append({
            "id": tid,
            "ticker": ticker,
            "traded_at": traded_at,
            "strategy_old": strategy,
            "strategy_new": new_strategy,
            "reason_old": exit_reason,
            "reason_new": new_reason,
            "update": needs_update,
        })
    return plans


def print_plan(plans: list[dict]) -> None:
    print(f"=== {TARGET_DATE} 매도 {len(plans)}건 보정 계획 ===")
    header = f"{'id':>4} {'ticker':8} {'time':19} {'strategy':24} {'exit_reason':28}"
    print(header)
    print("-" * len(header))
    for p in plans:
        strat_col = f"{p['strategy_old']}→{p['strategy_new']}"
        reason_col = f"{p['reason_old']}→{p['reason_new']}"
        mark = "*" if p["update"] else " "
        print(
            f"{p['id']:>4} {p['ticker']:8} {p['traded_at'][:19]:19} "
            f"{strat_col:24} {reason_col:28} {mark}"
        )


def apply(conn: sqlite3.Connection, plans: list[dict]) -> int:
    cur = conn.cursor()
    changed = 0
    for p in plans:
        if not p["update"]:
            continue
        cur.execute(
            "UPDATE trades SET strategy=?, exit_reason=? WHERE id=?",
            (p["strategy_new"], p["reason_new"], p["id"]),
        )
        changed += 1

    # daily_pnl.strategy 보정
    cur.execute(
        "SELECT strategy FROM daily_pnl WHERE date=?", (TARGET_DATE,),
    )
    row = cur.fetchone()
    daily_changed = False
    if row is not None and row[0] != DEFAULT_STRATEGY_FALLBACK:
        cur.execute(
            "UPDATE daily_pnl SET strategy=? WHERE date=?",
            (DEFAULT_STRATEGY_FALLBACK, TARGET_DATE),
        )
        daily_changed = True
    conn.commit()
    return changed, daily_changed


def verify(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    print()
    print(f"=== 보정 후 확인 ({TARGET_DATE}) ===")
    rows = cur.execute(
        "SELECT exit_reason, strategy, COUNT(*) FROM trades "
        "WHERE side='sell' AND substr(traded_at,1,10)=? "
        "GROUP BY exit_reason, strategy ORDER BY exit_reason",
        (TARGET_DATE,),
    ).fetchall()
    for r, s, c in rows:
        print(f"  exit_reason={r:15} strategy={s:10} count={c}")
    print()
    print("=== daily_pnl ===")
    dp = cur.execute(
        "SELECT date, strategy, total_trades, wins, losses, total_pnl "
        "FROM daily_pnl WHERE date=?",
        (TARGET_DATE,),
    ).fetchone()
    if dp:
        print(
            f"  date={dp[0]} strategy={dp[1]} trades={dp[2]} "
            f"wins={dp[3]} losses={dp[4]} pnl={dp[5]:+.0f}"
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="변경사항만 출력")
    g.add_argument("--apply", action="store_true", help="실제 UPDATE 실행 (백업 자동)")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"[ERROR] {DB_PATH} 없음", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB_PATH)
    try:
        plans = fetch_corrections(conn)
        if not plans:
            print(f"[INFO] {TARGET_DATE} 매도 행 없음 — 보정할 데이터 없음")
            return 0

        print_plan(plans)

        if args.dry_run:
            print()
            print("[DRY RUN] --apply 로 실제 적용 가능")
            return 0

        # --apply
        conn.close()
        backup_path = backup_db()
        print()
        print(f"[INFO] DB 백업 생성: {backup_path}")
        conn = sqlite3.connect(DB_PATH)
        changed, daily_changed = apply(conn, plans)
        print(f"[INFO] trades UPDATE: {changed}건 / daily_pnl: {'1건' if daily_changed else '변경없음'}")
        verify(conn)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
