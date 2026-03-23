"""tests/test_retry.py"""

import pytest
from core.retry import retry_async


@pytest.mark.asyncio
async def test_retry_succeeds_first_try():
    call_count = 0

    async def succeed():
        nonlocal call_count
        call_count += 1
        return "ok"

    result = await retry_async(succeed, max_retries=3, base_delay=0.01)
    assert result == "ok"
    assert call_count == 1


@pytest.mark.asyncio
async def test_retry_succeeds_after_failures():
    call_count = 0

    async def fail_twice():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("network error")
        return "ok"

    result = await retry_async(fail_twice, max_retries=3, base_delay=0.01)
    assert result == "ok"
    assert call_count == 3


@pytest.mark.asyncio
async def test_retry_exhausted_raises():
    async def always_fail():
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        await retry_async(always_fail, max_retries=2, base_delay=0.01)


@pytest.mark.asyncio
async def test_retry_respects_retry_after():
    call_count = 0

    class RateLimitError(Exception):
        def __init__(self, retry_after):
            self.retry_after = retry_after

    async def rate_limited():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RateLimitError(retry_after=0.05)
        return "ok"

    result = await retry_async(
        rate_limited, max_retries=3, base_delay=0.01,
        retry_after_attr="retry_after",
    )
    assert result == "ok"
    assert call_count == 2
