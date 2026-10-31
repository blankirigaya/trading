"""Continuous, session-scoped news ingestion.

One task per source, each on its own poll interval, each feeding a single bounded
analysis pipeline. Nothing here decides what to trade.

Lifecycle: :meth:`RealtimeNewsService.start` is called when the shadow session starts
and :meth:`stop` when it ends. Between them, news flows on its own.

Concurrency invariants:

* Market ingestion is never touched by this module. There is no shared queue with the
  FYERS feed, and news backpressure can only ever slow the news tasks.
* The remote AI is called through one bounded queue with a bounded semaphore
  (``NEWS_AI_MAX_CONCURRENT_REQUESTS``). When the queue is full the item *waits*; it is
  never silently discarded, so material news cannot be lost under load.
* Deduplication happens before the queue, so a headline repeated every five seconds
  costs one AI call, not one per poll.
"""

import asyncio
import contextlib
import logging
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ai_trader.config import Settings
from ai_trader.domain.news import NewsItem, NewsSource
from ai_trader.news.feeds import FeedHealth, NewsFeed, PollingFeed
from ai_trader.news.sources import build_provider

if TYPE_CHECKING:  # pragma: no cover - avoids a service/realtime import cycle
    from ai_trader.news.service import NewsIntelligence

logger = logging.getLogger(__name__)


@dataclass
class IngestOutcome:
    """What one batch of fetched items actually did."""

    received: int = 0
    new_items: int = 0
    duplicates: int = 0
    corrections: int = 0
    enqueued: int = 0
    # Only these are worth an AI call. Duplicates are deliberately excluded, which is
    # what makes dedup-before-analysis actually work.
    accepted: tuple[NewsItem, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "received": self.received,
            "new_items": self.new_items,
            "duplicates": self.duplicates,
            "corrections": self.corrections,
            "enqueued": self.enqueued,
        }


@dataclass
class RealtimeNewsService:
    """Runs every enabled source continuously for the life of a trading session."""

    settings: Settings
    intelligence: "NewsIntelligence"
    sources: list[NewsSource]
    emit: object = None
    feeds: dict[str, NewsFeed] = field(default_factory=dict)
    _tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    _running: bool = False
    _outcomes: dict[str, int] = field(default_factory=dict)
    # Source ids killed at runtime via set_source_enabled. Honoured by start()
    # so a toggle survives a service restart without rebuilding the session.
    _disabled: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.feeds = {source.source_id: self._build_feed(source) for source in self.sources}

    # --- construction -------------------------------------------------------
    def _build_feed(self, source: NewsSource) -> NewsFeed:
        """Pick the transport for a source.

        A source may declare a streaming transport in configuration; otherwise it is
        polled. Both produce identical `NewsItem` objects, so nothing downstream differs.
        """
        interval = self.settings.poll_interval_for(source.source_id)
        if getattr(source, "streaming", False):
            from ai_trader.news.feeds import StreamingFeed

            return StreamingFeed(source=source, interval_seconds=interval)
        return PollingFeed(
            source=source,
            provider=build_provider(source),
            interval_seconds=interval,
            degraded_after=self.settings.news_degraded_after_failures,
            offline_after=self.settings.news_offline_after_failures,
            initial_backoff=self.settings.news_error_backoff_initial_seconds,
            max_backoff=self.settings.news_error_backoff_max_seconds,
        )

    # --- lifecycle ----------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Start one polling task per enabled source. Idempotent."""
        if self._running:
            return
        self._running = True
        for source_id, feed in self.feeds.items():
            if source_id in self._disabled:
                continue
            self._tasks[source_id] = asyncio.create_task(
                self._supervise(feed), name=f"news-feed:{source_id}"
            )
        logger.info("news ingestion started for %d sources", len(self._tasks))
        self._emit(
            "SYSTEM",
            "news_ingestion_started",
            f"Continuous news ingestion started for {len(self._tasks)} sources",
        )

    async def stop(self) -> None:
        """Stop every feed cleanly. Safe to call when not running."""
        if not self._running and not self._tasks:
            return
        self._running = False
        for task in self._tasks.values():
            task.cancel()
        for task in self._tasks.values():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        logger.info("news ingestion stopped")
        self._emit("SYSTEM", "news_ingestion_stopped", "Continuous news ingestion stopped")

    def disable_sources(self, source_ids: Collection[str]) -> None:
        """Pre-mark sources as killed (e.g. from persisted controls at build).

        They stay registered so a later :meth:`set_source_enabled` can revive
        them, but :meth:`start` will not launch them.
        """
        self._disabled.update(source_ids)

    async def set_source_enabled(self, source_id: str, enabled: bool) -> bool:
        """Stop or (re)start one feed without touching the others.

        Returns True when the set of running feeds changed. Feeds stay
        registered, so re-enabling resumes the same feed object. No-op when
        the service itself is stopped: the next start() honours the flag
        via the sources list instead.
        """
        if source_id not in self.feeds:
            return False
        if enabled:
            self._disabled.discard(source_id)
        else:
            self._disabled.add(source_id)
        task = self._tasks.pop(source_id, None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if enabled and self._running:
            feed = self.feeds[source_id]
            self._tasks[source_id] = asyncio.create_task(
                self._supervise(feed), name=f"news-feed:{source_id}"
            )
        changed = (task is not None) != enabled
        if changed:
            self._emit(
                "SYSTEM",
                "news_source_toggled",
                f"News source {source_id} {'enabled' if enabled else 'disabled'}",
            )
        return changed

    async def _supervise(self, feed: NewsFeed) -> None:
        """Run a feed forever, restarting it if it dies unexpectedly."""
        while self._running:
            try:
                await feed.run(self._on_items)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a dead feed must not stop the others
                logger.exception("news feed %s crashed: %s", feed.source_id, exc)
                self._emit(
                    "ERROR",
                    "news_feed_error",
                    f"News feed {feed.source_id} error: {type(exc).__name__}",
                )
                await asyncio.sleep(self.settings.news_error_backoff_initial_seconds)

    # --- ingestion ----------------------------------------------------------
    async def _on_items(self, items: tuple[NewsItem, ...]) -> None:
        """Deduplicate, then enqueue only the genuinely new items for AI analysis."""
        outcome = self.intelligence.ingest_new_items(items)
        for key, value in outcome.as_dict().items():
            if isinstance(value, int):
                self._outcomes[key] = self._outcomes.get(key, 0) + value
        if not outcome.accepted:
            return
        # Awaiting here applies backpressure to this feed only. The queue is bounded
        # and the model server is serialised, so waiting is correct: dropping material
        # news would be worse than a slower feed.
        self._outcomes["enqueued"] = self._outcomes.get(
            "enqueued", 0
        ) + await self.intelligence.enqueue_for_analysis(outcome.accepted)

    def _emit(self, category: str, kind: str, message: str) -> None:
        if self.emit is not None:
            self.emit(category, kind, message)  # type: ignore[operator]

    # --- observability ------------------------------------------------------
    def provider_status(self) -> list[dict[str, object]]:
        now = datetime.now(UTC)
        statuses: list[dict[str, object]] = []
        for feed in sorted(self.feeds.values(), key=lambda item: item.source_id):
            health: FeedHealth = feed.health()
            if not self._running and health.status == "STARTING":
                health.status = "STOPPED"
            statuses.append(health.as_dict(now))
        return statuses

    def stats(self) -> dict[str, object]:
        return {
            "running": self._running,
            "sources": len(self.feeds),
            "queue_depth": self.intelligence.pipeline().queue.qsize(),
            "queue_capacity": self.intelligence.pipeline().queue.maxsize,
            "pipeline": self.intelligence.pipeline().stats.as_dict(),
            "totals": dict(self._outcomes),
        }
