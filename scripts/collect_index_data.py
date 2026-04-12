"""scripts/collect_index_data.py — 코스피/코스닥 지수 일봉 수집 + DB 저장.

키움 ka20006 `cur_prc` 등은 100배 스케일. 저장 시 / 100 로 실제값으로 변환.
1회 호출로 600일 반환되므로 기본 1페이지만 수집 (충분).

사용:
    python scripts/collect_index_data.py
"""

import asyncio
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import AppConfig
from core.auth import TokenManager
from core.kiwoom_rest import KiwoomRestClient
from core.rate_limiter import AsyncRateLimiter


# data/db_manager.py의 스키마와 동일
_ENSURE_TABLE = """
CREATE TABLE IF NOT EXISTS index_candles (
    index_code TEXT NOT NULL,
    dt         TEXT NOT NULL,
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL,
    volume     INTEGER,
    PRIMARY KEY (index_code, dt)
);
CREATE INDEX IF NOT EXISTS idx_index_candles_dt ON index_candles(index_code, dt);
"""


async def collect_index(
    rest: KiwoomRestClient,
    index_code: str,
    db_path: str,
    max_pages: int = 5,
) -> None:
    print(f"\n지수 {index_code} 수집 중...")

    all_items: list[dict] = []
    base_dt = datetime.now().strftime("%Y%m%d")

    for page in range(max_pages):
        data = await rest.get_index_daily(index_code, base_dt=base_dt)
        items = data.get("inds_dt_pole_qry") or []
        if not items:
            break
        all_items.extend(items)
        print(f"  page {page + 1}: +{len(items)}건 (base_dt={base_dt})")

        oldest = items[-1]["dt"]
        next_dt = (datetime.strptime(oldest, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        if next_dt == base_dt or len(items) < 100:
            break
        base_dt = next_dt

    # 날짜 기준 중복 제거
    unique: dict[str, dict] = {}
    for c in all_items:
        unique[c["dt"]] = c
    rows = list(unique.values())
    print(f"  유니크: {len(rows)}건")

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_ENSURE_TABLE)
        for c in rows:
            conn.execute(
                "INSERT OR REPLACE INTO index_candles "
                "(index_code, dt, open, high, low, close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    index_code,
                    c["dt"],
                    float(c["open_pric"]) / 100,
                    float(c["high_pric"]) / 100,
                    float(c["low_pric"]) / 100,
                    float(c["cur_prc"]) / 100,
                    int(c["trde_qty"]),
                ),
            )
        conn.commit()

        cur = conn.execute(
            "SELECT MIN(dt), MAX(dt), COUNT(*) FROM index_candles WHERE index_code=?",
            (index_code,),
        )
        min_dt, max_dt, count = cur.fetchone()
        print(f"  [OK] 저장: {count}건 ({min_dt} ~ {max_dt})")
    finally:
        conn.close()


async def main() -> int:
    cfg = AppConfig.from_yaml()
    tm = TokenManager(
        cfg.kiwoom.app_key, cfg.kiwoom.secret_key, cfg.kiwoom.rest_base_url
    )
    rl = AsyncRateLimiter(
        max_calls=cfg.kiwoom.rate_limit_calls, period=cfg.kiwoom.rate_limit_period
    )
    rest = KiwoomRestClient(cfg.kiwoom, tm, rl)
    try:
        await collect_index(rest, "001", cfg.db_path)   # 코스피
        await collect_index(rest, "101", cfg.db_path)   # 코스닥
    finally:
        await rest.aclose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
