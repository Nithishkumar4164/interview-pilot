"""One retry for transient provider errors, on top of FailoverManager's primary -> fallback.

Only timeouts, connection errors, rate limits and 5xx are retried; a
deterministic failure (bad request, unparseable output) would just fail again
on both providers.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import anthropic
import openai

T = TypeVar("T")

TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    TimeoutError,
    ConnectionError,
    anthropic.APITimeoutError,
    anthropic.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,
)


async def retry_transient(call: Callable[[], Awaitable[T]], attempts: int = 2, backoff_s: float = 0.5) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except TRANSIENT_ERRORS:
            if attempt == attempts:
                raise
            await asyncio.sleep(backoff_s * attempt)
    raise AssertionError("unreachable")
