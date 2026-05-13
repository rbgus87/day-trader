"""tests/test_intraday_market_filter.py — 장중 시장 필터 단위 테스트."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from core.market_filter import MarketFilter, INDEX_KOSPI, INDEX_KOSDAQ


# ── 헬퍼 ────────────────────────────────────────────────────────────────────

def _make_rest(index_code: str, cur_prc: float, open_prc: float | None = None) -> AsyncMock:
    """오늘 데이터 포함 REST mock 생성."""
    today = datetime.now().strftime("%Y%m%d")
    today_item: dict = {"dt": today, "cur_prc": str(cur_prc)}
    if open_prc is not None:
        today_item["open_prc"] = str(open_prc)
    # items[1] = 전일 종가 (fallback용)
    prev_item = {"dt": "20260101", "cur_prc": str(cur_prc * 1.005)}  # 전일보다 -0.5%
    rest = AsyncMock()
    rest.get_index_daily.return_value = {
        "inds_dt_pole_qry": [today_item, prev_item] + [{"cur_prc": "10000"}] * 5
    }
    return rest


def _make_rest_both(
    kospi_cur: float, kospi_open: float,
    kosdaq_cur: float, kosdaq_open: float,
) -> AsyncMock:
    """코스피/코스닥 두 지수 모두 응답하는 REST mock."""
    today = datetime.now().strftime("%Y%m%d")
    prev_item = {"dt": "20260101", "cur_prc": "100"}

    call_count = 0
    async def side_effect(index_code):
        if index_code == "001":
            return {"inds_dt_pole_qry": [
                {"dt": today, "cur_prc": str(kospi_cur), "open_prc": str(kospi_open)},
                prev_item,
            ] + [{"cur_prc": "10000"}] * 5}
        else:
            return {"inds_dt_pole_qry": [
                {"dt": today, "cur_prc": str(kosdaq_cur), "open_prc": str(kosdaq_open)},
                prev_item,
            ] + [{"cur_prc": "10000"}] * 5}

    rest = AsyncMock()
    rest.get_index_daily.side_effect = side_effect
    return rest


# ── 초기 상태 ────────────────────────────────────────────────────────────────

def test_initial_intraday_not_blocked():
    """초기 상태에서 장중 차단은 비활성."""
    mf = MarketFilter(rest=None)
    assert mf.is_intraday_blocked("kospi") is False
    assert mf.is_intraday_blocked("kosdaq") is False
    assert mf.is_intraday_blocked("unknown") is False


# ── 차단 조건 ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_block_when_change_below_threshold():
    """-1% 이하 → 차단."""
    # open=10000, cur=9850 → change = -1.5%
    rest = _make_rest_both(9850, 10000, 9850, 10000)
    mf = MarketFilter(rest=rest)
    await mf.refresh_intraday(block_threshold=-0.01, resume_threshold=-0.005)
    assert mf.is_intraday_blocked("kospi") is True
    assert mf.is_intraday_blocked("kosdaq") is True


@pytest.mark.asyncio
async def test_not_blocked_when_change_above_threshold():
    """-0.5% → 차단 기준(-1%) 미달 → 허용."""
    # open=10000, cur=9950 → change = -0.5%
    rest = _make_rest_both(9950, 10000, 9950, 10000)
    mf = MarketFilter(rest=rest)
    await mf.refresh_intraday(block_threshold=-0.01, resume_threshold=-0.005)
    assert mf.is_intraday_blocked("kospi") is False
    assert mf.is_intraday_blocked("kosdaq") is False


@pytest.mark.asyncio
async def test_block_kospi_only():
    """코스피만 -1% → 코스피 차단, 코스닥 허용."""
    today = datetime.now().strftime("%Y%m%d")
    prev = {"dt": "20260101", "cur_prc": "100"}

    async def side_effect(index_code):
        if index_code == "001":  # KOSPI
            return {"inds_dt_pole_qry": [
                {"dt": today, "cur_prc": "9850", "open_prc": "10000"},
                prev,
            ] + [{"cur_prc": "10000"}] * 5}
        else:  # KOSDAQ
            return {"inds_dt_pole_qry": [
                {"dt": today, "cur_prc": "9960", "open_prc": "10000"},
                prev,
            ] + [{"cur_prc": "10000"}] * 5}

    rest = AsyncMock()
    rest.get_index_daily.side_effect = side_effect
    mf = MarketFilter(rest=rest)
    await mf.refresh_intraday(block_threshold=-0.01, resume_threshold=-0.005)
    assert mf.is_intraday_blocked("kospi") is True
    assert mf.is_intraday_blocked("kosdaq") is False
    # unknown → 하나라도 차단이면 True
    assert mf.is_intraday_blocked("unknown") is True


# ── 해제 조건 ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unblock_when_recovery():
    """차단 상태에서 -0.5% 이상 회복 시 해제."""
    rest = _make_rest_both(9850, 10000, 9850, 10000)
    mf = MarketFilter(rest=rest)
    # 차단
    await mf.refresh_intraday(block_threshold=-0.01, resume_threshold=-0.005)
    assert mf.is_intraday_blocked("kospi") is True

    # 회복 (-0.3%)
    rest2 = _make_rest_both(9970, 10000, 9970, 10000)
    mf._rest = rest2
    await mf.refresh_intraday(block_threshold=-0.01, resume_threshold=-0.005)
    assert mf.is_intraday_blocked("kospi") is False


@pytest.mark.asyncio
async def test_stays_blocked_if_not_recovered():
    """차단 상태에서 -0.8%는 resume_threshold(-0.5%) 미달 → 차단 유지."""
    rest = _make_rest_both(9850, 10000, 9850, 10000)
    mf = MarketFilter(rest=rest)
    await mf.refresh_intraday(block_threshold=-0.01, resume_threshold=-0.005)
    assert mf.is_intraday_blocked("kospi") is True

    # -0.8% — resume_threshold -0.5% 미달
    rest2 = _make_rest_both(9920, 10000, 9920, 10000)
    mf._rest = rest2
    await mf.refresh_intraday(block_threshold=-0.01, resume_threshold=-0.005)
    assert mf.is_intraday_blocked("kospi") is True


# ── 쿨다운 ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cooldown_prevents_reblock():
    """해제 직후 cooldown_minutes 내에는 재차단 불가."""
    rest = _make_rest_both(9850, 10000, 9850, 10000)
    mf = MarketFilter(rest=rest)
    await mf.refresh_intraday(block_threshold=-0.01, resume_threshold=-0.005, cooldown_minutes=20)
    assert mf.is_intraday_blocked("kospi") is True

    # 회복 → 해제
    rest2 = _make_rest_both(9970, 10000, 9970, 10000)
    mf._rest = rest2
    await mf.refresh_intraday(block_threshold=-0.01, resume_threshold=-0.005, cooldown_minutes=20)
    assert mf.is_intraday_blocked("kospi") is False
    assert mf._kospi_last_unblock is not None

    # 쿨다운 내 재차단 시도 (-1.5%)
    rest3 = _make_rest_both(9850, 10000, 9850, 10000)
    mf._rest = rest3
    await mf.refresh_intraday(block_threshold=-0.01, resume_threshold=-0.005, cooldown_minutes=20)
    # 쿨다운(20분) 중이므로 재차단 불가
    assert mf.is_intraday_blocked("kospi") is False


@pytest.mark.asyncio
async def test_reblock_after_cooldown_expires():
    """쿨다운 만료 후에는 재차단 가능."""
    rest = _make_rest_both(9850, 10000, 9850, 10000)
    mf = MarketFilter(rest=rest)
    await mf.refresh_intraday(block_threshold=-0.01, resume_threshold=-0.005, cooldown_minutes=20)

    # 회복 → 해제, last_unblock을 30분 전으로 조작
    mf._kospi_intraday_blocked = False
    mf._kosdaq_intraday_blocked = False
    mf._kospi_last_unblock = datetime.now() - timedelta(minutes=25)
    mf._kosdaq_last_unblock = datetime.now() - timedelta(minutes=25)

    # 재차단 시도 (-1.5%)
    rest2 = _make_rest_both(9850, 10000, 9850, 10000)
    mf._rest = rest2
    await mf.refresh_intraday(block_threshold=-0.01, resume_threshold=-0.005, cooldown_minutes=20)
    # 쿨다운 만료 → 재차단
    assert mf.is_intraday_blocked("kospi") is True


# ── MA5 필터와 독립 ───────────────────────────────────────────────────────────

def test_intraday_independent_of_ma5():
    """MA5 강세 상태에서도 장중 차단 독립 적용."""
    mf = MarketFilter(rest=None)
    mf._kospi_strong = True  # MA5 강세
    mf._kospi_intraday_blocked = True  # 장중 차단

    assert mf.is_allowed("kospi") is True          # MA5 강세 허용
    assert mf.is_intraday_blocked("kospi") is True  # 장중은 차단


def test_ma5_weak_intraday_not_blocked():
    """MA5 약세이고 장중 차단 없을 때 독립 판단."""
    mf = MarketFilter(rest=None)
    mf._kospi_strong = False  # MA5 약세
    mf._kospi_intraday_blocked = False  # 장중 허용

    assert mf.is_allowed("kospi") is False           # MA5 차단
    assert mf.is_intraday_blocked("kospi") is False   # 장중은 허용


# ── open_prc 없을 때 fallback ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fallback_to_prev_close_when_no_open_prc():
    """open_prc 없을 때 전일 종가 fallback으로 등락률 계산."""
    today = datetime.now().strftime("%Y%m%d")
    # prev_close = 10000, cur = 9800 → -2% 차단
    rest = AsyncMock()
    rest.get_index_daily.return_value = {
        "inds_dt_pole_qry": [
            {"dt": today, "cur_prc": "9800"},   # open_prc 없음
            {"dt": "20260101", "cur_prc": "10000"},  # 전일 종가 (fallback)
        ] + [{"cur_prc": "10000"}] * 5
    }
    mf = MarketFilter(rest=rest)
    await mf.refresh_intraday(block_threshold=-0.01, resume_threshold=-0.005)
    assert mf.is_intraday_blocked("kospi") is True


# ── 당일 데이터 없을 때 상태 유지 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_data_preserves_state():
    """오늘 데이터 없으면 현재 차단 상태 유지."""
    rest = AsyncMock()
    rest.get_index_daily.return_value = {
        "inds_dt_pole_qry": [
            {"dt": "20200101", "cur_prc": "9800"},  # 오래된 날짜
        ]
    }
    mf = MarketFilter(rest=rest)
    mf._kospi_intraday_blocked = True  # 이미 차단 상태
    await mf.refresh_intraday(block_threshold=-0.01, resume_threshold=-0.005)
    # 상태 그대로 유지
    assert mf.is_intraday_blocked("kospi") is True


@pytest.mark.asyncio
async def test_intraday_change_property():
    """refresh_intraday 후 intraday_change 프로퍼티 값 확인."""
    rest = _make_rest_both(9850, 10000, 9960, 10000)
    mf = MarketFilter(rest=rest)
    await mf.refresh_intraday(block_threshold=-0.01, resume_threshold=-0.005)
    change = mf.intraday_change
    assert "001" in change
    assert "101" in change
    assert change["001"] == pytest.approx(-0.015, rel=1e-3)  # KOSPI -1.5%
    assert change["101"] == pytest.approx(-0.004, rel=1e-2)  # KOSDAQ -0.4%
