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

        페이지네이션: 각 API 호출에서 가장 오래된 캔들의 날짜를 추출해
        다음 호출의 ``base_dt`` 로 전달하여 이전 데이터를 가져온다.

        Args:
            ticker: 종목코드 (예: "005930")
            days: 수집 대상 영업일 수

        Returns:
            실제로 저장된 캔들 수 (중복 제외)
        """
        target = days * self._CANDLES_PER_DAY
        total_saved = 0
        total_fetched = 0
        base_dt = ""  # 빈 문자열 = 최신 데이터부터

        logger.info(f"[DataCollector] {ticker} 분봉 수집 시작 (목표: {days}일 ≈ {target}개)")

        while total_fetched < target:
            try:
                data = await self._rest.get_minute_ohlcv(ticker, base_dt=base_dt)
            except Exception as exc:
                logger.error(f"[DataCollector] API 호출 실패: {exc}")
                break

            candles = _extract_candles(data)
            fetched = len(candles)

            if fetched == 0:
                break

            saved = await self._parse_and_save(ticker, candles)
            total_saved += saved
            total_fetched += fetched

            # 페이지네이션: 가장 오래된 캔들의 날짜를 다음 base_dt로 사용
            oldest_cntr_tm = candles[-1].get("cntr_tm", "")
            if len(oldest_cntr_tm) >= 8:
                next_base_dt = oldest_cntr_tm[:8]  # YYYYMMDD 부분
                if next_base_dt == base_dt:
                    # 같은 날짜 반복 → 더 이상 이전 데이터 없음
                    logger.debug(f"[DataCollector] {ticker} 페이지네이션 종료 (같은 날짜 반복)")
                    break
                base_dt = next_base_dt
            else:
                break

            logger.debug(
                f"[DataCollector] {ticker} 페이지 완료 — "
                f"fetched={fetched}, saved={saved}, total_saved={total_saved}, "
                f"next_base_dt={base_dt}"
            )

            if fetched < self._PAGE_SIZE:
                break

        logger.info(f"[DataCollector] {ticker} 수집 완료 — 총 저장: {total_saved}개")
        return total_saved

    async def _parse_and_save(self, ticker: str, candles: list[dict]) -> int:
        """캔들 리스트를 파싱해 ``intraday_candles`` 테이블에 저장한다 (batch).

        Phase 4: per-row commit → executemany + 단일 commit (10x+ 가속).

        Returns:
            저장된 캔들 수 (INSERT OR IGNORE 기준, 중복 제외)
        """
        if not candles:
            return 0

        batch = []
        for candle in candles:
            ts = _parse_timestamp(candle.get("cntr_tm", ""))
            if ts is None:
                continue
            batch.append((
                ticker,
                "1m",
                ts,
                _abs_float(candle.get("open_pric")),
                _abs_float(candle.get("high_pric")),
                _abs_float(candle.get("low_pric")),
                _abs_float(candle.get("cur_prc")),
                _to_int(candle.get("trde_qty")),
            ))

        if not batch:
            return 0
        # batch_size = len(batch); 실제 INSERT OR IGNORE이라 중복은 무음 처리
        await self._db.executemany_safe(_INSERT_SQL, batch)
        return len(batch)


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _extract_candles(data: dict) -> list[dict]:
    """API 응답에서 캔들 리스트를 추출한다.

    키움 분봉 응답: ``data["stk_min_pole_chart_qry"]``
    """
    return (
        data.get("stk_min_pole_chart_qry")
        or data.get("output2")  # 하위 호환
        or []
    )


def _parse_timestamp(raw: str) -> str | None:
    """``"YYYYMMDDHHmmss"`` (14자리) 형식을 ``"YYYY-MM-DD HH:MM:SS"`` 로 변환한다.

    Args:
        raw: 키움 API의 ``cntr_tm`` 값 (예: ``"20260323090100"``)

    Returns:
        ``"YYYY-MM-DD HH:MM:SS"`` 문자열, 파싱 불가 시 ``None``
    """
    if not raw:
        return None

    # 14자리 (날짜+시각) 또는 6자리 (시각만) 지원
    if len(raw) >= 14:
        try:
            date = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
            time = f"{raw[8:10]}:{raw[10:12]}:{raw[12:14]}"
            return f"{date} {time}"
        except Exception:
            return None
    elif len(raw) >= 6:
        try:
            return f"{raw[:2]}:{raw[2:4]}:{raw[4:6]}"
        except Exception:
            return None
    return None


def _abs_float(value) -> float | None:
    """부호 포함 가격을 절대값 float로 변환한다.

    키움 API는 전일 대비 하락 시 음수로 반환한다.
    """
    if value is None:
        return None
    try:
        return abs(float(value))
    except (ValueError, TypeError):
        return None


def _to_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None
