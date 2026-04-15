"""scripts/fix_daily_pnl_20260415.py — 일회성 보정.

4/15 rebuild_stop 청산 4건은 trades에 기록됐으나 daily_pnl 엔트리가
없는 상태(Phase 0 정리 중 프로세스 종료 → save_daily_summary 미호출).

trades 합계로 daily_pnl UPSERT.

사용:
    python scripts/fix_daily_pnl_20260415.py              # dry-run
    python scripts/fix_daily_pnl_20260415.py --apply      # 실행 + 백업
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

TARGET_DATE = "2026-04-15"
DB_PATH = Path("daytrader.db")
BACKUP_PATH = Path("daytrader_pre_dailypnl_fix_20260415.db")


def collect_summary(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT strategy, pnl FROM trades "
        "WHERE side='sell' AND date(traded_at)=?",
        (TARGET_DATE,),
    ).fetchall()
    total_trades = len(rows)
    wins = sum(1 for _, p in rows if (p or 0) > 0)
    losses = total_trades - wins
    win_rate = (wins / total_trades) if total_trades else 0.0
    total_pnl = sum((p or 0) for _, p in rows)
    strategies = sorted({s for s, _ in rows if s})
    strategy_str = ",".join(strategies) if strategies else "none"
    # max_drawdown (누적 PnL peak-to-trough)
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for _, p in rows:
        cum += p or 0
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return {
        "date": TARGET_DATE,
        "strategy": strategy_str,
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "max_drawdown": max_dd,
    }


def existing_entry(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT date, strategy, total_trades, wins, losses, total_pnl, max_drawdown "
        "FROM daily_pnl WHERE date=?",
        (TARGET_DATE,),
    ).fetchone()
    if not row:
        return None
    return {
        "date": row[0], "strategy": row[1], "total_trades": row[2],
        "wins": row[3], "losses": row[4], "total_pnl": row[5],
        "max_drawdown": row[6],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"[ERROR] DB 없음: {DB_PATH}")
        return 1

    conn = sqlite3.connect(DB_PATH)
    try:
        before = existing_entry(conn)
        summary = collect_summary(conn)

        print(f"=== 4/15 daily_pnl 보정 ({'APPLY' if args.apply else 'DRY-RUN'}) ===")
        print(f"기존 엔트리: {before}")
        print(f"신규 집계  : {summary}")

        if summary["total_trades"] == 0:
            print("[SKIP] 4/15 sell 거래 0건 — 보정 불필요")
            return 0

        if not args.apply:
            print()
            print("[DRY-RUN] --apply 추가하면 실행")
            return 0

        # 백업
        if BACKUP_PATH.exists():
            print(f"[WARN] 백업 파일 이미 존재: {BACKUP_PATH}")
        else:
            shutil.copy2(DB_PATH, BACKUP_PATH)
            print(f"[OK] 백업: {BACKUP_PATH}")

        # UPSERT
        conn.execute(
            "INSERT INTO daily_pnl (date, strategy, total_trades, wins, losses, win_rate, total_pnl, max_drawdown) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(date) DO UPDATE SET "
            "strategy=excluded.strategy, total_trades=excluded.total_trades, "
            "wins=excluded.wins, losses=excluded.losses, win_rate=excluded.win_rate, "
            "total_pnl=excluded.total_pnl, max_drawdown=excluded.max_drawdown",
            (
                summary["date"], summary["strategy"], summary["total_trades"],
                summary["wins"], summary["losses"], summary["win_rate"],
                summary["total_pnl"], summary["max_drawdown"],
            ),
        )
        conn.commit()
        print(f"[OK] daily_pnl UPSERT 완료 ({datetime.now():%H:%M:%S})")

        # 정합 검증
        after = existing_entry(conn)
        print(f"적용 후 엔트리: {after}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
