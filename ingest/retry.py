"""A retry decorator with exponential backoff.

Network calls fail for boring reasons: a timeout, a 503, a rate limit. Retrying a
few times with growing pauses turns most of those blips into nothing. Retrying is
only safe because the work being repeated is idempotent: asking Open-Meteo for
the same day twice returns the same data, and the database write is an upsert.
"""

from __future__ import annotations

import functools
import logging
import random
import time
from collections.abc import Callable
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

log = logging.getLogger(__name__)


def retry(
    times: int = 4,
    base_delay: float = 0.5,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Call the wrapped function up to `times` times, sleeping 0.5 s, 1 s, 2 s ... between tries.

    Only exceptions listed in `retry_on` are retried. Anything else (a bug, a
    400 Bad Request) is raised at once, because trying again cannot fix it.
    """

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        # functools.wraps copies fn's name and docstring onto the wrapper, so
        # logs, tracebacks and tools like Airflow show `fetch_daily`, not `wrapper`.
        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            for attempt in range(1, times + 1):
                try:
                    return fn(*args, **kwargs)
                except retry_on as exc:
                    if attempt == times:
                        raise
                    delay = base_delay * 2 ** (attempt - 1)
                    # A little random jitter stops many clients that failed together
                    # from all retrying at the same instant (a "thundering herd").
                    delay += random.uniform(0, delay * 0.1)
                    log.warning(
                        "%s failed (%s); attempt %d of %d, retrying in %.1fs",
                        fn.__name__,
                        exc,
                        attempt,
                        times,
                        delay,
                    )
                    time.sleep(delay)
            raise AssertionError("unreachable: the loop either returns or raises")

        return wrapper

    return decorator
