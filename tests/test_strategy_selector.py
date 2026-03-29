"""tests/test_strategy_selector.py — StrategySelector 단위 테스트."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from screener.strategy_selector import StrategySelector


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def selector():
    """환경 변수 없이도 동작하는 모의 의존성 주입 선택기."""
    config = MagicMock()
    config.selector = {}  # 기본 임계값 사용
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
    """갭 없음, 섹터 ETF +2.5% → 모멘텀 전략 선택."""
    market_data = {
        "kospi_gap_pct": 0.0,
        "sector_etf_change_pct": 2.5,
        "index_range_pct": 1.5,
        "candidate_ticker": None,
    }
    strategy, ticker = await selector.select(market_data)
    assert strategy == "momentum"


@pytest.mark.asyncio
async def test_selects_vwap(selector):
    """평탄한 시장, 변동폭 0.5% → VWAP 전략 선택."""
    market_data = {
        "kospi_gap_pct": 0.0,
        "sector_etf_change_pct": 0.0,
        "index_range_pct": 0.5,
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
        "index_range_pct": 1.5,       # 변동폭 크고 (VWAP 임계값 0.8 초과)
        "candidate_ticker": None,     # 후보 종목도 없음
    }
    strategy, ticker = await selector.select(market_data)
    assert strategy is None
    assert ticker is None


@pytest.mark.asyncio
async def test_priority_orb_over_momentum(selector):
    """ORB + 모멘텀 조건 동시 충족 → ORB 우선 선택."""
    market_data = {
        "kospi_gap_pct": 1.0,         # ORB 조건 충족 (임계값 0.8)
        "sector_etf_change_pct": 2.5, # 모멘텀 조건도 충족 (임계값 2.0)
        "index_range_pct": 1.5,
        "candidate_ticker": None,
    }
    strategy, ticker = await selector.select(market_data)
    assert strategy == "orb"


# ---------------------------------------------------------------------------
# 경계값 테스트
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orb_threshold_exact(selector):
    """KOSPI 갭 정확히 0.8% → ORB 선택 (경계값 포함)."""
    market_data = {
        "kospi_gap_pct": 0.8,
        "sector_etf_change_pct": 0.0,
        "index_range_pct": 1.5,
        "candidate_ticker": None,
    }
    strategy, _ = await selector.select(market_data)
    assert strategy == "orb"


@pytest.mark.asyncio
async def test_orb_below_threshold(selector):
    """KOSPI 갭 0.79% → ORB 선택 안 됨."""
    market_data = {
        "kospi_gap_pct": 0.79,
        "sector_etf_change_pct": 0.0,
        "index_range_pct": 1.5,
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
        "index_range_pct": 1.5,
        "candidate_ticker": "005930",
    }
    strategy, ticker = await selector.select(market_data)
    assert strategy == "pullback"
    assert ticker == "005930"


@pytest.mark.asyncio
async def test_vwap_exact_threshold(selector):
    """지수 변동폭 정확히 0.8% → VWAP 선택 (경계값 포함)."""
    market_data = {
        "kospi_gap_pct": 0.0,
        "sector_etf_change_pct": 0.0,
        "index_range_pct": 0.8,
        "candidate_ticker": None,
    }
    strategy, _ = await selector.select(market_data)
    assert strategy == "vwap"


# ---------------------------------------------------------------------------
# collect_market_data 테스트
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_collect_market_data():
    """REST API 호출로 시장 데이터를 수집한다."""
    config = MagicMock()
    config.selector = {}
    rest_client = MagicMock()
    rest_client.get_market_snapshot = AsyncMock(return_value={
        "kospi_gap_pct": 0.7,
        "sector_etf_change_pct": 1.8,
        "top_sector": "반도체",
        "index_range_pct": 0.3,
    })

    sel = StrategySelector(config=config, rest_client=rest_client)
    data = await sel.collect_market_data(candidate_ticker="005930")

    assert data["kospi_gap_pct"] == 0.7
    assert data["candidate_ticker"] == "005930"
    assert data["top_sector"] == "반도체"
    rest_client.get_market_snapshot.assert_awaited_once()


@pytest.mark.asyncio
async def test_select_auto_collects_when_no_market_data():
    """market_data=None이면 REST API로 자동 수집 후 전략 선택."""
    config = MagicMock()
    config.selector = {}
    rest_client = MagicMock()
    rest_client.get_market_snapshot = AsyncMock(return_value={
        "kospi_gap_pct": 0.8,
        "sector_etf_change_pct": 0.0,
        "top_sector": "",
        "index_range_pct": 1.0,
    })

    sel = StrategySelector(config=config, rest_client=rest_client)
    strategy, ticker = await sel.select(candidate_ticker="005930")

    assert strategy == "orb"
    assert ticker == "005930"


# ---------------------------------------------------------------------------
# config 임계값 오버라이드 테스트
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_config_threshold_override():
    """config.yaml에서 임계값을 오버라이드하면 적용된다."""
    config = MagicMock()
    config.selector = {
        "orb_gap_threshold": 1.0,   # 기본 0.5 → 1.0으로 상향
    }
    rest_client = MagicMock()
    sel = StrategySelector(config=config, rest_client=rest_client)

    # 갭 0.8%는 기본 임계값(0.5)에선 ORB이지만, 1.0으로 상향했으므로 ORB 아님
    market_data = {
        "kospi_gap_pct": 0.8,
        "sector_etf_change_pct": 0.0,
        "index_range_pct": 1.0,
        "candidate_ticker": None,
    }
    strategy, _ = await sel.select(market_data)
    assert strategy != "orb"


# ---------------------------------------------------------------------------
# ATR 기반 Pullback 게이팅 테스트
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pullback_blocked_by_low_atr(selector):
    """ATR < 3%인 종목은 Pullback 선택 안 됨."""
    market_data = {
        "kospi_gap_pct": 0.0,
        "sector_etf_change_pct": 0.0,
        "index_range_pct": 1.5,
        "candidate_ticker": "005930",
        "atr_pct": 0.02,  # 2% < 3%
    }
    strategy, ticker = await selector.select(market_data)
    assert strategy is None


@pytest.mark.asyncio
async def test_pullback_allowed_by_high_atr(selector):
    """ATR >= 3%인 종목은 Pullback 선택됨."""
    market_data = {
        "kospi_gap_pct": 0.0,
        "sector_etf_change_pct": 0.0,
        "index_range_pct": 1.5,
        "candidate_ticker": "196170",
        "atr_pct": 0.04,  # 4% >= 3%
    }
    strategy, ticker = await selector.select(market_data)
    assert strategy == "pullback"
    assert ticker == "196170"
