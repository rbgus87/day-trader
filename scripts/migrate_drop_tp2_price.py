"""scripts/migrate_drop_tp2_price.py — positions 테이블 tp2_price 컬럼 제거.

tp2는 트레일링 스톱으로 관리되며 더 이상 쓰이지 않음. 스키마에 남은
잔재 컬럼 제거.

SQLite 3.35+ 는 ALTER TABLE DROP COLUMN 지원하나 이식성 위해 전통적
방법(신규 테이블 생성 + 데이터 복사 + 교체) 사용.

사용:
    python scripts/migrate_drop_tp2_price.py             # dry-run
    python scripts/migrate_drop_tp2_price.py --apply     # 실행 + 백업
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("daytrader.db")
BACKUP_PATH = Path("daytrader_pre_tp2drop_20260415.db")

NEW_POSITIONS_SQL = """
CREATE TABLE positions_new (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT NOT NULL,
    strategy      TEXT NOT NULL,
    entry_price   REAL NOT NULL,
    qty           INTEGER NOT NULL,
    remaining_qty INTEGER NOT NULL,
    stop_loss     REAL NOT NULL,
    tp1_price     REAL,
    trailing_pct  REAL,
    status        TEXT DEFAULT 'open',
    opened_at     TEXT NOT NULL,
    closed_at     TEXT
)
"""

COPY_SQL = """
INSERT INTO positions_new (
    id, ticker, strategy, entry_price, qty, remaining_qty,
    stop_loss, tp1_price, trailing_pct, status, opened_at, closed_at
)
SELECT id, ticker, strategy, entry_price, qty, remaining_qty,
       stop_loss, tp1_price, trailing_pct, status, opened_at, closed_at
  FROM positions
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"[ERROR] DB 없음: {DB_PATH}")
        return 1

    conn = sqlite3.connect(DB_PATH)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(positions)").fetchall()]
        print(f"현재 positions 컬럼: {cols}")
        if "tp2_price" not in cols:
            print("[SKIP] tp2_price 컬럼이 이미 없음")
            return 0

        n = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        print(f"positions 레코드: {n}건")

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

        try:
            conn.execute("BEGIN")
            conn.execute(NEW_POSITIONS_SQL)
            conn.execute(COPY_SQL)
            conn.execute("DROP TABLE positions")
            conn.execute("ALTER TABLE positions_new RENAME TO positions")
            conn.commit()
            print("[OK] 마이그레이션 완료")
        except Exception as e:
            conn.rollback()
            print(f"[ERROR] 롤백: {e}")
            return 1

        cols_after = [r[1] for r in conn.execute("PRAGMA table_info(positions)").fetchall()]
        print(f"적용 후 컬럼: {cols_after}")
        n_after = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        print(f"적용 후 레코드: {n_after}건 (기대 {n}건)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
