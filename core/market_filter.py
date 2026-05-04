"""core/market_filter.py — 시장 지수 기반 매매 허용 필터.

코스피(001) / 코스닥(101) 종합지수의 이동평균(MA) 대비 현재가를 비교하여
시장별 강세/약세를 판단한다. 종목의 market 필드("kospi"/"kosdaq")에 따라
해당 시장이 강세일 때만 매수를 허용한다.

- 지수 일봉은 키움 ka20006 API 사용 (cur_prc 값은 100배 스케일)
- refresh()는 장 시작 전 또는 주기적으로 호출
- is_allowed(market)는 동기 호출 (캐시된 상태 반환)
- 데이터 부족/이상치/예외 시 이전 캐시 상태 유지 (false weak 방지)
- 0건 응답 시 1회 재시도
"""

import asyncio
from datetime import datetime

from loguru import logger

from core.kiwoom_rest import KiwoomRestClient


# 키움 업종 코드
INDEX_KOSPI = "001"    # 코스피 종합
INDEX_KOSDAQ = "101"   # 코스닥 종합


class MarketFilter:
    """코스피/코스닥 지수의 MA 기반 강세/약세 판단기."""

    def __init__(
        self,
        rest: KiwoomRestClient,
        ma_length: int = 5,
        retry_delay: float = 1.0,
    ):
        self._rest = rest
        self._ma_length = ma_length
        self._retry_delay = retry_delay
        # 첫 성공 전까지는 보수적 차단보다 진입 허용을 선택 (강세 가정)
        self._kospi_strong = True
        self._kosdaq_strong = True
        self._last_update: datetime | None = None
        # 마지막 성공 갱신의 (current, ma) — 로그/디버깅용. 키움 cur_prc는 100배 스케일.
        self._index_metrics: dict[str, tuple[float, float]] = {}

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

        판정 불가(None) 또는 예외 발생 시 이전 캐시 상태를 그대로 유지한다.
        """
        try:
            result = await self._check_index(INDEX_KOSPI)
            if result is not None:
                self._kospi_strong = result
        except Exception as e:
            logger.error(
                f"[MARKET] 코스피 지수 조회 실패: {e} → 이전 상태 유지"
                f" (현재 {'강세' if self._kospi_strong else '약세'})"
            )

        try:
            result = await self._check_index(INDEX_KOSDAQ)
            if result is not None:
                self._kosdaq_strong = result
        except Exception as e:
            logger.error(
                f"[MARKET] 코스닥 지수 조회 실패: {e} → 이전 상태 유지"
                f" (현재 {'강세' if self._kosdaq_strong else '약세'})"
            )

        self._last_update = datetime.now()

        def _fmt(idx_code: str, strong: bool) -> str:
            state = "강세" if strong else "약세"
            m = self._index_metrics.get(idx_code)
            if m is None:
                return f"{state} (값 없음)"
            cur, ma = m
            return f"{state} (현재={cur / 100:.2f}, MA{self._ma_length}={ma / 100:.2f})"

        logger.info(
            f"[MARKET] 코스피 {_fmt(INDEX_KOSPI, self._kospi_strong)}, "
            f"코스닥 {_fmt(INDEX_KOSDAQ, self._kosdaq_strong)}"
        )

    async def _fetch_items(self, index_code: str) -> list:
        """ka20006 호출 후 응답 컨테이너에서 일봉 리스트 추출.

        스펙에 따라 컨테이너 키가 달라질 수 있어 여러 후보를 fallback.
        """
        data = await self._rest.get_index_daily(index_code)
        for key in (
            "inds_dt_pole_qry",
            "inds_dly_qry",
            "output",
            "output1",
            "output2",
        ):
            val = data.get(key)
            if isinstance(val, list) and val:
                return val
        return []

    async def _check_index(self, index_code: str) -> bool | None:
        """지수 일봉 조회 → 현재가 > MA 인지 판단.

        Returns:
            True  — 강세 (현재가 > MA)
            False — 약세 (현재가 ≤ MA)
            None  — 판정 불가 (데이터 부족/이상치). 호출자는 이전 상태 유지.

        cur_prc 값은 100배 스케일링되어 있으나 상대 비교이므로 스케일 제거 불필요.
        0건 응답 시 retry_delay 후 1회 재시도한다.
        """
        items = await self._fetch_items(index_code)

        if len(items) < self._ma_length + 1:
            logger.warning(
                f"[MARKET] 지수 {index_code} 데이터 부족: "
                f"{len(items)}건 (필요 {self._ma_length + 1}건) → "
                f"{self._retry_delay}s 후 재시도"
            )
            await asyncio.sleep(self._retry_delay)
            items = await self._fetch_items(index_code)
            if len(items) < self._ma_length + 1:
                logger.warning(
                    f"[MARKET] 지수 {index_code} 재시도 후에도 부족: "
                    f"{len(items)}건 → 이전 상태 유지"
                )
                return None
            logger.info(f"[MARKET] 지수 {index_code} 재시도 성공: {len(items)}건")

        # 장 시작 전/장중에 ka20006이 당일 행을 items[0]에 포함시키는 경우가 있다.
        # 그 행은 동시호가/시가가 미완성이라 MA5 비교에 부적합 → 직전 거래일 기준으로
        # 비교하기 위해 items[0].dt가 오늘이면 한 칸 밀어 사용한다.
        today_str = datetime.now().strftime("%Y%m%d")
        start = 1 if items[0].get("dt") == today_str else 0

        if len(items) < start + self._ma_length + 1:
            logger.warning(
                f"[MARKET] 지수 {index_code} 당일 행 스킵 후 데이터 부족: "
                f"{len(items)}건 → 이전 상태 유지"
            )
            return None

        try:
            current = float(items[start].get("cur_prc", 0))
            recent_closes = [
                float(items[i].get("cur_prc", 0))
                for i in range(start + 1, start + self._ma_length + 1)
            ]
        except (TypeError, ValueError) as e:
            logger.error(
                f"[MARKET] 지수 {index_code} 파싱 실패: {e} → 이전 상태 유지"
            )
            return None

        if current <= 0 or any(c <= 0 for c in recent_closes):
            logger.warning(
                f"[MARKET] 지수 {index_code} 이상치 포함 → 이전 상태 유지"
            )
            return None

        ma = sum(recent_closes) / len(recent_closes)
        self._index_metrics[index_code] = (current, ma)
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
