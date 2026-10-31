"""Retry helpers for SQLite persistence.

WAL plus a 30s busy timeout absorbs normal contention, but a writer that still
gets ``database is locked`` (or a transient I/O hiccup) should be retried, not
turned into a lost trade, a lost decision record, or a dead review loop.
Configuration alone is not a durability guarantee.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

RETRY_DELAYS_SECONDS = (0.2, 0.5, 1.0)


async def with_sqlite_retry[T](description: str, call: Callable[..., T], *args: Any) -> T:
    """Run blocking DB work in a thread, retrying only transient SQLite errors."""
    from sqlalchemy.exc import OperationalError

    last: BaseException | None = None
    for attempt, delay in enumerate((0.0, *RETRY_DELAYS_SECONDS)):
        if attempt:
            await asyncio.sleep(delay)
        try:
            return await asyncio.to_thread(call, *args)
        except OperationalError as exc:
            last = exc
            logger.warning(
                "%s failed (attempt %d): transient SQLite error, retrying",
                description,
                attempt + 1,
            )
    assert last is not None
    logger.error("%s failed after retries: %s", description, last)
    raise last
