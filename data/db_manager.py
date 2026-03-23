"""data/db_manager.py — SQLite 비동기 CRUD."""

import aiosqlite
from loguru import logger

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT NOT NULL,
    strategy     TEXT NOT NULL,
    side         TEXT NOT NULL,
    order_type   TEXT NOT NULL,
    price        REAL NOT NULL,
    qty          INTEGER NOT NULL,
    amount       REAL NOT NULL,
    pnl          REAL,
    pnl_pct      REAL,
    exit_reason  TEXT,
    traded_at    TEXT NOT NULL,
    created_at   TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS positions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT NOT NULL,
    strategy      TEXT NOT NULL,
    entry_price   REAL NOT NULL,
    qty           INTEGER NOT NULL,
    remaining_qty INTEGER NOT NULL,
    stop_loss     REAL NOT NULL,
    tp1_price     REAL,
    tp2_price     REAL,
    trailing_pct  REAL,
    status        TEXT DEFAULT 'open',
    opened_at     TEXT NOT NULL,
    closed_at     TEXT
);

CREATE TABLE IF NOT EXISTS intraday_candles (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker  TEXT NOT NULL,
    tf      TEXT NOT NULL,
    ts      TEXT NOT NULL,
    open    REAL,
    high    REAL,
    low     REAL,
    close   REAL,
    volume  INTEGER,
    vwap    REAL,
    UNIQUE(ticker, tf, ts)
);

CREATE TABLE IF NOT EXISTS daily_pnl (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT NOT NULL UNIQUE,
    strategy      TEXT,
    total_trades  INTEGER DEFAULT 0,
    wins          INTEGER DEFAULT 0,
    losses        INTEGER DEFAULT 0,
    win_rate      REAL,
    total_pnl     REAL DEFAULT 0,
    max_drawdown  REAL,
    created_at    TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS screener_results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    score         REAL,
    strategy_hint TEXT,
    selected      INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS system_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    level      TEXT NOT NULL,
    event      TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
"""


class DbManager:
    """aiosqlite 기반 비동기 DB 관리."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA_SQL)
        await self._conn.commit()
        logger.info(f"DB 초기화 완료: {self._db_path}")

    async def execute(self, sql: str, params: tuple = ()) -> int:
        cursor = await self._conn.execute(sql, params)
        await self._conn.commit()
        return cursor.lastrowid

    async def execute_safe(self, sql: str, params: tuple = ()) -> int | None:
        """실패해도 예외를 던지지 않음 (로그만 기록)."""
        try:
            return await self.execute(sql, params)
        except Exception as e:
            logger.warning(f"DB execute_safe 실패: {e}")
            return None

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        cursor = await self._conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        cursor = await self._conn.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
