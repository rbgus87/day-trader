"""core/retry.py — Exponential Backoff + Jitter 재시도."""

import asyncio
import random
from typing import Callable, Any

from loguru import logger


async def retry_async(
    func: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retry_after_attr: str | None = None,
    **kwargs: Any,
) -> Any:
    """비동기 함수를 재시도한다.

    Args:
        func: 재시도할 비동기 함수
        max_retries: 최대 재시도 횟수
        base_delay: 기본 대기 시간 (초)
        max_delay: 최대 대기 시간 (초)
        retry_after_attr: 예외에서 대기 시간을 읽을 속성명
    """
    last_exception = None

    for attempt in range(1, max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            if attempt == max_retries:
                logger.error(f"재시도 소진 ({max_retries}회): {e}")
                raise

            # Retry-After 헤더 우선
            delay = base_delay
            if retry_after_attr and hasattr(e, retry_after_attr):
                delay = float(getattr(e, retry_after_attr))
            else:
                # Exponential backoff + jitter
                delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                delay += random.uniform(0, delay * 0.1)

            logger.warning(f"재시도 {attempt}/{max_retries} — {delay:.2f}초 후 ({e})")
            await asyncio.sleep(delay)

    raise last_exception
