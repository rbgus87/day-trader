"""utils/market_calendar.py — 장 운영 시간 유틸리티.

KST 기준 장 시간 판정, 거래일 체크(주말/공휴일 제외),
WebSocket 활성 시간대 판정 등을 제공한다.
"""

from datetime import date, datetime, time, timedelta


# 장 시작/종료 시간
MARKET_OPEN = time(9, 0)
MARKET_CLOSE = time(15, 30)

# WebSocket 시세 수신 가능 시간대 (동시호가 ~ 시간외 포함)
WS_DATA_START = time(8, 50)
WS_DATA_END = time(15, 40)

# 2026년 한국 공휴일 (수동 관리)
KR_HOLIDAYS_2026 = {
    date(2026, 1, 1),   # 신정
    date(2026, 2, 16),  # 설날 연휴
    date(2026, 2, 17),  # 설날
    date(2026, 2, 18),  # 설날 연휴
    date(2026, 3, 1),   # 삼일절
    date(2026, 5, 5),   # 어린이날
    date(2026, 5, 24),  # 석가탄신일
    date(2026, 6, 6),   # 현충일
    date(2026, 8, 15),  # 광복절
    date(2026, 9, 24),  # 추석 연휴
    date(2026, 9, 25),  # 추석
    date(2026, 9, 26),  # 추석 연휴
    date(2026, 10, 3),  # 개천절
    date(2026, 10, 9),  # 한글날
    date(2026, 12, 25), # 크리스마스
}


def now_local() -> datetime:
    """현재 로컬 시각 반환."""
    return datetime.now()


def is_market_open() -> bool:
    """현재 장이 열려 있는지 판정 (거래일 + 09:00~15:30)."""
    now = now_local()
    if not is_trading_day(now.date()):
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def is_ws_active_hours() -> bool:
    """WebSocket 시세 수신이 가능한 시간대인지 판정 (08:50~15:40)."""
    now = now_local()
    if not is_trading_day(now.date()):
        return False
    return WS_DATA_START <= now.time() <= WS_DATA_END


def is_trading_day(target_date: date | None = None) -> bool:
    """해당 날짜가 거래일인지 판정 (주말/공휴일 제외)."""
    d = target_date or now_local().date()
    # 주말 체크
    if d.weekday() >= 5:
        return False
    # 공휴일 체크
    if d in KR_HOLIDAYS_2026:
        return False
    return True
