"""tests/test_db_manager.py"""

import pytest
from data.db_manager import DbManager


@pytest.mark.asyncio
async def test_init_creates_tables():
    db = DbManager(":memory:")
    await db.init()
    tables = await db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    names = [row["name"] for row in tables]
    assert "trades" in names
    assert "positions" in names
    assert "daily_pnl" in names
    assert "intraday_candles" in names
    assert "screener_results" in names
    assert "system_log" in names
    await db.close()


@pytest.mark.asyncio
async def test_insert_and_fetch_trade():
    db = DbManager(":memory:")
    await db.init()
    await db.execute(
        "INSERT INTO trades (ticker,strategy,side,order_type,price,qty,amount,traded_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("005930", "orb", "buy", "market", 70000, 10, 700000, "2026-03-23T09:10:00"),
    )
    rows = await db.fetch_all("SELECT * FROM trades WHERE ticker='005930'")
    assert len(rows) == 1
    assert rows[0]["price"] == 70000
    await db.close()


@pytest.mark.asyncio
async def test_candle_unique_constraint():
    db = DbManager(":memory:")
    await db.init()
    params = ("005930", "1m", "2026-03-23T09:01:00", 70000, 70500, 69500, 70200, 1000, 70100)
    await db.execute(
        "INSERT INTO intraday_candles (ticker,tf,ts,open,high,low,close,volume,vwap) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        params,
    )
    await db.execute_safe(
        "INSERT OR IGNORE INTO intraday_candles (ticker,tf,ts,open,high,low,close,volume,vwap) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        params,
    )
    rows = await db.fetch_all("SELECT * FROM intraday_candles")
    assert len(rows) == 1
    await db.close()
