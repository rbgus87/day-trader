"""tests/test_market_filter.py — MarketFilter 단위 테스트."""

from datetime import datetime
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


def test_initial_state_is_strong():
    """첫 성공 전에는 강세 가정 (보수적 차단보다 진입 허용)."""
    mf = MarketFilter(rest=None)
    assert mf.kospi_strong is True
    assert mf.kosdaq_strong is True
    assert mf.last_update is None


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
    mf = MarketFilter(rest=rest, ma_length=5, retry_delay=0.0)

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
    mf = MarketFilter(rest=rest, ma_length=5, retry_delay=0.0)

    result = await mf._check_index(INDEX_KOSDAQ)
    assert result is False


@pytest.mark.asyncio
async def test_check_index_equal_to_ma_is_weak():
    """현재가 == MA인 경우 > 비교이므로 False."""
    rest = AsyncMock()
    rest.get_index_daily.return_value = _make_index_response(
        [100, 100, 100, 100, 100, 100]
    )
    mf = MarketFilter(rest=rest, ma_length=5, retry_delay=0.0)

    assert await mf._check_index(INDEX_KOSPI) is False


@pytest.mark.asyncio
async def test_check_index_insufficient_data_returns_none():
    """데이터 부족 시 재시도까지 실패하면 None (판정 불가)."""
    rest = AsyncMock()
    rest.get_index_daily.return_value = _make_index_response([110, 100])
    mf = MarketFilter(rest=rest, ma_length=5, retry_delay=0.0)

    assert await mf._check_index(INDEX_KOSPI) is None
    # 재시도 1회 포함 총 2회 호출
    assert rest.get_index_daily.call_count == 2


@pytest.mark.asyncio
async def test_check_index_retry_succeeds_after_empty():
    """0건 응답 후 재시도에서 정상 데이터 받으면 판정 성공."""
    rest = AsyncMock()
    rest.get_index_daily.side_effect = [
        {"inds_dt_pole_qry": []},  # 첫 호출: 빈 응답
        _make_index_response([110, 100, 100, 100, 100, 100]),  # 재시도 성공
    ]
    mf = MarketFilter(rest=rest, ma_length=5, retry_delay=0.0)

    assert await mf._check_index(INDEX_KOSPI) is True
    assert rest.get_index_daily.call_count == 2


@pytest.mark.asyncio
async def test_check_index_fallback_key():
    """응답 컨테이너 키가 output 등 다른 이름이어도 처리."""
    rest = AsyncMock()
    rest.get_index_daily.return_value = _make_index_response(
        [110, 100, 100, 100, 100, 100], key="output"
    )
    mf = MarketFilter(rest=rest, ma_length=5, retry_delay=0.0)

    assert await mf._check_index(INDEX_KOSPI) is True


@pytest.mark.asyncio
async def test_check_index_zero_price_returns_none():
    """가격 0 포함 시 None (이상치 → 이전 상태 유지)."""
    rest = AsyncMock()
    rest.get_index_daily.return_value = _make_index_response(
        [110, 0, 100, 100, 100, 100]
    )
    mf = MarketFilter(rest=rest, ma_length=5, retry_delay=0.0)

    assert await mf._check_index(INDEX_KOSPI) is None


def _patch_market_filter_now(monkeypatch, fake_now: datetime) -> None:
    """core.market_filter의 datetime.now()를 fake_now로 고정."""
    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now if tz is None else fake_now.replace(tzinfo=tz)
    monkeypatch.setattr("core.market_filter.datetime", _FakeDT)


@pytest.mark.asyncio
async def test_check_index_before_market_open_skips_today_row(monkeypatch):
    """09:00 이전: items[0].dt가 오늘이면 미완성 호가로 보고 스킵."""
    _patch_market_filter_now(monkeypatch, datetime(2026, 5, 4, 8, 30))
    rest = AsyncMock()
    # items[0] = 오늘 미완성 호가 50 (스킵), items[1] = 110 (current로 사용),
    # items[2..6] = 100×5 → MA5 = 100, 110 > 100 → 강세
    rest.get_index_daily.return_value = {
        "inds_dt_pole_qry": [
            {"dt": "20260504", "cur_prc": "50"},
            {"dt": "20260430", "cur_prc": "110"},
            {"dt": "20260429", "cur_prc": "100"},
            {"dt": "20260428", "cur_prc": "100"},
            {"dt": "20260427", "cur_prc": "100"},
            {"dt": "20260424", "cur_prc": "100"},
            {"dt": "20260423", "cur_prc": "100"},
        ]
    }
    mf = MarketFilter(rest=rest, ma_length=5, retry_delay=0.0)

    assert await mf._check_index(INDEX_KOSPI) is True
    cur, ma = mf._index_metrics[INDEX_KOSPI]
    assert cur == 110
    assert ma == 100


@pytest.mark.asyncio
async def test_check_index_after_market_open_uses_today_row(monkeypatch):
    """09:00 이후: items[0].dt가 오늘이면 장중 현재가로 보고 그대로 사용."""
    _patch_market_filter_now(monkeypatch, datetime(2026, 5, 4, 10, 30))
    rest = AsyncMock()
    # items[0] = 오늘 장중가 150 (사용), items[1..5] = 100×5 → MA5 = 100, 150 > 100 → 강세
    rest.get_index_daily.return_value = {
        "inds_dt_pole_qry": [
            {"dt": "20260504", "cur_prc": "150"},
            {"dt": "20260430", "cur_prc": "100"},
            {"dt": "20260429", "cur_prc": "100"},
            {"dt": "20260428", "cur_prc": "100"},
            {"dt": "20260427", "cur_prc": "100"},
            {"dt": "20260424", "cur_prc": "100"},
        ]
    }
    mf = MarketFilter(rest=rest, ma_length=5, retry_delay=0.0)

    assert await mf._check_index(INDEX_KOSPI) is True
    cur, ma = mf._index_metrics[INDEX_KOSPI]
    assert cur == 150
    assert ma == 100


@pytest.mark.asyncio
async def test_check_index_at_9am_boundary_uses_today_row(monkeypatch):
    """09:00 정각은 '이후'로 간주 (start = 0)."""
    _patch_market_filter_now(monkeypatch, datetime(2026, 5, 4, 9, 0))
    rest = AsyncMock()
    rest.get_index_daily.return_value = {
        "inds_dt_pole_qry": [
            {"dt": "20260504", "cur_prc": "150"},
            {"dt": "20260430", "cur_prc": "100"},
            {"dt": "20260429", "cur_prc": "100"},
            {"dt": "20260428", "cur_prc": "100"},
            {"dt": "20260427", "cur_prc": "100"},
            {"dt": "20260424", "cur_prc": "100"},
        ]
    }
    mf = MarketFilter(rest=rest, ma_length=5, retry_delay=0.0)

    assert await mf._check_index(INDEX_KOSPI) is True
    cur, _ = mf._index_metrics[INDEX_KOSPI]
    assert cur == 150  # 오늘 행을 사용


@pytest.mark.asyncio
async def test_check_index_does_not_skip_when_today_row_absent():
    """items[0].dt가 오늘이 아니면 스킵하지 않고 기존대로 동작."""
    rest = AsyncMock()
    rest.get_index_daily.return_value = {
        "inds_dt_pole_qry": [
            {"dt": "20260430", "cur_prc": "110"},
            {"dt": "20260429", "cur_prc": "100"},
            {"dt": "20260428", "cur_prc": "100"},
            {"dt": "20260427", "cur_prc": "100"},
            {"dt": "20260424", "cur_prc": "100"},
            {"dt": "20260423", "cur_prc": "100"},
        ]
    }
    mf = MarketFilter(rest=rest, ma_length=5, retry_delay=0.0)

    assert await mf._check_index(INDEX_KOSPI) is True
    cur, ma = mf._index_metrics[INDEX_KOSPI]
    assert cur == 110
    assert ma == 100


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

    mf = MarketFilter(rest=rest, ma_length=5, retry_delay=0.0)
    await mf.refresh()

    assert mf.kospi_strong is True
    assert mf.kosdaq_strong is False
    assert mf.last_update is not None


@pytest.mark.asyncio
async def test_refresh_keeps_previous_when_data_insufficient():
    """데이터 부족 시 이전 캐시 상태를 그대로 유지."""
    rest = AsyncMock()
    rest.get_index_daily.return_value = {"inds_dt_pole_qry": []}

    mf = MarketFilter(rest=rest, ma_length=5, retry_delay=0.0)
    # 이전 사이클에서 코스피 약세, 코스닥 강세였다고 가정
    mf._kospi_strong = False
    mf._kosdaq_strong = True

    await mf.refresh()

    # 데이터 부족이지만 이전 상태가 유지되어야 함
    assert mf.kospi_strong is False
    assert mf.kosdaq_strong is True


@pytest.mark.asyncio
async def test_refresh_keeps_previous_on_exception():
    """API 예외 발생 시도 이전 캐시 상태를 유지."""
    rest = AsyncMock()
    rest.get_index_daily.side_effect = Exception("network error")

    mf = MarketFilter(rest=rest, ma_length=5, retry_delay=0.0)
    mf._kospi_strong = True
    mf._kosdaq_strong = False

    await mf.refresh()

    assert mf.kospi_strong is True
    assert mf.kosdaq_strong is False
    assert mf.last_update is not None


@pytest.mark.asyncio
async def test_refresh_partial_update_only_failed_kept():
    """한쪽만 실패하면 그쪽만 이전값 유지, 성공한 쪽은 갱신."""
    rest = AsyncMock()

    def side_effect(code, base_dt=""):
        if code == INDEX_KOSPI:
            return _make_index_response([110, 100, 100, 100, 100, 100])
        # 코스닥은 빈 응답
        return {"inds_dt_pole_qry": []}

    rest.get_index_daily.side_effect = side_effect

    mf = MarketFilter(rest=rest, ma_length=5, retry_delay=0.0)
    # 이전 코스닥 강세 가정
    mf._kospi_strong = False
    mf._kosdaq_strong = True

    await mf.refresh()

    assert mf.kospi_strong is True   # 갱신됨
    assert mf.kosdaq_strong is True  # 빈 응답 → 이전값 유지
