"""tests/test_rate_limiter.py"""

import asyncio
import time
import pytest

from core.rate_limiter import AsyncRateLimiter


@pytest.mark.asyncio
async def test_allows_within_limit():
    limiter = AsyncRateLimiter(max_calls=3, period=1.0)
    for _ in range(3):
        await limiter.wait()


@pytest.mark.asyncio
async def test_blocks_over_limit():
    limiter = AsyncRateLimiter(max_calls=2, period=0.5)
    start = time.monotonic()
    for _ in range(3):
        await limiter.wait()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.4


@pytest.mark.asyncio
async def test_can_call_check():
    limiter = AsyncRateLimiter(max_calls=1, period=1.0)
    assert limiter.can_call() is True
    await limiter.wait()
    assert limiter.can_call() is False
