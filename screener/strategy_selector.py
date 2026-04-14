"""screener/strategy_selector.py — 시장 상황 기반 전략 자동 선택.

현재는 Momentum 단일 전략 체제 (2026-04-14 정리).
Flow/Pullback/Gap/OpenBreak/BigCandle은 strategy/archive/로 이동.
"""

from __future__ import annotations

from loguru import logger

from config.settings import AppConfig
from core.kiwoom_rest import KiwoomRestClient


class StrategySelector:
    """시장 데이터 수집 + momentum 단일 전략 선택.

    force_strategy가 비어있어도 momentum을 반환 (단일 전략 체제).
    """

    DEFAULT_MOMENTUM_ETF_THRESHOLD: float = 2.0

    def __init__(self, config: AppConfig, rest_client: KiwoomRestClient) -> None:
        self._config = config
        self._rest_client = rest_client

        sel = getattr(config, "selector", None) or {}
        self._momentum_etf_threshold = sel.get(
            "momentum_etf_threshold", self.DEFAULT_MOMENTUM_ETF_THRESHOLD,
        )

    async def collect_market_data(
        self, candidate_ticker: str | None = None,
    ) -> dict:
        snapshot = await self._rest_client.get_market_snapshot()
        return {
            "kospi_gap_pct": snapshot["kospi_gap_pct"],
            "sector_etf_change_pct": snapshot["sector_etf_change_pct"],
            "top_sector": snapshot.get("top_sector", ""),
            "index_range_pct": snapshot["index_range_pct"],
            "candidate_ticker": candidate_ticker,
        }

    async def select(
        self, market_data: dict | None = None, candidate_ticker: str | None = None,
    ) -> tuple[str | None, str | None]:
        if market_data is None:
            market_data = await self.collect_market_data(candidate_ticker)

        candidate = market_data.get("candidate_ticker")

        force = getattr(self._config, "force_strategy", "")
        if force and force != "momentum":
            logger.warning(f"force_strategy={force} 무시 — momentum만 지원")

        logger.info(
            f"전략 선택: 모멘텀 (섹터 ETF {market_data.get('sector_etf_change_pct', 0):.2f}%)",
        )
        return "momentum", candidate

    def _check_momentum(self, market_data: dict) -> bool:
        """섹터 ETF 등락률이 임계값 이상이면 모멘텀 전략. (참고용 — 현재 select는 항상 momentum)"""
        return float(market_data.get("sector_etf_change_pct", 0)) >= self._momentum_etf_threshold
