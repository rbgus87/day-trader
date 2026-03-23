"""backtest/data_collector.py — 키움 REST API 과거 분봉 수집 배치."""

from loguru import logger

from core.kiwoom_rest import KiwoomRestClient
from data.db_manager import DbManager

_INSERT_SQL = (
    "INSERT OR IGNORE INTO intraday_candles "
    "(ticker, tf, ts, open, high, low, close, volume) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)


class DataCollector:
    """과거 분봉 데이터를 수집해 DB에 저장한다.

    키움 REST API는 1회 호출당 최대 900개 캔들을 반환한다.
    ``days`` 파라미터만큼의 영업일 데이터가 쌓일 때까지 페이지네이션을 반복한다.
    Rate Limiting은 ``rest_client`` 내부에서 처리한다.
    """

    # 1분봉 기준 하루 최대 캔들 수 (09:00~15:30 → 390분, 여유 포함)
    _CANDLES_PER_DAY = 400
    # API 1회 최대 반환 개수
    _PAGE_SIZE = 900

    def __init__(self, rest_client: KiwoomRestClient, db: DbManager) -> None:
        self._rest = rest_client
        self._db = db

    async def collect_minute_candles(self, ticker: str, days: int = 30) -> int:
        """``ticker`` 의 과거 ``days`` 일치 분봉을 수집해 DB에 저장한다.

        Args:
            ticker: 종목코드 (예: "005930")
            days: 수집 대상 영업일 수

        Returns:
            실제로 저장된 캔들 수 (중복 제외)
        """
        target = days * self._CANDLES_PER_DAY
        total_saved = 0
        total_fetched = 0

        logger.info(f"[DataCollector] {ticker} 분봉 수집 시작 (목표: {days}일 ≈ {target}개)")

        while total_fetched < target:
            try:
                data = await self._rest.get_minute_ohlcv(ticker)
            except Exception as exc:
                logger.error(f"[DataCollector] API 호출 실패: {exc}")
                break

            saved = await self._parse_and_save(ticker, data)
            output = data.get("output2") or []
            fetched = len(output)

            total_saved += saved
            total_fetched += fetched

            logger.debug(
                f"[DataCollector] {ticker} 페이지 완료 — "
                f"fetched={fetched}, saved={saved}, total_saved={total_saved}"
            )

            # API가 빈 응답을 돌려주거나 PAGE_SIZE 미만이면 더 이상 데이터 없음
            if fetched == 0 or fetched < self._PAGE_SIZE:
                break

        logger.info(f"[DataCollector] {ticker} 수집 완료 — 총 저장: {total_saved}개")
        return total_saved

    async def _parse_and_save(self, ticker: str, data: dict) -> int:
        """API 응답을 파싱해 ``intraday_candles`` 테이블에 저장한다.

        Args:
            ticker: 종목코드
            data: API 응답 dict (``data["output2"]`` 에 캔들 리스트)

        Returns:
            저장된 캔들 수 (INSERT OR IGNORE 기준, 중복 제외)
        """
        output2: list[dict] = data.get("output2") or []
        if not output2:
            return 0

        saved = 0
        for candle in output2:
            ts = _parse_timestamp(candle.get("stck_cntg_hour", ""))
            if ts is None:
                continue

            params = (
                ticker,
                "1m",
                ts,
                _to_float(candle.get("stck_oprc")),
                _to_float(candle.get("stck_hgpr")),
                _to_float(candle.get("stck_lwpr")),
                _to_float(candle.get("stck_clpr")),
                _to_int(candle.get("cntg_vol")),
            )
            result = await self._db.execute_safe(_INSERT_SQL, params)
            if result is not None:
                saved += 1

        return saved


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _parse_timestamp(raw: str) -> str | None:
    """``"HHMMSS"`` 형식 문자열을 ``"HH:MM:SS"`` 로 변환한다.

    날짜 정보가 API 응답에 없으므로 시각만 저장한다.
    실제 운영에서는 날짜를 함께 받아 ``"YYYY-MM-DD HH:MM:SS"`` 로 구성한다.

    Args:
        raw: 키움 API의 ``stck_cntg_hour`` 값 (예: ``"090100"``)

    Returns:
        ``"HH:MM:SS"`` 문자열, 파싱 불가 시 ``None``
    """
    if not raw or len(raw) < 6:
        return None
    try:
        hh, mm, ss = raw[:2], raw[2:4], raw[4:6]
        return f"{hh}:{mm}:{ss}"
    except Exception:
        return None


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _to_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None
