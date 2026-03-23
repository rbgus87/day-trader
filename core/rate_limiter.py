"""core/rate_limiter.py — 비동기 슬라이딩 윈도우 Rate Limiter."""

import asyncio
import time
from collections import deque


class AsyncRateLimiter:
    """초당 N회 요청 제한 (슬라이딩 윈도우)."""

    def __init__(self, max_calls: int = 5, period: float = 1.0):
        self._max_calls = max_calls
        self._period = period
        self._calls: deque[float] = deque()

    def can_call(self) -> bool:
        now = time.monotonic()
        self._purge(now)
        return len(self._calls) < self._max_calls

    async def wait(self) -> None:
        while True:
            now = time.monotonic()
            self._purge(now)
            if len(self._calls) < self._max_calls:
                self._calls.append(now)
                return
            sleep_time = self._calls[0] + self._period - now
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    def _purge(self, now: float) -> None:
        cutoff = now - self._period
        while self._calls and self._calls[0] <= cutoff:
            self._calls.popleft()
