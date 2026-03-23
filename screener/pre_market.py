"""screener/pre_market.py — 장 전 4단계 스크리닝 (PRD F-SCR-01)."""

from __future__ import annotations

from loguru import logger

from config.settings import ScreenerConfig


class PreMarketScreener:
    """08:30 장 전 4단계 스크리닝으로 단타 후보 5~10종목 선정.

    후보 dict 스펙:
        ticker              (str)  종목코드
        name                (str)  종목명
        market_cap          (int)  시가총액 (원)
        avg_volume_amount   (int)  일평균 거래대금 (원)
        volume              (int)  전일 거래량
        prev_volume         (int)  전전일 거래량 (거래량 급증 비교 기준)
        atr_pct             (float) ATR(14) / 전일 종가 비율
        ma20_trend          (str)  "ascending" | "flat" | "descending"
        institutional_buy   (int)  기관 순매수 수량
        foreign_buy         (int)  외국인 순매수 수량
        has_event           (bool) 실적발표/주요공시 예정 여부
    """

    MAX_RESULTS = 10
    MIN_RESULTS = 5

    def __init__(self, rest_client, db, config: ScreenerConfig):
        self._rest = rest_client
        self._db = db
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def screen(self, candidates: list[dict]) -> list[dict]:
        """4단계 필터 적용 후 상위 5~10종목 반환."""
        logger.info(f"스크리닝 시작: 후보 {len(candidates)}종목")

        step1 = self._filter_basic(candidates)
        logger.info(f"기본 필터 통과: {len(step1)}종목")

        step2 = self._filter_technical(step1)
        logger.info(f"기술 필터 통과: {len(step2)}종목")

        step3 = self._filter_supply(step2)
        logger.info(f"수급 필터 통과: {len(step3)}종목")

        step4 = self._filter_event(step3)
        logger.info(f"이벤트 필터 통과: {len(step4)}종목")

        # score 기준 내림차순 정렬, 최대 10종목
        ranked = sorted(step4, key=lambda x: x.get("score", 0), reverse=True)
        results = ranked[: self.MAX_RESULTS]
        logger.info(f"최종 선정: {len(results)}종목")
        return results

    # ------------------------------------------------------------------
    # Stage 1: 기본 필터
    # ------------------------------------------------------------------

    def _filter_basic(self, candidates: list[dict]) -> list[dict]:
        """시가총액, 일평균 거래대금, 관리/투자주의 제외 필터.

        관리종목/투자주의는 candidates에 'is_managed' 필드로 전달하거나,
        필드 부재 시 False(정상)로 간주.
        """
        result = []
        for c in candidates:
            if c.get("market_cap", 0) < self._config.min_market_cap:
                logger.debug(f"{c['ticker']} 제외: 시가총액 미달 ({c.get('market_cap', 0):,})")
                continue
            if c.get("avg_volume_amount", 0) < self._config.min_avg_volume_amount:
                logger.debug(f"{c['ticker']} 제외: 거래대금 미달 ({c.get('avg_volume_amount', 0):,})")
                continue
            if c.get("is_managed", False):
                logger.debug(f"{c['ticker']} 제외: 관리/투자주의 종목")
                continue
            result.append(c)
        return result

    # ------------------------------------------------------------------
    # Stage 2: 기술 필터
    # ------------------------------------------------------------------

    def _filter_technical(self, candidates: list[dict]) -> list[dict]:
        """20일 이평 상향, 전일 거래량 +50%, ATR(14) 2% 이상 필터."""
        result = []
        for c in candidates:
            # MA20 상향
            if self._config.ma20_ascending and c.get("ma20_trend") != "ascending":
                logger.debug(f"{c['ticker']} 제외: MA20 비상향 ({c.get('ma20_trend')})")
                continue

            # 거래량 급증: volume >= prev_volume * volume_surge_ratio
            volume = c.get("volume", 0)
            prev_volume = c.get("prev_volume", 0)
            if prev_volume > 0:
                surge_ratio = volume / prev_volume
            else:
                surge_ratio = 0.0

            if surge_ratio < self._config.volume_surge_ratio:
                logger.debug(
                    f"{c['ticker']} 제외: 거래량 급증 미달 "
                    f"(ratio={surge_ratio:.2f} < {self._config.volume_surge_ratio})"
                )
                continue

            # ATR 2% 이상
            if c.get("atr_pct", 0.0) < self._config.min_atr_pct:
                logger.debug(
                    f"{c['ticker']} 제외: ATR 미달 ({c.get('atr_pct', 0.0):.4f} < {self._config.min_atr_pct})"
                )
                continue

            result.append(c)
        return result

    # ------------------------------------------------------------------
    # Stage 3: 수급 필터 (점수 부여)
    # ------------------------------------------------------------------

    def _filter_supply(self, candidates: list[dict]) -> list[dict]:
        """기관/외국인 순매수 가산점 부여. 제외 없음 — 점수만 조정."""
        result = []
        for c in candidates:
            score = c.get("score", 0.0)

            inst_buy = c.get("institutional_buy", 0)
            foreign_buy = c.get("foreign_buy", 0)

            if inst_buy > 0:
                # 기관 순매수: 주요 가산점
                score += 2.0
            if foreign_buy > 0:
                # 외국인 순매수: 보조 가산점
                score += 1.0

            updated = {**c, "score": score}
            result.append(updated)
        return result

    # ------------------------------------------------------------------
    # Stage 4: 이벤트 필터
    # ------------------------------------------------------------------

    def _filter_event(self, candidates: list[dict]) -> list[dict]:
        """실적발표/주요공시 예정 종목 제외."""
        result = []
        for c in candidates:
            if c.get("has_event", False):
                logger.debug(f"{c['ticker']} 제외: 이벤트(실적/공시) 예정")
                continue
            result.append(c)
        return result

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def save_results(self, date: str, results: list[dict]) -> None:
        """스크리닝 결과를 screener_results 테이블에 저장."""
        for rank, item in enumerate(results, start=1):
            ticker = item["ticker"]
            score = item.get("score", 0.0)
            strategy_hint = item.get("strategy_hint", "")
            selected = 1 if rank <= self.MAX_RESULTS else 0

            await self._db.execute(
                """
                INSERT INTO screener_results (date, ticker, score, strategy_hint, selected)
                VALUES (?, ?, ?, ?, ?)
                """,
                (date, ticker, score, strategy_hint, selected),
            )
        logger.info(f"스크리닝 결과 저장 완료: {date}, {len(results)}종목")
