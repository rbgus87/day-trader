"""screener/strategy_selector.py — 시장 상황 기반 전략 자동 선택."""

from __future__ import annotations

from loguru import logger

from config.settings import AppConfig
from core.kiwoom_rest import KiwoomRestClient


class StrategySelector:
    """시장 데이터를 분석하여 당일 적용할 전략과 종목을 선택한다.

    우선순위: ORB > 모멘텀 > VWAP > 눌림목 > None
    """

    # 임계값 상수
    ORB_GAP_THRESHOLD: float = 0.5          # KOSPI 갭 기준 (%)
    MOMENTUM_ETF_THRESHOLD: float = 1.5     # 섹터 ETF 변동 기준 (%)
    VWAP_RANGE_THRESHOLD: float = 0.5       # 지수 변동폭 기준 (%)

    def __init__(self, config: AppConfig, rest_client: KiwoomRestClient) -> None:
        self._config = config
        self._rest_client = rest_client

    async def select(
        self, market_data: dict
    ) -> tuple[str | None, str | None]:
        """시장 데이터를 분석하여 (전략명, 종목코드) 반환.

        Args:
            market_data: {
                "kospi_gap_pct": float,          # KOSPI 갭 등락률 (%)
                "sector_etf_change_pct": float,  # 섹터 ETF 변동률 (%)
                "index_range_pct": float,        # 지수 일중 변동폭 (%)
                "candidate_ticker": str | None,  # 눌림목 후보 종목
            }

        Returns:
            (strategy_name, ticker) 또는 (None, None)
        """
        candidate = market_data.get("candidate_ticker")

        if self._check_orb(market_data):
            logger.info("전략 선택: ORB (KOSPI 갭 %.2f%%)", market_data.get("kospi_gap_pct", 0))
            return "orb", candidate

        if self._check_momentum(market_data):
            logger.info(
                "전략 선택: 모멘텀 (섹터 ETF %.2f%%)",
                market_data.get("sector_etf_change_pct", 0),
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
        """KOSPI 갭이 +0.5% 이상이면 ORB 전략 적용."""
        return float(market_data.get("kospi_gap_pct", 0)) >= self.ORB_GAP_THRESHOLD

    def _check_momentum(self, market_data: dict) -> bool:
        """특정 섹터 ETF가 +1.5% 이상이면 모멘텀 브레이크아웃 전략 적용."""
        return float(market_data.get("sector_etf_change_pct", 0)) >= self.MOMENTUM_ETF_THRESHOLD

    def _check_vwap(self, market_data: dict) -> bool:
        """지수 변동폭이 ±0.5% 이내(절댓값 기준)면 VWAP 회귀 전략 적용."""
        return abs(float(market_data.get("index_range_pct", 999))) <= self.VWAP_RANGE_THRESHOLD

    def _check_pullback(self, market_data: dict) -> bool:
        """눌림목 후보 종목이 존재하면 눌림목 매매 전략 적용 (폴백)."""
        return market_data.get("candidate_ticker") is not None
