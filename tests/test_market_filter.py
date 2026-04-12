"""tests/test_market_filter.py — MarketFilter 단위 테스트."""

from unittest.mock import AsyncMock

import pytest

from core.market_filter import MarketFilter, INDEX_KOSPI, INDEX_KOSDAQ


# ──────────────────────────────────────────────────────────────────────
# is_allowed 로직 (상태 수동 설정)
# ──────────────────────────────────────────────────────────────────────

def test_is_allowed_kospi_strong_kosdaq_weak():
    mf = MarketFilter(rest=None)
    mf._kospi_strong = True
    mf._kosdaq_strong = False

    assert mf.is_allowed("kospi") is True
    assert mf.is_allowed("kosdaq") is False
    # unknown → 하나라도 강세면 허용
    assert mf.is_allowed("unknown") is True


def test_is_allowed_kosdaq_strong_kospi_weak():
    mf = MarketFilter(rest=None)
    mf._kospi_strong = False
    mf._kosdaq_strong = True

    assert mf.is_allowed("kospi") is False
    assert mf.is_allowed("kosdaq") is True
    assert mf.is_allowed("unknown") is True


def test_is_allowed_both_weak():
    mf = MarketFilter(rest=None)
    mf._kospi_strong = False
    mf._kosdaq_strong = False

    assert mf.is_allowed("kospi") is False
    assert mf.is_allowed("kosdaq") is False
    assert mf.is_allowed("unknown") is False


def test_is_allowed_both_strong():
    mf = MarketFilter(rest=None)
    mf._kospi_strong = True
    mf._kosdaq_strong = True

    assert mf.is_allowed("kospi") is True
    assert mf.is_allowed("kosdaq") is True
    assert mf.is_allowed("unknown") is True


# ──────────────────────────────────────────────────────────────────────
# _check_index (MA 계산 로직)
# ──────────────────────────────────────────────────────────────────────

def _make_index_response(prices: list[float], key: str = "inds_dt_pole_qry") -> dict:
    """지수 일봉 응답 mock 생성. prices[0]이 최신."""
    return {key: [{"cur_prc": str(p)} for p in prices]}


@pytest.mark.asyncio
async def test_check_index_strong_above_ma():
    """현재가가 MA보다 높으면 True."""
    rest = AsyncMock()
    # MA5 = (100+100+100+100+100)/5 = 100, current=110 → True
    rest.get_index_daily.return_value = _make_index_response(
        [110, 100, 100, 100, 100, 100]
    )
    mf = MarketFilter(rest=rest, ma_length=5)

    result = await mf._check_index(INDEX_KOSPI)
    assert result is True


@pytest.mark.asyncio
async def test_check_index_weak_below_ma():
    """현재가가 MA보다 낮으면 False."""
    rest = AsyncMock()
    # MA5 = 100, current=95 → False
    rest.get_index_daily.return_value = _make_index_response(
        [95, 100, 100, 100, 100, 100]
    )
    mf = MarketFilter(rest=rest, ma_length=5)

    result = await mf._check_index(INDEX_KOSDAQ)
    assert result is False


@pytest.mark.asyncio
async def test_check_index_equal_to_ma_is_weak():
    """현재가 == MA인 경우 > 비교이므로 False."""
    rest = AsyncMock()
    rest.get_index_daily.return_value = _make_index_response(
        [100, 100, 100, 100, 100, 100]
    )
    mf = MarketFilter(rest=rest, ma_length=5)

    assert await mf._check_index(INDEX_KOSPI) is False


@pytest.mark.asyncio
async def test_check_index_insufficient_data():
    """데이터 부족 시 False (보수적)."""
    rest = AsyncMock()
    rest.get_index_daily.return_value = _make_index_response([110, 100])
    mf = MarketFilter(rest=rest, ma_length=5)

    assert await mf._check_index(INDEX_KOSPI) is False


@pytest.mark.asyncio
async def test_check_index_fallback_key():
    """응답 컨테이너 키가 output 등 다른 이름이어도 처리."""
    rest = AsyncMock()
    rest.get_index_daily.return_value = _make_index_response(
        [110, 100, 100, 100, 100, 100], key="output"
    )
    mf = MarketFilter(rest=rest, ma_length=5)

    assert await mf._check_index(INDEX_KOSPI) is True


@pytest.mark.asyncio
async def test_check_index_zero_price_is_weak():
    """가격 0 포함 시 False (이상치)."""
    rest = AsyncMock()
    rest.get_index_daily.return_value = _make_index_response(
        [110, 0, 100, 100, 100, 100]
    )
    mf = MarketFilter(rest=rest, ma_length=5)

    assert await mf._check_index(INDEX_KOSPI) is False


# ──────────────────────────────────────────────────────────────────────
# refresh
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_updates_both_markets():
    rest = AsyncMock()

    # 코스피 강세, 코스닥 약세
    def side_effect(code, base_dt=""):
        if code == INDEX_KOSPI:
            return _make_index_response([110, 100, 100, 100, 100, 100])
        return _make_index_response([90, 100, 100, 100, 100, 100])

    rest.get_index_daily.side_effect = side_effect

    mf = MarketFilter(rest=rest, ma_length=5)
    await mf.refresh()

    assert mf.kospi_strong is True
    assert mf.kosdaq_strong is False
    assert mf.last_update is not None


@pytest.mark.asyncio
async def test_refresh_on_failure_is_weak():
    """API 실패 시 보수적으로 False."""
    rest = AsyncMock()
    rest.get_index_daily.side_effect = Exception("network error")

    mf = MarketFilter(rest=rest, ma_length=5)
    await mf.refresh()

    assert mf.kospi_strong is False
    assert mf.kosdaq_strong is False
    assert mf.last_update is not None
