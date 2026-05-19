"""core/vi_handler.py — VI(변동성완화장치) 휴리스틱 감지 및 주문 전환 의사결정.

가격 휴리스틱(전일종가 대비 ±static_pct 이상)으로 정적VI 발동을 추정하고,
REST 주문 거부(rt_cd ≠ "0")로 SUSPECTED 상태를 활성화한다. 무상태 인메모리.

스펙: docs/superpowers/specs/2026-05-12-vi-handler-design.md
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from loguru import logger


# KRX 상한가 / VI 제외 영역
_LIMIT_UP_MULTIPLIER = 1.30   # KRX 일반 종목 상한가 +30%
_LIMIT_UP_BUFFER = 0.99       # 상한가의 99% 이상은 limit_up_exit 영역으로 간주


class VIState(Enum):
    NORMAL = "normal"
    STATIC_VI = "static_vi"
    SUSPECTED = "suspected"


@dataclass(frozen=True)
class _Entry:
    state: VIState
    expires_at: datetime


class VIHandler:
    """VI 발동 추정 + 주문 전환 의사결정 (인메모리)."""

    def __init__(
        self,
        static_pct: float = 0.098,
        assumed_duration_sec: int = 150,
        suspected_duration_sec: int = 60,
    ):
        self._static_pct = static_pct
        self._assumed_duration = timedelta(seconds=assumed_duration_sec)
        self._suspected_duration = timedelta(seconds=suspected_duration_sec)
        self._entries: dict[str, _Entry] = {}
        # WS 1h로 관리 중인 종목 — update_from_tick이 fallback 역할만 수행
        self._ws_controlled: set[str] = set()
        # 감시 종목 집합 — 미설정 시 전 종목 INFO 로깅
        self._universe: set[str] = set()

    def get_vi_state(self, ticker: str) -> VIState:
        entry = self._entries.get(ticker)
        if entry is None:
            return VIState.NORMAL
        if datetime.now() >= entry.expires_at:
            logger.debug(f"[VI] {ticker} 만료 → NORMAL")
            del self._entries[ticker]
            return VIState.NORMAL
        return entry.state

    def is_vi_active(self, ticker: str) -> bool:
        return self.get_vi_state(ticker) != VIState.NORMAL

    def should_use_best_limit(self, ticker: str) -> bool:
        return self.get_vi_state(ticker) != VIState.NORMAL

    def update_from_tick(self, ticker: str, price: float, prev_close: float) -> None:
        # WS 1h가 이 종목을 관리 중이면 가격 휴리스틱 적용 안 함
        if ticker in self._ws_controlled:
            return
        if prev_close <= 0 or price <= 0:
            return
        limit_up_price = prev_close * _LIMIT_UP_MULTIPLIER
        if price >= limit_up_price * _LIMIT_UP_BUFFER:
            return
        change_pct = (price - prev_close) / prev_close
        if abs(change_pct) >= self._static_pct:
            existing = self._entries.get(ticker)
            if existing is not None and existing.state == VIState.SUSPECTED:
                # SUSPECTED는 주문 거부 기반 확정 신호 → 휴리스틱으로 강등 금지
                return
            now = datetime.now()
            # 이미 STATIC_VI 활성 중이면 만료 시각만 갱신, 로그 생략
            already_static = (
                existing is not None
                and now < existing.expires_at
                and existing.state == VIState.STATIC_VI
            )
            expires = now + self._assumed_duration
            self._entries[ticker] = _Entry(VIState.STATIC_VI, expires)
            if not already_static:
                logger.info(
                    f"[VI] {ticker} STATIC 추정 — change={change_pct * 100:+.2f}%, "
                    f"expires_at={expires:%H:%M:%S}"
                )

    def log_summary(self) -> None:
        """5분 주기 스케줄러에서 호출 — VI 활성 종목 수 요약 로그."""
        now = datetime.now()
        active = [e for e in self._entries.values() if now < e.expires_at]
        static_cnt = sum(1 for e in active if e.state == VIState.STATIC_VI)
        suspected_cnt = sum(1 for e in active if e.state == VIState.SUSPECTED)
        if static_cnt + suspected_cnt > 0:
            logger.info(f"[VI] 현재 {static_cnt}종목 STATIC_추정, {suspected_cnt}종목 SUSPECTED")

    def set_universe(self, universe: set[str]) -> None:
        """감시 종목 집합 갱신 — 이 종목만 VI-WS 이벤트를 INFO로 로깅."""
        self._universe = set(universe)

    def update_from_ws_vi(self, ticker: str, vi_data: dict) -> None:
        """WS '1h' VI 발동/해제 메시지로 VI 상태를 정확하게 갱신.

        가격 휴리스틱(update_from_tick)보다 우선 적용.
        vi_data 필드:
            "1225": VI적용구분 ("정적" / "동적" / "동적+정적" / "")
            "1224": VI해제시각 (HHMMSS)
        """
        self._ws_controlled.add(ticker)
        vi_type = str(vi_data.get("1225", ""))
        release_str = str(vi_data.get("1224", ""))

        # 감시 종목 여부 — _NX, _AL 접미사 제거 후 매칭
        base_ticker = ticker.split("_")[0]
        in_watch = not self._universe or base_ticker in self._universe

        if vi_type in ("정적", "동적", "동적+정적"):
            expires = self._parse_vi_release_time(release_str)
            self._entries[ticker] = _Entry(VIState.STATIC_VI, expires)
            msg = f"[VI-WS] {ticker} {vi_type} 발동 — 해제: {release_str or '미확인'}"
            if in_watch:
                logger.info(msg)
            else:
                logger.debug(msg)
        else:
            if ticker in self._entries:
                del self._entries[ticker]
            msg = f"[VI-WS] {ticker} VI 해제"
            if in_watch:
                logger.info(msg)
            else:
                logger.debug(msg)

    @staticmethod
    def _parse_vi_release_time(time_str: str) -> datetime:
        """HHMMSS 형식 VI 해제 시각 → datetime. 파싱 실패 시 현재 + 120초."""
        try:
            if len(time_str) == 6:
                h = int(time_str[:2])
                m = int(time_str[2:4])
                s = int(time_str[4:6])
                return datetime.now().replace(hour=h, minute=m, second=s, microsecond=0)
        except (ValueError, TypeError):
            pass
        return datetime.now() + timedelta(seconds=120)

    def flag_suspected(self, ticker: str, reason: str) -> None:
        expires = datetime.now() + self._suspected_duration
        self._entries[ticker] = _Entry(VIState.SUSPECTED, expires)
        logger.warning(f"[VI] {ticker} SUSPECTED — {reason}")
