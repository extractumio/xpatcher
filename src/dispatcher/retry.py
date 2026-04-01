"""Retry logic with exponential backoff."""

import time
from typing import Callable, TypeVar

T = TypeVar("T")


class RetryExhausted(Exception):
    pass


def retry_with_backoff(
    fn: Callable[[], T],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_exceptions: tuple = (Exception,),
) -> T:
    """Execute fn with exponential backoff on retryable exceptions."""
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except retryable_exceptions as e:
            last_error = e
            if attempt == max_retries:
                raise RetryExhausted(f"Failed after {max_retries + 1} attempts: {e}") from e
            delay = min(base_delay * (2 ** attempt), max_delay)
            time.sleep(delay)
    raise RetryExhausted(f"Failed: {last_error}")
