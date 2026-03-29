"""tests/test_batch_collector.py — BatchCollector 단위 테스트."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from backtest.batch_collector import BatchCollector


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_data_collector():
    collector = MagicMock()
    collector.collect_minute_candles = AsyncMock(return_value=400)
    return collector


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.fetch_all = AsyncMock(return_value=[])
    return db


@pytest.fixture
def batch(mock_data_collector, mock_db):
    return BatchCollector(collector=mock_data_collector, db=mock_db)


# ---------------------------------------------------------------------------
# collect_tickers 테스트
# ---------------------------------------------------------------------------

class TestCollectTickers:
    @pytest.mark.asyncio
    async def test_collects_multiple_tickers(self, batch, mock_data_collector):
        """여러 종목을 순차 수집한다."""
        tickers = ["005930", "000660", "035420"]
        results = await batch.collect_tickers(tickers, days=30)

        assert len(results) == 3
        assert mock_data_collector.collect_minute_candles.call_count == 3
        for ticker in tickers:
            assert results[ticker] == 400

    @pytest.mark.asyncio
    async def test_handles_single_failure(self, batch, mock_data_collector):
        """단일 종목 실패 시 다른 종목은 정상 수집된다."""
        call_count = 0

        async def side_effect(ticker, days=30):
            nonlocal call_count
            call_count += 1
            if ticker == "000660":
                raise Exception("API error")
            return 400

        mock_data_collector.collect_minute_candles = AsyncMock(side_effect=side_effect)

        results = await batch.collect_tickers(["005930", "000660", "035420"], days=30)

        assert results["005930"] == 400
        assert results["000660"] == 0  # 실패
        assert results["035420"] == 400

    @pytest.mark.asyncio
    async def test_empty_tickers(self, batch):
        """빈 종목 리스트에 대해 빈 결과를 반환한다."""
        results = await batch.collect_tickers([], days=30)
        assert results == {}


# ---------------------------------------------------------------------------
# collect_from_screener 테스트
# ---------------------------------------------------------------------------

class TestCollectFromScreener:
    @pytest.mark.asyncio
    async def test_collects_screened_tickers(self, batch, mock_db, mock_data_collector):
        """스크리너 결과의 selected=1 종목을 수집한다."""
        mock_db.fetch_all = AsyncMock(return_value=[
            {"ticker": "005930"},
            {"ticker": "000660"},
        ])

        results = await batch.collect_from_screener("2026-03-23", days=30)

        assert len(results) == 2
        mock_data_collector.collect_minute_candles.call_count == 2
        mock_db.fetch_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_empty_for_no_screener_results(self, batch, mock_db):
        """스크리너 결과가 없으면 빈 dict를 반환한다."""
        mock_db.fetch_all = AsyncMock(return_value=[])

        results = await batch.collect_from_screener("2026-03-23")
        assert results == {}


# ---------------------------------------------------------------------------
# collect_universe 테스트
# ---------------------------------------------------------------------------

class TestCollectUniverse:
    @pytest.mark.asyncio
    async def test_loads_and_collects_universe(self, batch, mock_data_collector, tmp_path):
        """universe.yaml의 종목을 로드하여 수집한다."""
        universe_path = tmp_path / "universe.yaml"
        universe_path.write_text(
            yaml.dump({"stocks": [
                {"ticker": "005930", "name": "삼성전자"},
                {"ticker": "000660", "name": "SK하이닉스"},
            ]}),
            encoding="utf-8",
        )

        with patch.object(BatchCollector, "_load_universe_tickers", return_value=["005930", "000660"]):
            results = await batch.collect_universe(days=30)

        assert len(results) == 2
        assert mock_data_collector.collect_minute_candles.call_count == 2


# ---------------------------------------------------------------------------
# _load_universe_tickers 테스트
# ---------------------------------------------------------------------------

class TestLoadUniverseTickers:
    def test_loads_tickers_from_yaml(self, tmp_path):
        """universe.yaml에서 티커 코드만 추출한다."""
        universe_path = tmp_path / "universe.yaml"
        universe_path.write_text(
            yaml.dump({"stocks": [
                {"ticker": "005930", "name": "삼성전자"},
                {"ticker": "000660", "name": "SK하이닉스"},
            ]}),
            encoding="utf-8",
        )

        with patch("backtest.batch_collector._DEFAULT_UNIVERSE_PATH", universe_path):
            tickers = BatchCollector._load_universe_tickers()

        assert tickers == ["005930", "000660"]
