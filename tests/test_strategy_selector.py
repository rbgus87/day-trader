"""tests/test_strategy_selector.py — StrategySelector 단위 테스트 (momentum 단일 체제).

2026-04-14: flow/pullback/gap/open_break/big_candle 전략을 strategy/archive/로 이동.
StrategySelector는 이제 "항상 momentum" 반환. 본 테스트는 그 단일 계약 검증.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from screener.strategy_selector import StrategySelector


def _make_selector(force: str = "", threshold_override: dict | None = None) -> StrategySelector:
    config = MagicMock()
    config.selector = threshold_override or {}
    config.force_strategy = force
    rest_client = MagicMock()
    return StrategySelector(config=config, rest_client=rest_client)


@pytest.mark.asyncio
async def test_selects_momentum_by_default():
    """섹터 ETF 값과 무관하게 momentum 반환 (단일 전략 체제)."""
    sel = _make_selector()
    strategy, ticker = await sel.select({
        "sector_etf_change_pct": 2.5,
        "candidate_ticker": "005930",
    })
    assert strategy == "momentum"
    assert ticker == "005930"


@pytest.mark.asyncio
async def test_selects_momentum_even_with_low_etf():
    """섹터 ETF가 낮아도 momentum 반환 (archive 전략들 폴백 경로 제거됨)."""
    sel = _make_selector()
    strategy, ticker = await sel.select({
        "sector_etf_change_pct": 0.1,
        "candidate_ticker": "042700",
    })
    assert strategy == "momentum"
    assert ticker == "042700"


@pytest.mark.asyncio
async def test_force_strategy_non_momentum_ignored():
    """force_strategy='flow' 같은 archive 전략 요청은 무시하고 momentum 반환."""
    sel = _make_selector(force="flow")
    strategy, _ = await sel.select({
        "sector_etf_change_pct": 0.0,
        "candidate_ticker": "005930",
    })
    assert strategy == "momentum"


@pytest.mark.asyncio
async def test_force_strategy_empty_still_momentum():
    """force_strategy 비었어도 momentum 반환."""
    sel = _make_selector(force="")
    strategy, _ = await sel.select({
        "sector_etf_change_pct": 0.0,
        "candidate_ticker": "005930",
    })
    assert strategy == "momentum"


@pytest.mark.asyncio
async def test_collect_market_data():
    """REST 스냅샷을 market_data dict로 변환하고 candidate_ticker 주입."""
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
async def test_select_auto_collects_market_data_if_missing():
    """market_data 인자가 None이면 collect_market_data 자동 호출."""
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
