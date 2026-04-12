"""tests/test_defense.py — Phase 2 Day 10 약세장 방어 기능.

- 일일 최대 손실 한도 (enabled 플래그 제어)
- 종목 블랙리스트 (최근 N일 내 M회 이상 손실)
"""

import os
import sqlite3
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from config.settings import AppConfig, TradingConfig
from risk.risk_manager import RiskManager


# ──────────────────────────────────────────────────────────────────────
# 일일 손실 한도
# ──────────────────────────────────────────────────────────────────────

def _make_rm(cfg: TradingConfig) -> RiskManager:
    return RiskManager(trading_config=cfg, db=MagicMock(), notifier=AsyncMock())


def test_daily_loss_limit_ok():
    """-1% 손실 → 한도 미달 (기본 -2%)."""
    rm = _make_rm(TradingConfig())
    rm.set_daily_capital(1_000_000)
    rm._daily_pnl = -10_000
    assert rm.is_trading_halted() is False


def test_daily_loss_limit_reached():
    """-2.5% 손실 → 한도 도달."""
    rm = _make_rm(TradingConfig())
    rm.set_daily_capital(1_000_000)
    rm._daily_pnl = -25_000
    assert rm.is_trading_halted() is True


def test_daily_loss_limit_disabled():
    """daily_max_loss_enabled=False면 한도 체크 생략."""
    cfg = replace(TradingConfig(), daily_max_loss_enabled=False)
    rm = _make_rm(cfg)
    rm.set_daily_capital(1_000_000)
    rm._daily_pnl = -50_000  # -5%
    assert rm.is_trading_halted() is False


# ──────────────────────────────────────────────────────────────────────
# 블랙리스트 (DB 기반)
# ──────────────────────────────────────────────────────────────────────

SCHEMA_TRADES = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    strategy TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    price REAL NOT NULL,
    qty INTEGER NOT NULL,
    amount REAL NOT NULL,
    pnl REAL,
    pnl_pct REAL,
    exit_reason TEXT,
    traded_at TEXT NOT NULL
);
"""


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_TRADES)
    conn.commit()
    conn.close()
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


def _insert_sell(db_path: str, ticker: str, pnl: float, days_ago: int) -> None:
    traded_at = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO trades (ticker, strategy, side, order_type, price, qty, amount, "
        "pnl, pnl_pct, exit_reason, traded_at) "
        "VALUES (?, 'momentum', 'sell', 'market', 10000, 10, 100000, ?, 0, 'stop_loss', ?)",
        (ticker, pnl, traded_at),
    )
    conn.commit()
    conn.close()


def test_blacklist_below_threshold(temp_db):
    """최근 5일 내 손실 2회 → threshold(3) 미달."""
    cfg = TradingConfig()
    rm = _make_rm(cfg)
    _insert_sell(temp_db, "005930", -5000, days_ago=1)
    _insert_sell(temp_db, "005930", -3000, days_ago=2)
    assert rm.is_ticker_blacklisted("005930", db_path=temp_db) is False


def test_blacklist_at_threshold(temp_db):
    """최근 5일 내 손실 3회 → 블랙."""
    cfg = TradingConfig()
    rm = _make_rm(cfg)
    for d in (1, 2, 3):
        _insert_sell(temp_db, "005930", -5000, days_ago=d)
    assert rm.is_ticker_blacklisted("005930", db_path=temp_db) is True


def test_blacklist_wins_excluded(temp_db):
    """이익 거래는 집계에서 제외."""
    cfg = TradingConfig()
    rm = _make_rm(cfg)
    for d in (1, 2, 3):
        _insert_sell(temp_db, "005930", +5000, days_ago=d)  # 이익
    assert rm.is_ticker_blacklisted("005930", db_path=temp_db) is False


def test_blacklist_outside_lookback(temp_db):
    """lookback 기간 밖의 손실은 집계 제외."""
    cfg = TradingConfig()  # lookback 5일
    rm = _make_rm(cfg)
    for d in (10, 20, 30):  # 모두 5일 밖
        _insert_sell(temp_db, "005930", -5000, days_ago=d)
    assert rm.is_ticker_blacklisted("005930", db_path=temp_db) is False


def test_blacklist_disabled(temp_db):
    """blacklist_enabled=False면 항상 False."""
    cfg = replace(TradingConfig(), blacklist_enabled=False)
    rm = _make_rm(cfg)
    for d in (1, 2, 3, 4):
        _insert_sell(temp_db, "005930", -5000, days_ago=d)
    assert rm.is_ticker_blacklisted("005930", db_path=temp_db) is False


def test_blacklist_missing_db():
    """존재하지 않는 DB 경로 → False (안전 폴백)."""
    cfg = TradingConfig()
    rm = _make_rm(cfg)
    assert rm.is_ticker_blacklisted("005930", db_path="/nonexistent/db.sqlite") is False
