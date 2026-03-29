"""tests/test_data_collector.py — DataCollector 단위 테스트."""

from unittest.mock import AsyncMock, MagicMock
import pytest

from backtest.data_collector import DataCollector, _parse_timestamp


# ---------------------------------------------------------------------------
# 공통 픽스처
# ---------------------------------------------------------------------------

def _make_candle(cntr_tm: str = "20260323090100") -> dict:
    """키움 REST API 실제 분봉 응답 형식."""
    return {
        "cntr_tm": cntr_tm,
        "open_pric": "-70000",
        "high_pric": "-70500",
        "low_pric": "-69500",
        "cur_prc": "-70200",
        "trde_qty": "1000",
        "acc_trde_qty": "5000000",
    }


def _api_response(candles: list[dict]) -> dict:
    return {"stk_min_pole_chart_qry": candles, "return_code": 0}


@pytest.fixture
def mock_rest():
    rest = MagicMock()
    rest.get_minute_ohlcv = AsyncMock()
    return rest


@pytest.fixture
def mock_db():
    db = MagicMock()
    # execute_safe가 호출될 때마다 1(rowid)을 반환 → 저장 성공으로 간주
    db.execute_safe = AsyncMock(return_value=1)
    return db


@pytest.fixture
def collector(mock_rest, mock_db):
    return DataCollector(rest_client=mock_rest, db=mock_db)


# ---------------------------------------------------------------------------
# 1. test_collect_saves_candles
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_collect_saves_candles(collector, mock_rest, mock_db):
    """API가 캔들 데이터를 반환하면 DB insert가 호출되어야 한다."""
    candles = [_make_candle(f"2026032309{i:02d}00") for i in range(3)]
    # PAGE_SIZE(900) 미만 → 1회 호출 후 종료
    mock_rest.get_minute_ohlcv.return_value = _api_response(candles)

    total = await collector.collect_minute_candles("005930", days=1)

    assert mock_rest.get_minute_ohlcv.call_count == 1
    assert mock_db.execute_safe.call_count == 3
    assert total == 3


# ---------------------------------------------------------------------------
# 2. test_handles_empty_response
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handles_empty_response(collector, mock_rest, mock_db):
    """API가 빈 캔들을 반환하면 저장 없이 0을 반환해야 한다."""
    mock_rest.get_minute_ohlcv.return_value = _api_response([])

    total = await collector.collect_minute_candles("005930", days=1)

    assert total == 0
    mock_db.execute_safe.assert_not_called()


# ---------------------------------------------------------------------------
# 3. test_parse_and_save_correct_format
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parse_and_save_correct_format(collector, mock_rest, mock_db):
    """_parse_and_save가 올바른 SQL 파라미터로 execute_safe를 호출해야 한다."""
    candle = _make_candle("20260323093000")
    candles = [candle]

    saved = await collector._parse_and_save("005930", candles)

    assert saved == 1
    mock_db.execute_safe.assert_called_once()
    _sql, params = mock_db.execute_safe.call_args[0]

    assert params[0] == "005930"                     # ticker
    assert params[1] == "1m"                         # tf
    assert params[2] == "2026-03-23 09:30:00"        # ts — YYYY-MM-DD HH:MM:SS
    assert params[3] == 70000.0                      # open (abs)
    assert params[4] == 70500.0                      # high (abs)
    assert params[5] == 69500.0                      # low (abs)
    assert params[6] == 70200.0                      # close (abs)
    assert params[7] == 1000                         # volume
    assert "INSERT OR IGNORE" in _sql
    assert "intraday_candles" in _sql


# ---------------------------------------------------------------------------
# 4. 보조: _parse_timestamp 단위 테스트
# ---------------------------------------------------------------------------

def test_parse_timestamp_14_digit():
    """14자리 YYYYMMDDHHmmss → YYYY-MM-DD HH:MM:SS."""
    assert _parse_timestamp("20260323090100") == "2026-03-23 09:01:00"
    assert _parse_timestamp("20260323153000") == "2026-03-23 15:30:00"


def test_parse_timestamp_6_digit():
    """6자리 HHmmss → HH:MM:SS (하위 호환)."""
    assert _parse_timestamp("090100") == "09:01:00"
    assert _parse_timestamp("153000") == "15:30:00"


def test_parse_timestamp_invalid():
    assert _parse_timestamp("") is None
    assert _parse_timestamp("123") is None
    assert _parse_timestamp(None) is None


# ---------------------------------------------------------------------------
# 5. execute_safe 실패(None 반환) 시 카운트에서 제외
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_candle_not_counted(collector, mock_rest, mock_db):
    """execute_safe가 None을 반환(중복)하면 저장 카운트에서 제외해야 한다."""
    mock_db.execute_safe.return_value = None  # 중복 → IGNORE
    candles = [_make_candle("20260323090100"), _make_candle("20260323090200")]
    mock_rest.get_minute_ohlcv.return_value = _api_response(candles)

    total = await collector.collect_minute_candles("005930", days=1)

    assert total == 0
    assert mock_db.execute_safe.call_count == 2
