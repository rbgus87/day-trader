"""tests/test_atr_stop.py — ATR 기반 동적 손절 테스트 (Phase 2 Day 6)."""

import os

import pytest

from core.indicators import calculate_atr_stop_loss, get_latest_atr


# ──────────────────────────────────────────────────────────────────────
# calculate_atr_stop_loss — 순수 함수
# ──────────────────────────────────────────────────────────────────────

def test_atr_stop_basic():
    """ATR 3%, multiplier 1.5 → 손절폭 4.5%."""
    stop = calculate_atr_stop_loss(10000, 0.03, 1.5, 0.015, 0.08)
    assert stop == pytest.approx(9550.0)


def test_atr_stop_min_clamp():
    """ATR 0.5% × 1.5 = 0.75% → 하한 1.5%로 클램프."""
    stop = calculate_atr_stop_loss(10000, 0.005, 1.5, 0.015, 0.08)
    assert stop == pytest.approx(9850.0)


def test_atr_stop_max_clamp():
    """ATR 10% × 1.5 = 15% → 상한 8%로 클램프."""
    stop = calculate_atr_stop_loss(10000, 0.10, 1.5, 0.015, 0.08)
    assert stop == pytest.approx(9200.0)


def test_atr_stop_different_multiplier():
    """multiplier 2.0 적용 시 손절폭이 배수만큼 확장."""
    stop = calculate_atr_stop_loss(10000, 0.03, 2.0, 0.015, 0.08)
    # 0.03 * 2.0 = 0.06 → 6%
    assert stop == pytest.approx(9400.0)


# ──────────────────────────────────────────────────────────────────────
# get_latest_atr — DB 조회 (통합)
# ──────────────────────────────────────────────────────────────────────

DB_PATH = "daytrader.db"


@pytest.mark.skipif(not os.path.exists(DB_PATH), reason="daytrader.db 미존재")
def test_atr_fetch_from_db_samsung():
    """삼성전자(005930)는 대형주 — ATR%가 0.5%~8% 합리적 범위."""
    atr = get_latest_atr(DB_PATH, "005930")
    assert atr is not None
    assert 0.005 < atr < 0.08


@pytest.mark.skipif(not os.path.exists(DB_PATH), reason="daytrader.db 미존재")
def test_atr_fetch_nonexistent_ticker():
    """존재하지 않는 종목은 None."""
    atr = get_latest_atr(DB_PATH, "999999")
    assert atr is None


@pytest.mark.skipif(not os.path.exists(DB_PATH), reason="daytrader.db 미존재")
def test_atr_fetch_accepts_yyyymmdd_and_dashed():
    """YYYYMMDD / YYYY-MM-DD 둘 다 수용 (결과 동일)."""
    a1 = get_latest_atr(DB_PATH, "005930", "20260410")
    a2 = get_latest_atr(DB_PATH, "005930", "2026-04-10")
    assert a1 is not None
    assert a2 is not None
    assert a1 == a2


def test_atr_fetch_invalid_db_path_returns_none():
    """존재하지 않는 DB 파일은 예외 없이 None."""
    atr = get_latest_atr("/nonexistent/path.db", "005930")
    assert atr is None


# ──────────────────────────────────────────────────────────────────────
# ATR TP1 — Phase 2 Day 7
# ──────────────────────────────────────────────────────────────────────

def test_atr_tp1_basic():
    """ATR 3%, multiplier 3.0 → TP1 +9%."""
    from core.indicators import calculate_atr_tp1

    tp = calculate_atr_tp1(10000, 0.03, 3.0, 0.03, 0.25)
    assert tp == pytest.approx(10900.0)


def test_atr_tp1_min_clamp():
    """ATR 0.5% × 3.0 = 1.5% → 하한 3% 적용."""
    from core.indicators import calculate_atr_tp1

    tp = calculate_atr_tp1(10000, 0.005, 3.0, 0.03, 0.25)
    assert tp == pytest.approx(10300.0)


def test_atr_tp1_max_clamp():
    """ATR 15% × 3.0 = 45% → 상한 25% 적용."""
    from core.indicators import calculate_atr_tp1

    tp = calculate_atr_tp1(10000, 0.15, 3.0, 0.03, 0.25)
    assert tp == pytest.approx(12500.0)


# ──────────────────────────────────────────────────────────────────────
# ATR 트레일링 — Chandelier
# ──────────────────────────────────────────────────────────────────────

def test_atr_trailing_basic():
    """peak=10000, ATR 3%, multiplier 2.5 → 트레일 -7.5%."""
    from core.indicators import calculate_atr_trailing_stop

    trail = calculate_atr_trailing_stop(10000, 0.03, 2.5, 0.02, 0.10)
    assert trail == pytest.approx(9250.0)


def test_atr_trailing_min_clamp():
    """ATR 0.5% × 2.5 = 1.25% → 하한 2%."""
    from core.indicators import calculate_atr_trailing_stop

    trail = calculate_atr_trailing_stop(10000, 0.005, 2.5, 0.02, 0.10)
    assert trail == pytest.approx(9800.0)


def test_atr_trailing_max_clamp():
    """ATR 10% × 2.5 = 25% → 상한 10%."""
    from core.indicators import calculate_atr_trailing_stop

    trail = calculate_atr_trailing_stop(10000, 0.10, 2.5, 0.02, 0.10)
    assert trail == pytest.approx(9000.0)
