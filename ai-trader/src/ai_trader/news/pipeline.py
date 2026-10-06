"""News pipeline with explicit backpressure.

Ingestion must never block market data, and analysis must never block ingestion.
Two bounded queues and a bounded analysis semaphore provide that separation. When
the queues are full, items are dropped with a recorded reason rather than growing
without limit or blocking the caller.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ai_trader.domain.news import NewsAnalysis, NewsItem, NewsSource
from ai_trader.news.provider import ProviderResult as ProviderResult

__all__ = [
    "NewsPipeline",
    "PipelineStats",
    "ProviderResult",
    "poll_sources",
]

EmitFn = Callable[[str, str, str], None]
ReviewFn = Callable[[NewsItem, NewsAnalysis], Awaitable[None]]


def default_review_gate(item: NewsItem, analysis: NewsAnalysis) -> bool:
    """Only the news analyst's own flag triggers a review by default."""
    return analysis.requires_portfolio_review


@dataclass
class PipelineStats:
    enqueued: int = 0
    dropped_queue_full: int = 0
    analysed: int = 0
    analysis_failed: int = 0
    reviews_requested: int = 0
    reviews_suppressed_duplicate: int = 0
    dropped_queue_items: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "enqueued": self.enqueued,
            "dropped_queue_full": self.dropped_queue_full,
            "analysed": self.analysed,
            "analysis_failed": self.analysis_failed,
            "reviews_requested": self.reviews_requested,
            "reviews_suppressed_duplicate": self.reviews_suppressed_duplicate,
        }


class NewsPipeline:
    """Decouples ingestion from analysis and from portfolio review.

    The analysis worker is the only component that calls the remote AI, and the
    semaphore bounds concurrent requests to the single model server.

    ``review_gate`` decides whether a completed analysis is worth a portfolio review.
    It defaults to the news analyst's own ``requires_portfolio_review`` flag; the live
    session widens it to also cover news about a currently held symbol. Keeping the
    decision here means cluster-level review deduplication applies to every trigger.
    """

    def __init__(
        self,
        *,
        analyse: Callable[[NewsItem, datetime], Awaitable[NewsAnalysis]],
        on_review: ReviewFn,
        emit: EmitFn | None = None,
        queue_capacity: int = 200,
        max_concurrent_analyses: int = 1,
        review_gate: Callable[[NewsItem, NewsAnalysis], bool] | None = None,
    ) -> None:
        self.analyse = analyse
        self.on_review = on_review
        self.emit = emit or (lambda *_: None)
        self.queue: asyncio.Queue[NewsItem] = asyncio.Queue(maxsize=max(1, queue_capacity))
        self.semaphore = asyncio.Semaphore(max(1, max_concurrent_analyses))
        self.review_gate = review_gate or default_review_gate
        self.stats = PipelineStats()
        self._worker: asyncio.Task[None] | None = None
        self._stopping = False
        # One review per cluster; a duplicate story must not trigger a second review.
        self._reviewed_clusters: set[str] = set()

    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopping = True
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._worker = None

    def submit(self, item: NewsItem) -> bool:
        """Enqueue for analysis. Returns False when the queue is full.

        Never blocks the caller, so market ingestion cannot be held up by news. Used by
        one-shot commands; continuous ingestion uses :meth:`enqueue`.
        """
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            self.stats.dropped_queue_full += 1
            self.stats.dropped_queue_items.append(item.item_id)
            self.emit(
                "SYSTEM",
                "news_dropped_backpressure",
                f"Dropped {item.source_name} item: analysis queue full",
            )
            return False
        self.stats.enqueued += 1
        return True

    async def enqueue(self, item: NewsItem) -> bool:
        """Enqueue, waiting for room rather than discarding the item.

        The queue is bounded so an unavailable model cannot cause unbounded memory
        growth, but waiting is better than losing material news: a slow model should
        delay the news feed, never drop a filing. Callers are news feed tasks, never
        the market-data task.
        """
        if self._stopping:
            return False
        try:
            await self.queue.put(item)
        except asyncio.CancelledError:
            raise
        self.stats.enqueued += 1
        return True

    async def drain(self) -> None:
        """Process everything currently queued, then return.

        Unlike the long-running worker this must terminate once the queue is empty,
        so one-shot commands and tests do not hang.
        """
        while not self.queue.empty():
            await self._process(self.queue.get_nowait())

    async def _run(self) -> None:
        while True:
            if self._stopping:
                return
            try:
                item = await asyncio.wait_for(self.queue.get(), timeout=0.2)
            except TimeoutError:
                if self._stopping:
                    return
                continue
            await self._process(item)

    async def _process(self, item: NewsItem) -> None:
        self.emit(
            "AI", "news_analysis_started", f"Analysing {item.source_name}: {item.headline[:120]}"
        )
        async with self.semaphore:
            try:
                analysis = await self.analyse(item, datetime.now(UTC))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a failed analysis must not kill the worker
                self.stats.analysis_failed += 1
                self.emit(
                    "ERROR",
                    "news_analysis_failed",
                    f"News analysis failed for {item.item_id}: {type(exc).__name__}",
                )
                return

        if analysis.status.value != "COMPLETED":
            self.stats.analysis_failed += 1
            self.emit(
                "ERROR",
                "news_analysis_failed",
                f"News analysis incomplete: {analysis.failure_reason or analysis.status.value}",
            )
            return

        self.stats.analysed += 1
        self.emit(
            "AI",
            "news_analysis_completed",
            f"{item.source_name}: {analysis.materiality.value if analysis.materiality else 'UNCLEAR'}"
            f" / {analysis.direction.value if analysis.direction else 'UNCLEAR'}",
        )

        if not self.review_gate(item, analysis):
            return
        if analysis.cluster_id in self._reviewed_clusters:
            self.stats.reviews_suppressed_duplicate += 1
            self.emit(
                "SYSTEM",
                "news_review_suppressed",
                f"Cluster {analysis.cluster_id} already reviewed",
            )
            return
        self._reviewed_clusters.add(analysis.cluster_id)
        self.stats.reviews_requested += 1
        self.emit(
            "SYSTEM",
            "news_portfolio_review_requested",
            f"Portfolio review requested for {item.symbols or 'market'}",
        )
        try:
            await self.on_review(item, analysis)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.emit(
                "ERROR",
                "provider_error",
                f"News-triggered portfolio review failed: {type(exc).__name__}",
            )


async def poll_sources(sources: list[NewsSource], *, timeout: int = 25) -> list[ProviderResult]:
    """Poll every configured provider concurrently and return raw results."""
    import httpx

    from ai_trader.news.provider import gather
    from ai_trader.news.sources import build_provider

    providers = [build_provider(source, timeout=timeout) for source in sources]
    async with httpx.AsyncClient(follow_redirects=True) as client:
        return list(await asyncio.gather(*(gather(p, client) for p in providers)))


__all__ = ["NewsPipeline", "PipelineStats", "ProviderResult", "default_review_gate", "poll_sources"]
