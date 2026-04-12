"""core/market_filter.py — 시장 지수 기반 매매 허용 필터.

코스피(001) / 코스닥(101) 종합지수의 이동평균(MA) 대비 현재가를 비교하여
시장별 강세/약세를 판단한다. 종목의 market 필드("kospi"/"kosdaq")에 따라
해당 시장이 강세일 때만 매수를 허용한다.

- 지수 일봉은 키움 ka20006 API 사용 (cur_prc 값은 100배 스케일)
- refresh()는 장 시작 전 또는 주기적으로 호출
- is_allowed(market)는 동기 호출 (캐시된 상태 반환)
"""

from datetime import datetime

from loguru import logger

from core.kiwoom_rest import KiwoomRestClient


# 키움 업종 코드
INDEX_KOSPI = "001"    # 코스피 종합
INDEX_KOSDAQ = "101"   # 코스닥 종합


class MarketFilter:
    """코스피/코스닥 지수의 MA 기반 강세/약세 판단기."""

    def __init__(self, rest: KiwoomRestClient, ma_length: int = 5):
        self._rest = rest
        self._ma_length = ma_length
        self._kospi_strong = False
        self._kosdaq_strong = False
        self._last_update: datetime | None = None

    @property
    def kospi_strong(self) -> bool:
        return self._kospi_strong

    @property
    def kosdaq_strong(self) -> bool:
        return self._kosdaq_strong

    @property
    def last_update(self) -> datetime | None:
        return self._last_update

    async def refresh(self) -> None:
        """코스피/코스닥 지수 일봉 조회 → MA 계산 → 강세 여부 갱신.

        실패 시 보수적으로 둘 다 False (매수 차단) 처리.
        """
        try:
            self._kospi_strong = await self._check_index(INDEX_KOSPI)
        except Exception as e:
            logger.error(f"[MARKET] 코스피 지수 조회 실패: {e}")
            self._kospi_strong = False

        try:
            self._kosdaq_strong = await self._check_index(INDEX_KOSDAQ)
        except Exception as e:
            logger.error(f"[MARKET] 코스닥 지수 조회 실패: {e}")
            self._kosdaq_strong = False

        self._last_update = datetime.now()
        logger.info(
            f"[MARKET] 코스피 {'강세' if self._kospi_strong else '약세'}, "
            f"코스닥 {'강세' if self._kosdaq_strong else '약세'} "
            f"(MA{self._ma_length})"
        )

    async def _check_index(self, index_code: str) -> bool:
        """지수 일봉 조회 → 현재가 > MA(ma_length) 인지 판단.

        응답 컨테이너 키가 스펙에 따라 달라질 수 있어 여러 후보 키를 fallback.
        cur_prc 값은 100배 스케일링되어 있으나 상대 비교이므로 스케일 제거 불필요.
        """
        data = await self._rest.get_index_daily(index_code)

        items: list = []
        for key in (
            "inds_dt_pole_qry",
            "inds_dly_qry",
            "output",
            "output1",
            "output2",
        ):
            val = data.get(key)
            if isinstance(val, list) and val:
                items = val
                break

        if len(items) < self._ma_length + 1:
            logger.warning(
                f"[MARKET] 지수 {index_code} 데이터 부족: "
                f"{len(items)}건 (필요 {self._ma_length + 1}건)"
            )
            return False

        # items[0]: 최신일, items[1..ma_length]: 최근 MA 기간
        try:
            current = float(items[0].get("cur_prc", 0))
            recent_closes = [
                float(items[i].get("cur_prc", 0))
                for i in range(1, self._ma_length + 1)
            ]
        except (TypeError, ValueError) as e:
            logger.error(f"[MARKET] 지수 {index_code} 파싱 실패: {e}")
            return False

        if current <= 0 or any(c <= 0 for c in recent_closes):
            logger.warning(f"[MARKET] 지수 {index_code} 이상치 포함 → 약세 처리")
            return False

        ma = sum(recent_closes) / len(recent_closes)
        return current > ma

    def is_allowed(self, market: str) -> bool:
        """종목의 시장 구분으로 매수 허용 여부 반환.

        Args:
            market: "kospi" / "kosdaq" / 그 외

        Returns:
            해당 시장이 강세면 True. "unknown" 등은 둘 중 하나라도 강세면 True.
        """
        if market == "kospi":
            return self._kospi_strong
        if market == "kosdaq":
            return self._kosdaq_strong
        # unknown → 보수적으로 둘 중 하나라도 강세면 허용
        return self._kospi_strong or self._kosdaq_strong
