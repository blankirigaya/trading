"""Bridges the news pipeline to the portfolio review workflow.

News can *ask* for a review. It cannot produce one by itself, and it can never execute.
The chain is always:

    news -> AI news analyst -> (this module) -> Portfolio AI -> Jev -> final AI -> paper order

Two triggers:

* the news analyst set ``requires_portfolio_review``; or
* the news names a symbol the paper book currently holds, so an existing position is
  re-examined on new information.

The second trigger is deliberately based on *symbol overlap*, not on the words in the
headline. Deciding what to do about the news remains entirely with the AI.

The coordinator is a bounded, coalescing buffer. A burst of filings produces one review
carrying all of them rather than a burst of competing reviews, and the buffer is bounded
so a wedged portfolio AI cannot grow it without limit.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ai_trader.domain.news import NewsAnalysis, NewsItem
from ai_trader.news.review_context import NewsReviewBundle, build_bundle

logger = logging.getLogger(__name__)

LatencyFn = Callable[[str, str, datetime], None]
EmitFn = Callable[..., None]


@dataclass
class NewsReviewRequest:
    """One request for an immediate review, with the news that caused it."""

    item: NewsItem
    analysis: NewsAnalysis
    reason: str


@dataclass
class NewsReviewCoordinator:
    """Turns material news into an immediate portfolio review."""

    held_symbols: Callable[[], set[str]] | None = None
    recent_items: Callable[[], tuple[NewsItem, ...]] | None = None
    recent_analyses: Callable[[], tuple[NewsAnalysis, ...]] | None = None
    latency_sink: LatencyFn | None = None
    emit: EmitFn | None = None
    review_held_positions: bool = True
    max_queue: int = 20
    _pending: list[NewsReviewRequest] = field(default_factory=list)
    _ready: asyncio.Event = field(default_factory=asyncio.Event)
    _scheduled: int = 0
    _dropped: int = 0
    _stopping: bool = False

    # --- triggering ---------------------------------------------------------
    def should_review(self, item: NewsItem, analysis: NewsAnalysis) -> str | None:
        """Return why a review is warranted, or None if this news is not actionable.

        Returning the reason keeps the trigger auditable: there is no hidden rule.
        """
        if analysis.requires_portfolio_review:
            return "NEWS_AI_FLAGGED"
        if self.review_held_positions and self._concerns_held(item, analysis):
            return "NEWS_CONCERNS_HELD_POSITION"
        return None

    def _concerns_held(self, item: NewsItem, analysis: NewsAnalysis) -> bool:
        if self.held_symbols is None:
            return False
        held = {symbol.upper() for symbol in self.held_symbols()}
        if not held:
            return False
        named = {symbol.upper() for symbol in analysis.affected_symbols}
        named.update(symbol.upper() for symbol in item.symbols)
        return bool(named & held)

    def request(self, item: NewsItem, analysis: NewsAnalysis, reason: str) -> None:
        """Queue an immediate review, coalescing repeats of the same item."""
        if any(pending.item.item_id == item.item_id for pending in self._pending):
            return
        if len(self._pending) >= self.max_queue:
            self._dropped += 1
            logger.warning(
                "news review queue full (%d); not scheduling another review", self.max_queue
            )
            if self.emit is not None:
                self.emit(
                    "SYSTEM",
                    "news_review_queue_full",
                    "Portfolio review queue is full; this news will not trigger another review",
                )
            return
        self._pending.append(NewsReviewRequest(item=item, analysis=analysis, reason=reason))
        self._scheduled += 1
        if self.emit is not None:
            symbols = analysis.affected_symbols or item.symbols
            self.emit(
                "SYSTEM",
                "news_review_requested",
                f"Immediate portfolio review requested ({reason}) for"
                f" {', '.join(symbols) or 'the market'}",
                symbol=symbols[0] if symbols else None,
            )
        self._ready.set()

    # --- consumption --------------------------------------------------------
    async def wait_for_bundle(self) -> NewsReviewBundle | None:
        """Block until news warrants a review. Returns None once stopped.

        News already queued when this is called is returned immediately, so nothing is
        lost between the news task starting and the review task starting.
        """
        if self._pending:
            return self.next_bundle()
        await self._ready.wait()
        if self._stopping:
            return None
        return self.next_bundle()

    def next_bundle(self) -> NewsReviewBundle | None:
        """Take everything queued and build one review context.

        Coalescing matters: three filings arriving together should produce one review
        carrying all three, not three reviews racing each other for the paper book.
        """
        if not self._pending:
            return None
        trigger = tuple((request.item, request.analysis) for request in self._pending)
        bundle = build_bundle(
            trigger=trigger,
            recent_items=self.recent_items() if self.recent_items else (),
            recent_analyses=self.recent_analyses() if self.recent_analyses else (),
        )
        self._pending.clear()
        self._ready.clear()
        if bundle.trigger_item_ids and self.latency_sink is not None:
            self._mark_review_started(bundle)
        return bundle

    def _mark_review_started(self, bundle: NewsReviewBundle) -> None:
        assert self.latency_sink is not None
        now = datetime.now(UTC)
        for analysis in bundle.analyses:
            self.latency_sink("review_started", analysis.analysis_id, now)

    def stop(self) -> None:
        """Release any waiter so the consumer task can exit."""
        self._stopping = True
        self._ready.set()

    @property
    def pending(self) -> int:
        return len(self._pending)

    def stats(self) -> dict[str, int]:
        return {
            "scheduled": self._scheduled,
            "dropped_queue_full": self._dropped,
            "pending": self.pending,
        }
