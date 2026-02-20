import functools
import time
from collections.abc import Callable, Coroutine
from typing import Any, ParamSpec, TypeVar

from src.utils.logging import get_logger

logger = get_logger("llm.metrics")

P = ParamSpec("P")
T = TypeVar("T")


def llm_logged(func: Callable[P, Coroutine[Any, Any, T]]) -> Callable[P, Coroutine[Any, Any, T]]:
    """Decorator that logs LLM call metadata: provider, task, latency, tokens."""

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        start = time.monotonic()
        try:
            result = await func(*args, **kwargs)
            latency_ms = (time.monotonic() - start) * 1000
            logger.info(
                "llm_call",
                function=func.__name__,
                latency_ms=round(latency_ms, 1),
                success=True,
            )
            return result
        except Exception as e:
            latency_ms = (time.monotonic() - start) * 1000
            logger.error(
                "llm_call",
                function=func.__name__,
                latency_ms=round(latency_ms, 1),
                success=False,
                error=str(e),
            )
            raise

    return wrapper
