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
from datetime import datetime, time

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

        # ── 장중 필터 상태 ──────────────────────────────────────────────
        self._kospi_intraday_blocked: bool = False
        self._kosdaq_intraday_blocked: bool = False
        # 해제 시각 — 쿨다운(최소 cooldown_minutes 후 재차단 가능) 계산용
        self._kospi_last_unblock: datetime | None = None
        self._kosdaq_last_unblock: datetime | None = None
        # 마지막 갱신의 index_code → change_pct (로그/디버깅용)
        self._intraday_change: dict[str, float] = {}

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

        # 시간 분기: 장 시작 전(< 09:00)에는 당일 행이 동시호가 단계라 미완성 → 스킵.
        # 장 시작 후(≥ 09:00)에는 items[0]이 장중 현재가이므로 그대로 사용해 적시성 확보.
        now = datetime.now()
        today_str = now.strftime("%Y%m%d")
        skip_today = (
            items[0].get("dt") == today_str and now.time() < time(9, 0)
        )
        start = 1 if skip_today else 0

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

    # ── 장중 필터 (당일 시가 대비 등락률) ─────────────────────────────────

    @property
    def intraday_change(self) -> dict[str, float]:
        """마지막 갱신의 index_code → change_pct (로그/UI용)."""
        return dict(self._intraday_change)

    def is_intraday_blocked(self, market: str) -> bool:
        """시장별 장중 차단 여부.

        Args:
            market: "kospi" / "kosdaq" / 그 외

        Returns:
            차단 중이면 True. "unknown"은 둘 중 하나라도 차단이면 True.
        """
        if market == "kospi":
            return self._kospi_intraday_blocked
        if market == "kosdaq":
            return self._kosdaq_intraday_blocked
        return self._kospi_intraday_blocked or self._kosdaq_intraday_blocked

    async def _fetch_intraday_change(self, index_code: str) -> float | None:
        """당일 지수 시가 대비 현재가 등락률 계산.

        시가 필드(open_prc / oprc / strt_prc)가 응답에 없으면 전일 종가를 대신 사용.
        장 전(09:00 미만) 또는 당일 데이터 미포함 시 None 반환.
        """
        items = await self._fetch_items(index_code)
        if len(items) < 2:
            return None
        today_str = datetime.now().strftime("%Y%m%d")
        if items[0].get("dt") != today_str:
            return None  # 장 전 또는 당일 데이터 미포함
        try:
            cur = float(items[0].get("cur_prc", 0) or 0)
            if cur <= 0:
                return None
            # 시가 취득: 여러 후보 필드 순차 시도
            open_p = 0.0
            for fld in ("open_prc", "oprc", "strt_prc"):
                v = items[0].get(fld)
                if v is not None:
                    try:
                        open_p = float(v)
                        if open_p > 0:
                            break
                    except (TypeError, ValueError):
                        pass
            if open_p <= 0:
                # fallback: 전일 종가를 시가 대용으로 사용
                prev_close = float(items[1].get("cur_prc", 0) or 0)
                if prev_close <= 0:
                    return None
                open_p = prev_close
            return (cur - open_p) / open_p
        except (TypeError, ValueError):
            return None

    async def refresh_intraday(
        self,
        block_threshold: float = -0.01,
        resume_threshold: float = -0.005,
        cooldown_minutes: int = 20,
    ) -> None:
        """코스피/코스닥 당일 등락률 조회 → 장중 차단/해제 상태 갱신.

        히스테리시스:
          - 미차단 → 차단: change < block_threshold AND 쿨다운 기간 외
          - 차단 → 해제: change >= resume_threshold → 해제 + 쿨다운 시작
          - 해제 후 cooldown_minutes 이내는 재차단 불가
        예외 / 데이터 없음 시 현재 상태를 유지한다.
        """
        for index_code, market_key in (("001", "kospi"), ("101", "kosdaq")):
            try:
                change = await self._fetch_intraday_change(index_code)
                if change is None:
                    logger.debug(f"[INTRADAY] {market_key} 등락률 조회 불가 → 현재 상태 유지")
                    continue
                self._intraday_change[index_code] = change

                is_blocked: bool = getattr(self, f"_{market_key}_intraday_blocked")
                last_unblock: datetime | None = getattr(self, f"_{market_key}_last_unblock")

                if not is_blocked:
                    in_cooldown = (
                        last_unblock is not None
                        and (datetime.now() - last_unblock).total_seconds()
                        < cooldown_minutes * 60
                    )
                    if change < block_threshold and not in_cooldown:
                        setattr(self, f"_{market_key}_intraday_blocked", True)
                        logger.warning(
                            f"[INTRADAY] {market_key} 장중 매수 차단: "
                            f"등락률={change:.2%} < {block_threshold:.2%}"
                        )
                else:
                    if change >= resume_threshold:
                        setattr(self, f"_{market_key}_intraday_blocked", False)
                        setattr(self, f"_{market_key}_last_unblock", datetime.now())
                        logger.info(
                            f"[INTRADAY] {market_key} 장중 매수 재개: "
                            f"등락률={change:.2%} >= {resume_threshold:.2%}"
                        )
            except Exception as e:
                logger.error(f"[INTRADAY] {market_key} 장중 필터 갱신 실패: {e}")
