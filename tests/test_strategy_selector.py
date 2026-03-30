"""tests/test_strategy_selector.py — StrategySelector 단위 테스트 (3전략 체제)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from screener.strategy_selector import StrategySelector


@pytest.fixture()
def selector():
    config = MagicMock()
    config.selector = {}
    config.force_strategy = ""
    rest_client = MagicMock()
    return StrategySelector(config=config, rest_client=rest_client)


# ---------------------------------------------------------------------------
# Momentum 선택
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_selects_momentum(selector):
    """섹터 ETF >= 2.0% → Momentum."""
    market_data = {
        "sector_etf_change_pct": 2.5,
        "candidate_ticker": "005930",
    }
    strategy, ticker = await selector.select(market_data)
    assert strategy == "momentum"


@pytest.mark.asyncio
async def test_momentum_threshold_exact(selector):
    """섹터 ETF 정확히 2.0% → Momentum."""
    market_data = {
        "sector_etf_change_pct": 2.0,
        "candidate_ticker": "005930",
    }
    strategy, _ = await selector.select(market_data)
    assert strategy == "momentum"


@pytest.mark.asyncio
async def test_momentum_below_threshold(selector):
    """섹터 ETF 1.9% → Momentum 아님."""
    market_data = {
        "sector_etf_change_pct": 1.9,
        "candidate_ticker": "005930",
    }
    strategy, _ = await selector.select(market_data)
    assert strategy != "momentum"


# ---------------------------------------------------------------------------
# Flow 선택 (Momentum 미충족 시 폴백)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_selects_flow(selector):
    """Momentum 미충족 + 후보 종목 → Flow."""
    market_data = {
        "sector_etf_change_pct": 0.5,
        "candidate_ticker": "042700",
    }
    strategy, ticker = await selector.select(market_data)
    assert strategy == "flow"
    assert ticker == "042700"


# ---------------------------------------------------------------------------
# Pullback 선택 (ATR 기반)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pullback_with_high_atr(selector):
    """ATR >= 3% + 후보 종목 → Flow 우선 (Pullback보다 우선순위 높음)."""
    market_data = {
        "sector_etf_change_pct": 0.0,
        "candidate_ticker": "196170",
        "atr_pct": 0.04,
    }
    strategy, _ = await selector.select(market_data)
    # Flow가 Pullback보다 우선
    assert strategy == "flow"


@pytest.mark.asyncio
async def test_pullback_blocked_by_low_atr(selector):
    """ATR < 3% → Pullback 제외 (Flow는 통과)."""
    market_data = {
        "sector_etf_change_pct": 0.0,
        "candidate_ticker": "005930",
        "atr_pct": 0.02,
    }
    strategy, _ = await selector.select(market_data)
    # Flow가 먼저 매치됨
    assert strategy == "flow"


# ---------------------------------------------------------------------------
# None 선택
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_selects_none(selector):
    """후보 종목 없음 → (None, None)."""
    market_data = {
        "sector_etf_change_pct": 0.0,
        "candidate_ticker": None,
    }
    strategy, ticker = await selector.select(market_data)
    assert strategy is None
    assert ticker is None


# ---------------------------------------------------------------------------
# collect_market_data
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_collect_market_data():
    config = MagicMock()
    config.selector = {}
    config.force_strategy = ""
    rest_client = MagicMock()
    rest_client.get_market_snapshot = AsyncMock(return_value={
        "kospi_gap_pct": 0.7,
        "sector_etf_change_pct": 1.8,
        "top_sector": "반도체",
        "index_range_pct": 0.3,
    })
    sel = StrategySelector(config=config, rest_client=rest_client)
    data = await sel.collect_market_data(candidate_ticker="005930")
    assert data["candidate_ticker"] == "005930"
    rest_client.get_market_snapshot.assert_awaited_once()


@pytest.mark.asyncio
async def test_select_auto_collects():
    config = MagicMock()
    config.selector = {}
    config.force_strategy = ""
    rest_client = MagicMock()
    rest_client.get_market_snapshot = AsyncMock(return_value={
        "kospi_gap_pct": 0.0,
        "sector_etf_change_pct": 2.5,
        "top_sector": "",
        "index_range_pct": 1.0,
    })
    sel = StrategySelector(config=config, rest_client=rest_client)
    strategy, ticker = await sel.select(candidate_ticker="005930")
    assert strategy == "momentum"
    assert ticker == "005930"


# ---------------------------------------------------------------------------
# Config 임계값 오버라이드
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_config_threshold_override():
    config = MagicMock()
    config.selector = {"momentum_etf_threshold": 3.0}
    config.force_strategy = ""
    rest_client = MagicMock()
    sel = StrategySelector(config=config, rest_client=rest_client)

    market_data = {
        "sector_etf_change_pct": 2.5,
        "candidate_ticker": "005930",
    }
    strategy, _ = await sel.select(market_data)
    # 2.5 < 3.0이므로 Momentum 아님 → Flow
    assert strategy == "flow"


# ---------------------------------------------------------------------------
# force_strategy 설정
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_force_strategy_flow():
    """force_strategy='flow' → 항상 flow 반환."""
    config = MagicMock()
    config.selector = {}
    config.force_strategy = "flow"
    rest_client = MagicMock()
    sel = StrategySelector(config=config, rest_client=rest_client)

    market_data = {
        "sector_etf_change_pct": 3.0,  # Momentum 조건 충족하지만 무시
        "candidate_ticker": "005930",
    }
    strategy, ticker = await sel.select(market_data)
    assert strategy == "flow"
    assert ticker == "005930"


@pytest.mark.asyncio
async def test_force_strategy_empty_uses_selector():
    """force_strategy='' → 기존 selector 로직 동작."""
    config = MagicMock()
    config.selector = {}
    config.force_strategy = ""
    rest_client = MagicMock()
    sel = StrategySelector(config=config, rest_client=rest_client)

    market_data = {
        "sector_etf_change_pct": 3.0,
        "candidate_ticker": "005930",
    }
    strategy, _ = await sel.select(market_data)
    assert strategy == "momentum"
