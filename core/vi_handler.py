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


class VIState(Enum):
    NORMAL = "normal"
    STATIC_VI = "static_vi"
    SUSPECTED = "suspected"


@dataclass
class _Entry:
    state: VIState
    expires_at: datetime


class VIHandler:
    """VI 발동 추정 + 주문 전환 의사결정 (인메모리)."""

    def __init__(
        self,
        static_pct: float = 0.095,
        assumed_duration_sec: int = 150,
        suspected_duration_sec: int = 60,
    ):
        self._static_pct = static_pct
        self._assumed_duration = timedelta(seconds=assumed_duration_sec)
        self._suspected_duration = timedelta(seconds=suspected_duration_sec)
        self._entries: dict[str, _Entry] = {}

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
        if prev_close <= 0 or price <= 0:
            return
        limit_up_price = prev_close * 1.30
        if price >= limit_up_price * 0.99:
            return
        change_pct = (price - prev_close) / prev_close
        if abs(change_pct) >= self._static_pct:
            expires = datetime.now() + self._assumed_duration
            self._entries[ticker] = _Entry(VIState.STATIC_VI, expires)
            logger.info(
                f"[VI] {ticker} STATIC 추정 — change={change_pct * 100:+.2f}%, "
                f"expires_at={expires:%H:%M:%S}"
            )

    def update_from_ws_0a(self, ticker: str, payload: dict) -> None:
        """TODO: 키움 WS '0A'(기세) 메시지의 VI 발동 필드 확정 후 구현.
        실제 페이로드 샘플 수집 → 단위 테스트 추가 → 본문 작성."""
        pass

    def flag_suspected(self, ticker: str, reason: str) -> None:
        expires = datetime.now() + self._suspected_duration
        self._entries[ticker] = _Entry(VIState.SUSPECTED, expires)
        logger.warning(f"[VI] {ticker} SUSPECTED — {reason}")
