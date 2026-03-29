"""screener/strategy_selector.py — 시장 상황 기반 전략 자동 선택."""

from __future__ import annotations

from loguru import logger

from config.settings import AppConfig
from core.kiwoom_rest import KiwoomRestClient


class StrategySelector:
    """시장 데이터를 분석하여 당일 적용할 전략과 종목을 선택한다.

    우선순위: ORB > 모멘텀 > VWAP > 눌림목 > None
    REST API로 실시간 시장 데이터를 자동 수집한다.
    """

    # 기본 임계값 (config.yaml의 strategy.selector 섹션으로 오버라이드 가능)
    DEFAULT_ORB_GAP_THRESHOLD: float = 0.5          # KOSPI 갭 기준 (%)
    DEFAULT_MOMENTUM_ETF_THRESHOLD: float = 1.5     # 섹터 ETF 변동 기준 (%)
    DEFAULT_VWAP_RANGE_THRESHOLD: float = 0.5       # 지수 변동폭 기준 (%)

    def __init__(self, config: AppConfig, rest_client: KiwoomRestClient) -> None:
        self._config = config
        self._rest_client = rest_client

        # config.yaml에서 임계값 로드 (없으면 기본값)
        sel = getattr(config, "selector", None) or {}
        self._orb_gap_threshold = sel.get(
            "orb_gap_threshold", self.DEFAULT_ORB_GAP_THRESHOLD,
        )
        self._momentum_etf_threshold = sel.get(
            "momentum_etf_threshold", self.DEFAULT_MOMENTUM_ETF_THRESHOLD,
        )
        self._vwap_range_threshold = sel.get(
            "vwap_range_threshold", self.DEFAULT_VWAP_RANGE_THRESHOLD,
        )

    async def collect_market_data(
        self, candidate_ticker: str | None = None,
    ) -> dict:
        """REST API로 시장 데이터를 수집하여 반환한다.

        Args:
            candidate_ticker: 스크리닝에서 선정된 눌림목 후보 종목코드.

        Returns:
            전략 선택에 필요한 market_data dict.
        """
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
        """시장 데이터를 분석하여 (전략명, 종목코드) 반환.

        Args:
            market_data: 미리 수집한 시장 데이터. None이면 REST API로 자동 수집.
            candidate_ticker: market_data가 None일 때 사용할 후보 종목코드.

        Returns:
            (strategy_name, ticker) 또는 (None, None)
        """
        if market_data is None:
            market_data = await self.collect_market_data(candidate_ticker)

        candidate = market_data.get("candidate_ticker")

        if self._check_orb(market_data):
            logger.info("전략 선택: ORB (KOSPI 갭 %.2f%%)", market_data.get("kospi_gap_pct", 0))
            return "orb", candidate

        if self._check_momentum(market_data):
            logger.info(
                "전략 선택: 모멘텀 (섹터 ETF %.2f%%, %s)",
                market_data.get("sector_etf_change_pct", 0),
                market_data.get("top_sector", ""),
            )
            return "momentum", candidate

        if self._check_vwap(market_data):
            logger.info(
                "전략 선택: VWAP (지수 변동 %.2f%%)",
                market_data.get("index_range_pct", 0),
            )
            return "vwap", candidate

        if self._check_pullback(market_data):
            logger.info("전략 선택: 눌림목 (후보 종목: %s)", candidate)
            return "pullback", candidate

        logger.info("전략 선택 없음 — 당일 매매 없음")
        return None, None

    # ------------------------------------------------------------------
    # 조건 판별 메서드
    # ------------------------------------------------------------------

    def _check_orb(self, market_data: dict) -> bool:
        """KOSPI 갭이 임계값 이상이면 ORB 전략 적용."""
        return float(market_data.get("kospi_gap_pct", 0)) >= self._orb_gap_threshold

    def _check_momentum(self, market_data: dict) -> bool:
        """섹터 ETF 등락률이 임계값 이상이면 모멘텀 브레이크아웃 전략 적용."""
        return float(market_data.get("sector_etf_change_pct", 0)) >= self._momentum_etf_threshold

    def _check_vwap(self, market_data: dict) -> bool:
        """지수 변동폭이 임계값 이내(절댓값 기준)면 VWAP 회귀 전략 적용."""
        return abs(float(market_data.get("index_range_pct", 999))) <= self._vwap_range_threshold

    def _check_pullback(self, market_data: dict) -> bool:
        """눌림목 후보 종목이 존재하면 눌림목 매매 전략 적용 (폴백)."""
        return market_data.get("candidate_ticker") is not None
