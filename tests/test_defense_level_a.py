"""tests/test_defense_level_a.py — Phase 3 Day 11.5 방어 레벨 A."""

import os
import sqlite3
import tempfile
from dataclasses import replace
from datetime import datetime, time, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from config.settings import AppConfig, TradingConfig
from risk.risk_manager import RiskManager
from strategy.momentum_strategy import MomentumStrategy


# ──────────────────────────────────────────────────────────────────────
# 매수 시간 제한
# ──────────────────────────────────────────────────────────────────────

def test_buy_time_limit_before_cutoff():
    """11:30 이전은 허용 (10:30)."""
    cfg = AppConfig.from_yaml().trading
    strategy = MomentumStrategy(cfg)
    strategy._backtest_time = time(10, 30)
    assert strategy._check_buy_time_limit() is False


def test_buy_time_limit_after_cutoff():
    """11:30 이후 차단 (12:00)."""
    cfg = AppConfig.from_yaml().trading
    strategy = MomentumStrategy(cfg)
    strategy._backtest_time = time(12, 0)
    assert strategy._check_buy_time_limit() is True


def test_buy_time_limit_at_cutoff():
    """정확히 11:30은 차단 (>=)."""
    cfg = AppConfig.from_yaml().trading
    strategy = MomentumStrategy(cfg)
    strategy._backtest_time = time(11, 30)
    assert strategy._check_buy_time_limit() is True


def test_buy_time_limit_disabled():
    """비활성 시 항상 False."""
    cfg = replace(AppConfig.from_yaml().trading, buy_time_limit_enabled=False)
    strategy = MomentumStrategy(cfg)
    strategy._backtest_time = time(15, 0)
    assert strategy._check_buy_time_limit() is False


# ──────────────────────────────────────────────────────────────────────
# 일일 손실 한도 -1.5% 강화
# ──────────────────────────────────────────────────────────────────────

def test_daily_max_loss_tighter_ok():
    """-1.4% → 미달."""
    rm = RiskManager(
        trading_config=TradingConfig(daily_max_loss_pct=-0.015),
        db=MagicMock(), notifier=AsyncMock(),
    )
    rm.set_daily_capital(1_000_000)
    rm._daily_pnl = -14_000
    assert rm.is_trading_halted() is False


def test_daily_max_loss_tighter_reached():
    """-1.6% → 초과."""
    rm = RiskManager(
        trading_config=TradingConfig(daily_max_loss_pct=-0.015),
        db=MagicMock(), notifier=AsyncMock(),
    )
    rm.set_daily_capital(1_000_000)
    rm._daily_pnl = -16_000
    assert rm.is_trading_halted() is True


# ──────────────────────────────────────────────────────────────────────
# 연속 손실 휴식 (DB 기반)
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


def _insert_daily_pnl(db_path: str, pnl: float, days_ago: int) -> None:
    """days_ago일 전 날짜로 pnl 1건 기록."""
    traded_at = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO trades (ticker, strategy, side, order_type, price, qty, amount, "
        "pnl, pnl_pct, exit_reason, traded_at) "
        "VALUES ('X', 'm', 'sell', 'market', 1, 1, 1, ?, 0, '', ?)",
        (pnl, traded_at),
    )
    conn.commit()
    conn.close()


def _make_rm() -> RiskManager:
    return RiskManager(
        trading_config=TradingConfig(),
        db=MagicMock(),
        notifier=AsyncMock(),
    )


def test_loss_rest_not_triggered_empty_history(temp_db):
    """기록 없으면 False."""
    rm = _make_rm()
    assert rm.is_in_loss_rest(db_path=temp_db) is False


def test_loss_rest_not_triggered_below_threshold(temp_db):
    """2일 연속 손실 → threshold(3) 미달."""
    rm = _make_rm()
    _insert_daily_pnl(temp_db, -1000, days_ago=1)
    _insert_daily_pnl(temp_db, -500, days_ago=2)
    assert rm.is_in_loss_rest(db_path=temp_db) is False


def test_loss_rest_triggered_at_threshold(temp_db):
    """3일 연속 손실 → 휴식."""
    rm = _make_rm()
    _insert_daily_pnl(temp_db, -1000, days_ago=1)
    _insert_daily_pnl(temp_db, -500, days_ago=2)
    _insert_daily_pnl(temp_db, -700, days_ago=3)
    assert rm.is_in_loss_rest(db_path=temp_db) is True


def test_loss_rest_broken_by_profit(temp_db):
    """중간에 이익일이 있으면 연속성 깨짐."""
    rm = _make_rm()
    _insert_daily_pnl(temp_db, -1000, days_ago=1)
    _insert_daily_pnl(temp_db, +500, days_ago=2)   # 이익 (연속 깨짐)
    _insert_daily_pnl(temp_db, -700, days_ago=3)
    _insert_daily_pnl(temp_db, -400, days_ago=4)
    assert rm.is_in_loss_rest(db_path=temp_db) is False


def test_loss_rest_disabled(temp_db):
    """비활성 시 항상 False."""
    rm = RiskManager(
        trading_config=replace(TradingConfig(), consecutive_loss_rest_enabled=False),
        db=MagicMock(), notifier=AsyncMock(),
    )
    for d in (1, 2, 3, 4):
        _insert_daily_pnl(temp_db, -1000, days_ago=d)
    assert rm.is_in_loss_rest(db_path=temp_db) is False
