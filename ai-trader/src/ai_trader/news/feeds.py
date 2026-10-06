"""Continuous news feeds.

A *feed* is the long-running transport for one source. Two implementations exist:

* :class:`PollingFeed` polls an HTTP provider on an interval. It works for RSS and for
  HTML listing pages.
* :class:`StreamingFeed` is the extension point for a provider that pushes items over a
  WebSocket, webhook, or SSE connection.

Both satisfy the same :class:`NewsFeed` contract, so :class:`~ai_trader.news.realtime.RealtimeNewsService`
and everything downstream of it are transport-agnostic. Adding a streaming provider must
not require any change to the trading workflow: the feed simply yields ``NewsItem``
objects, and dedup, clustering, AI analysis, and portfolio review are unchanged.

Backpressure lives one layer up. A feed never decides what to do with an item; it only
reports what it saw and whether the poll succeeded.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ai_trader.domain.news import NewsItem, NewsSource
from ai_trader.news.provider import NewsProvider, gather

logger = logging.getLogger(__name__)

ItemSink = Callable[[tuple[NewsItem, ...]], Awaitable[None]]

# Sources that must not be hammered. A poll interval below this is clamped.
MIN_POLL_SECONDS = 2.0


@dataclass
class FeedHealth:
    """Observable liveness for one feed, surfaced to the UI."""

    source_id: str
    status: str = "STARTING"
    last_success_at: datetime | None = None
    last_item_at: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    polls: int = 0
    items_seen: int = 0
    poll_interval_seconds: float = 10.0

    def as_dict(self, now: datetime | None = None) -> dict[str, object]:
        reference = now or datetime.now(UTC)
        return {
            "source_id": self.source_id,
            "status": self.status,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_item_at": self.last_item_at.isoformat() if self.last_item_at else None,
            "seconds_since_last_item": (
                round((reference - self.last_item_at).total_seconds(), 1)
                if self.last_item_at
                else None
            ),
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
            "polls": self.polls,
            "items_seen": self.items_seen,
            "poll_interval_seconds": self.poll_interval_seconds,
        }


class NewsFeed:
    """Transport contract for one source."""

    source_id: str

    async def run(self, on_items: ItemSink) -> None:
        """Yield batches of items until cancelled. Must not raise on upstream errors."""
        raise NotImplementedError

    def health(self) -> FeedHealth:
        raise NotImplementedError


@dataclass
class PollingFeed(NewsFeed):
    """Polls an HTTP provider on a fixed interval.

    On failure the interval backs off exponentially up to ``max_backoff`` so a broken
    upstream is retried gently instead of hammered. A success resets the backoff.
    """

    source: NewsSource
    provider: NewsProvider
    interval_seconds: float
    degraded_after: int = 2
    offline_after: int = 5
    initial_backoff: float = 2.0
    max_backoff: float = 300.0
    _health: FeedHealth = field(init=False)

    def __post_init__(self) -> None:
        self.source_id = self.source.source_id
        self.interval_seconds = max(MIN_POLL_SECONDS, float(self.interval_seconds))
        self._health = FeedHealth(
            source_id=self.source.source_id, poll_interval_seconds=self.interval_seconds
        )

    def health(self) -> FeedHealth:
        return self._health

    def _record_success(self, item_count: int) -> None:
        health = self._health
        health.polls += 1
        health.consecutive_failures = 0
        health.last_success_at = datetime.now(UTC)
        health.last_error = None
        health.status = "LIVE"
        if item_count:
            health.items_seen += item_count
            health.last_item_at = health.last_success_at

    def _record_failure(self, error: str) -> None:
        health = self._health
        health.polls += 1
        health.consecutive_failures += 1
        health.last_error = error
        health.status = (
            "OFFLINE" if health.consecutive_failures >= self.offline_after else "DEGRADED"
        )

    def _next_delay(self) -> float:
        """Base interval on success; exponential backoff on failure."""
        if self._health.consecutive_failures == 0:
            return self.interval_seconds
        backoff = self.initial_backoff * (2 ** (self._health.consecutive_failures - 1))
        return float(min(self.max_backoff, max(self.interval_seconds, backoff)))

    async def run(self, on_items: ItemSink) -> None:
        import httpx

        async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
            while True:
                result = await gather(self.provider, client)
                if result.ok:
                    self._record_success(result.item_count)
                else:
                    self._record_failure(result.error or "unknown provider error")
                    logger.warning(
                        "news feed %s failed (%d in a row): %s",
                        self.source.source_id,
                        self._health.consecutive_failures,
                        result.error,
                    )
                if result.items:
                    await on_items(result.items)
                await asyncio.sleep(self._next_delay())


@dataclass
class StreamingFeed(NewsFeed):
    """Extension point for a push-based provider (WebSocket, webhook, or SSE).

    Not used by any currently configured source: every enabled source is HTTP. It
    exists so a streaming NSE or BSE integration can be added by supplying ``stream``
    without touching the trading workflow, the analyser, or the review path.
    """

    source: NewsSource
    interval_seconds: float
    stream: Callable[[ItemSink], Awaitable[None]] | None = None
    _health: FeedHealth = field(init=False)

    def __post_init__(self) -> None:
        self.source_id = self.source.source_id
        self._health = FeedHealth(
            source_id=self.source.source_id, poll_interval_seconds=0.0, status="LIVE"
        )

    def health(self) -> FeedHealth:
        return self._health

    async def run(self, on_items: ItemSink) -> None:
        if self.stream is None:
            raise NotImplementedError(
                f"{self.source.source_id} is configured as streaming but supplies no transport"
            )
        await self.stream(on_items)
