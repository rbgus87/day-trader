"""screener/strategy_selector.py — 시장 상황 기반 전략 자동 선택.

3전략 체제: Momentum > Flow > Pullback > None
ORB/VWAP 폐기 (2026-03-30 백테스트 결과).
"""

from __future__ import annotations

from loguru import logger

from config.settings import AppConfig
from core.kiwoom_rest import KiwoomRestClient


class StrategySelector:
    """시장 데이터를 분석하여 당일 적용할 전략과 종목을 선택한다.

    우선순위: Momentum > Flow > Pullback > None
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
        if force:
            logger.info("전략 강제 설정: %s", force)
            return force, candidate

        if self._check_momentum(market_data):
            logger.info(
                "전략 선택: 모멘텀 (섹터 ETF %.2f%%)",
                market_data.get("sector_etf_change_pct", 0),
            )
            return "momentum", candidate

        if self._check_flow(market_data):
            logger.info("전략 선택: Flow (수급 감지 대기)")
            return "flow", candidate

        if self._check_pullback(market_data):
            logger.info("전략 선택: 눌림목 (후보 종목: %s)", candidate)
            return "pullback", candidate

        logger.info("전략 선택 없음 — 당일 매매 없음")
        return None, None

    def _check_momentum(self, market_data: dict) -> bool:
        """섹터 ETF 등락률이 임계값 이상이면 모멘텀 전략."""
        return float(market_data.get("sector_etf_change_pct", 0)) >= self._momentum_etf_threshold

    def _check_flow(self, market_data: dict) -> bool:
        """Flow는 장중 실시간 판단이므로 항상 선택 가능."""
        return market_data.get("candidate_ticker") is not None

    def _check_pullback(self, market_data: dict) -> bool:
        """후보 종목이 존재하고 ATR >= 3%이면 적용 (저변동 종목 제외)."""
        if market_data.get("candidate_ticker") is None:
            return False
        atr_pct = float(market_data.get("atr_pct", 0))
        if atr_pct > 0 and atr_pct < 0.03:
            logger.info("Pullback 제외: ATR %.2f%% < 3%%", atr_pct * 100)
            return False
        return True
