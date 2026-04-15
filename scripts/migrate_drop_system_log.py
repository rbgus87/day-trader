"""scripts/migrate_drop_system_log.py — 미사용 system_log 테이블 제거.

스키마에만 있고 코드 어디서도 INSERT/SELECT 안 함.

사용:
    python scripts/migrate_drop_system_log.py             # dry-run
    python scripts/migrate_drop_system_log.py --apply     # 실행 + 백업
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("daytrader.db")
BACKUP_PATH = Path("daytrader_pre_syslog_drop.db")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"[ERROR] DB 없음: {DB_PATH}")
        return 1

    conn = sqlite3.connect(DB_PATH)
    try:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='system_log'"
        ).fetchone()
        if not exists:
            print("[SKIP] system_log 테이블이 이미 없음")
            return 0
        n = conn.execute("SELECT COUNT(*) FROM system_log").fetchone()[0]
        print(f"system_log 레코드: {n}건")

        if not args.apply:
            print()
            print("[DRY-RUN] --apply 추가하면 실행")
            return 0

        if BACKUP_PATH.exists():
            print(f"[WARN] 백업 파일 이미 존재: {BACKUP_PATH}")
        else:
            shutil.copy2(DB_PATH, BACKUP_PATH)
            print(f"[OK] 백업: {BACKUP_PATH}")

        conn.execute("DROP TABLE system_log")
        conn.commit()
        print("[OK] system_log 테이블 DROP 완료")

        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]
        print(f"현존 테이블: {tables}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
