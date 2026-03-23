"""tests/test_strategy_selector.py — StrategySelector 단위 테스트."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from screener.strategy_selector import StrategySelector


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def selector():
    """환경 변수 없이도 동작하는 모의 의존성 주입 선택기."""
    config = MagicMock()
    rest_client = MagicMock()
    return StrategySelector(config=config, rest_client=rest_client)


# ---------------------------------------------------------------------------
# 기본 전략 선택 테스트
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_selects_orb_on_gap(selector):
    """KOSPI 갭 +0.8% → ORB 전략 선택."""
    market_data = {
        "kospi_gap_pct": 0.8,
        "sector_etf_change_pct": 0.0,
        "index_range_pct": 1.0,
        "candidate_ticker": None,
    }
    strategy, ticker = await selector.select(market_data)
    assert strategy == "orb"


@pytest.mark.asyncio
async def test_selects_momentum(selector):
    """갭 없음, 섹터 ETF +2% → 모멘텀 전략 선택."""
    market_data = {
        "kospi_gap_pct": 0.0,
        "sector_etf_change_pct": 2.0,
        "index_range_pct": 1.0,
        "candidate_ticker": None,
    }
    strategy, ticker = await selector.select(market_data)
    assert strategy == "momentum"


@pytest.mark.asyncio
async def test_selects_vwap(selector):
    """평탄한 시장, 변동폭 0.3% → VWAP 전략 선택."""
    market_data = {
        "kospi_gap_pct": 0.0,
        "sector_etf_change_pct": 0.0,
        "index_range_pct": 0.3,
        "candidate_ticker": None,
    }
    strategy, ticker = await selector.select(market_data)
    assert strategy == "vwap"


@pytest.mark.asyncio
async def test_selects_none(selector):
    """모든 조건 미충족 → (None, None) 반환."""
    market_data = {
        "kospi_gap_pct": 0.0,
        "sector_etf_change_pct": 0.0,
        "index_range_pct": 1.0,       # 변동폭 크고
        "candidate_ticker": None,     # 후보 종목도 없음
    }
    strategy, ticker = await selector.select(market_data)
    assert strategy is None
    assert ticker is None


@pytest.mark.asyncio
async def test_priority_orb_over_momentum(selector):
    """ORB + 모멘텀 조건 동시 충족 → ORB 우선 선택."""
    market_data = {
        "kospi_gap_pct": 0.6,         # ORB 조건 충족
        "sector_etf_change_pct": 2.0, # 모멘텀 조건도 충족
        "index_range_pct": 1.0,
        "candidate_ticker": None,
    }
    strategy, ticker = await selector.select(market_data)
    assert strategy == "orb"


# ---------------------------------------------------------------------------
# 경계값 테스트
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orb_threshold_exact(selector):
    """KOSPI 갭 정확히 0.5% → ORB 선택 (경계값 포함)."""
    market_data = {
        "kospi_gap_pct": 0.5,
        "sector_etf_change_pct": 0.0,
        "index_range_pct": 1.0,
        "candidate_ticker": None,
    }
    strategy, _ = await selector.select(market_data)
    assert strategy == "orb"


@pytest.mark.asyncio
async def test_orb_below_threshold(selector):
    """KOSPI 갭 0.49% → ORB 선택 안 됨."""
    market_data = {
        "kospi_gap_pct": 0.49,
        "sector_etf_change_pct": 0.0,
        "index_range_pct": 1.0,
        "candidate_ticker": None,
    }
    strategy, _ = await selector.select(market_data)
    assert strategy != "orb"


@pytest.mark.asyncio
async def test_pullback_with_candidate(selector):
    """후보 종목 존재 시 눌림목 전략 선택 (폴백)."""
    market_data = {
        "kospi_gap_pct": 0.0,
        "sector_etf_change_pct": 0.0,
        "index_range_pct": 1.0,
        "candidate_ticker": "005930",
    }
    strategy, ticker = await selector.select(market_data)
    assert strategy == "pullback"
    assert ticker == "005930"


@pytest.mark.asyncio
async def test_vwap_exact_threshold(selector):
    """지수 변동폭 정확히 0.5% → VWAP 선택 (경계값 포함)."""
    market_data = {
        "kospi_gap_pct": 0.0,
        "sector_etf_change_pct": 0.0,
        "index_range_pct": 0.5,
        "candidate_ticker": None,
    }
    strategy, _ = await selector.select(market_data)
    assert strategy == "vwap"
