"""News intelligence service: poll, deduplicate, analyse, and request review.

The service never executes an order. It produces news material, AI analysis, and
(at most) a request for the normal portfolio review workflow.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx

from ai_trader.ai.base import AIProvider
from ai_trader.config import Settings
from ai_trader.domain.news import (
    AnalysisStatus,
    NewsAnalysis,
    NewsDocument,
    NewsItem,
    NewsPortfolioReviewContext,
)
from ai_trader.news.analyst import (
    PROMPT_NAME,
    PROMPT_VERSION,
    NewsAnalysisResponse,
    build_user_prompt,
    failed_analysis,
    load_system_prompt,
    to_analysis,
    to_review_context,
)
from ai_trader.news.clustering import NewsClusterIndex, similar_headline
from ai_trader.news.dedup import normalize_content_for_hash
from ai_trader.news.documents import download_document, is_trusted_source
from ai_trader.news.pipeline import NewsPipeline, default_review_gate, poll_sources
from ai_trader.news.provider import ProviderResult

if TYPE_CHECKING:  # pragma: no cover - import cycle broken for type checking only
    from ai_trader.news.realtime import IngestOutcome
from ai_trader.news.sources import all_sources, source_by_id
from ai_trader.persistence.news_repository import NewsRepository

EmitFn = Callable[..., None]
ReviewFn = Callable[["NewsItem", "NewsAnalysis"], Awaitable[None]]
ContextReviewFn = Callable[
    ["NewsItem", "NewsAnalysis", "NewsPortfolioReviewContext"], Awaitable[None]
]


@dataclass
class PollSummary:
    received: int = 0
    stored: int = 0
    duplicates: int = 0
    clusters: int = 0
    enqueued: int = 0
    provider_results: list[ProviderResult] = field(default_factory=list)
    documents: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "received": self.received,
            "stored": self.stored,
            "duplicates": self.duplicates,
            "clusters": self.clusters,
            "enqueued": self.enqueued,
            "documents": self.documents,
            "providers": [
                {
                    "source_id": result.source_id,
                    "ok": result.ok,
                    "items": result.item_count,
                    "error": result.error,
                }
                for result in self.provider_results
            ],
        }


class NewsIntelligence:
    """Owns the news store, cluster index, and analysis pipeline."""

    def __init__(
        self,
        settings: Settings,
        repository: NewsRepository,
        ai_provider: AIProvider | None = None,
        emit: EmitFn | None = None,
        hydrate: bool = True,
        on_review: ContextReviewFn | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.ai_provider = ai_provider
        self._emit = emit or _noop_emit
        # Operator kill switch (persisted control, applied at runtime). When
        # False, headlines still ingest and deduplicate, but no AI analysis
        # runs and no news-triggered reviews can happen.
        self.analysis_enabled = True
        self.index = NewsClusterIndex(settings.news_headline_similarity_threshold)
        self.items: dict[str, NewsItem] = {}
        self.analyses: dict[str, NewsAnalysis] = {}
        self._market_context: str | None = None
        self._review_handler = on_review
        self._pipeline: NewsPipeline | None = None
        # Widens the default trigger to include news about a held symbol.
        self._review_gate: Callable[[NewsItem, NewsAnalysis], bool] | None = None
        for source in all_sources():
            self.repository.upsert_source(source)
        if hydrate:
            self.hydrate()

    def set_review_handler(self, handler: ContextReviewFn) -> None:
        """Install the portfolio-review sink (the live workflow in the running system)."""
        self._review_handler = handler

    def set_review_gate(self, gate: Callable[[NewsItem, NewsAnalysis], bool] | None) -> None:
        """Widen or replace what counts as worth a portfolio review."""
        self._review_gate = gate
        if self._pipeline is not None:
            self._pipeline.review_gate = gate or default_review_gate

    def pipeline(self) -> NewsPipeline:
        """The shared analysis pipeline. Created on first use so the queue is bound
        to a running event loop."""
        if self._pipeline is None:
            self._pipeline = NewsPipeline(
                analyse=self.analyse_item,
                on_review=self._handle_review,
                emit=self._emit,
                queue_capacity=self.settings.news_queue_capacity,
                max_concurrent_analyses=self.settings.news_ai_max_concurrent_requests,
                review_gate=self._review_gate,
            )
        return self._pipeline

    async def _handle_review(self, item: NewsItem, analysis: NewsAnalysis) -> None:
        """Record that a review was requested, then hand off to the real workflow."""
        self.repository.mark_reviewed(analysis.analysis_id)
        if self._review_handler is None:
            return
        await self._review_handler(item, analysis, self.review_context(item, analysis))

    def hydrate(self, limit: int = 500) -> None:
        """Reload stored items so a fresh process can analyse previously polled news.

        Rebuilds the cluster index deterministically, so cluster ids stay stable.
        """
        stored = self.repository.recent_items(limit=limit)
        if not stored:
            return
        self.index.rebuild(stored)
        for item in stored:
            self.items[item.item_id] = item

    # --- market context ----------------------------------------------------
    def set_market_context(self, context: str | None) -> None:
        """Optional market snapshot text handed to the analyst. Never a signal."""
        self._market_context = context

    # --- ingestion ---------------------------------------------------------
    async def poll(self, *, timeout: int = 25, analyse: bool = False) -> PollSummary:
        """Fetch every enabled source and store what is new.

        ``analyse=True`` also submits the new items to the pipeline and waits for it to
        drain, which is what a one-shot command wants. The default only ingests, so a
        scheduled poll can never block on the remote model.
        """
        summary = PollSummary()
        results = await poll_sources([s for s in all_sources() if s.enabled], timeout=timeout)
        summary.provider_results = results
        pending: list[NewsItem] = []
        for result in results:
            for item in result.items[: self.settings.news_max_items_per_poll]:
                summary.received += 1
                self._emit("MARKET", "news_received", f"{item.source_name}: {item.headline[:140]}")
                classification, stored = self._ingest(item, summary)
                if classification in ("new", "correction"):
                    pending.append(stored)
        summary.clusters = len(self.index)
        await self._analyse_pending(pending, summary, analyse=analyse)
        return summary

    async def _analyse_pending(
        self, items: list[NewsItem], summary: PollSummary, *, analyse: bool
    ) -> None:
        pipeline = self.pipeline()
        for item in items:
            if pipeline.submit(item):
                summary.enqueued += 1
        if not analyse or not items:
            return
        pipeline.start()
        await pipeline.drain()

    def ingest_items(self, items: list[NewsItem]) -> PollSummary:
        """Ingest already-parsed items (replay, tests, fixtures)."""
        summary = PollSummary()
        for item in items:
            summary.received += 1
            self._emit("MARKET", "news_received", f"{item.source_name}: {item.headline[:140]}")
            classification, stored = self._ingest(item, summary)
            if classification in ("new", "correction"):
                self._submit_for_analysis(stored, summary)
        summary.clusters = len(self.index)
        return summary

    # --- continuous ingestion ---------------------------------------------
    def ingest_new_items(self, items: tuple[NewsItem, ...] | list[NewsItem]) -> "IngestOutcome":
        """Store a fetched batch, classifying each item.

        This is the deduplication gate for continuous ingestion. It runs *before* the
        analysis queue, so a headline repeated on every poll costs no AI call. It
        returns which items are worth analysing; nothing is enqueued here.
        """
        from ai_trader.news.realtime import IngestOutcome

        outcome = IngestOutcome()
        summary = PollSummary()
        accepted: list[NewsItem] = []
        for item in items:
            outcome.received += 1
            classification, stored = self._ingest(item, summary, emit_received=True)
            if classification in ("new", "correction"):
                outcome.new_items += 1
                if classification == "correction":
                    outcome.corrections += 1
                accepted.append(stored)
            else:
                outcome.duplicates += 1
        outcome.accepted = tuple(accepted)
        return outcome

    async def enqueue_for_analysis(self, items: tuple[NewsItem, ...] | list[NewsItem]) -> int:
        """Queue items for immediate AI analysis, applying backpressure.

        Unlike :meth:`NewsPipeline.submit`, a full queue makes the caller *wait* for
        space instead of dropping the item. The caller is a news feed task, never the
        market-data task, so waiting cannot stall price ingestion.
        """
        queued = 0
        pipeline = self.pipeline()
        for item in items:
            stored = self.items.get(item.item_id, item)
            if not self.analysis_enabled:
                continue
            if await pipeline.enqueue(stored):
                queued += 1
        return queued

    def _submit_for_analysis(self, item: NewsItem, summary: PollSummary) -> None:
        if self.pipeline().submit(item):
            summary.enqueued += 1

    def _ingest(self, item: NewsItem, summary: PollSummary, *, emit_received: bool = False) -> tuple[str, NewsItem]:
        """Deduplicate and persist one item. Returns (new / duplicate / correction, stored item).

        A publisher correction is a *new* item: same source and canonical URL, different
        content. It is stored as a new version so the original evidence survives, and it
        is eligible for re-analysis because the information genuinely changed. The
        stored copy is returned because a correction is filed under a derived id.
        """
        previous = self.repository.latest_version_for(item.source_id, item.canonical_url)
        correction_of = None
        if previous is not None and previous.content_hash != item.content_hash:
            if _is_repoll_jitter(previous, item):
                # Same story re-polled with publisher-side jitter (alternating
                # headline variants, volatile timestamps): not a correction,
                # so it must not cost another AI analysis.
                summary.duplicates += 1
                if emit_received:
                    self._emit(
                        "SYSTEM",
                        "news_deduplicated",
                        f"Re-poll of known story without new information "
                        f"({item.source_name}); not re-analysed",
                        symbol=item.symbols[0] if item.symbols else None,
                    )
                return "duplicate", self.items.get(previous.item_id, previous)
            from ai_trader.news.dedup import content_hash_for

            correction_of = previous.item_id
            next_version = previous.version + 1
            # The incoming id is derived from the headline, so a revision that keeps
            # the same headline would collide with the original row. Deriving the new id
            # from the content keeps it stable across re-polls while making the revision
            # a distinct, auditable row.
            item = item.model_copy(
                update={
                    "item_id": content_hash_for(
                        f"{item.source_id}|{item.canonical_url}|{item.content_hash}|{next_version}"
                    )[:32],
                    "version": next_version,
                    "supersedes_item_id": previous.item_id,
                }
            )

        cluster, is_duplicate = self.index.add(item, item.received_at)
        self.repository.save_cluster(cluster)
        self.repository.add_cluster_member(
            cluster.cluster_id,
            item.item_id,
            item.source_id,
            item.item_id == cluster.representative_item_id,
        )
        stored = self.repository.save_item(item, cluster.cluster_id)
        self.items[item.item_id] = item.model_copy(update={"cluster_id": cluster.cluster_id})

        if not stored:
            summary.duplicates += 1
            if emit_received:
                self._emit(
                    "SYSTEM",
                    "news_deduplicated",
                    f"Duplicate of {previous.headline[:100] if previous else item.headline[:100]}"
                    f" from {item.source_name}; not re-analysed",
                )
            return "duplicate", item

        summary.stored += 1
        if emit_received:
            self._emit(
                "MARKET",
                "news_received",
                f"{item.source_name}: {item.headline[:140]}",
                symbol=item.symbols[0] if item.symbols else None,
            )
        if correction_of is not None:
            self._emit(
                "SYSTEM",
                "news_correction_received",
                f"{item.source_name} revised an item (v{item.version}); re-analysing",
                symbol=item.symbols[0] if item.symbols else None,
            )
            return "correction", item
        if is_duplicate:
            summary.duplicates += 1
            self._emit(
                "SYSTEM",
                "news_deduplicated",
                f"Duplicate report grouped into {cluster.cluster_id}"
                f" ({len(cluster.member_item_ids)} sources)",
            )
            return "duplicate", item
        return "new", item

    # --- analysis ----------------------------------------------------------
    async def analyse_item(
        self, item: NewsItem, started_at: datetime | None = None
    ) -> NewsAnalysis:
        cluster_id = item.cluster_id or ""
        started = started_at or datetime.now(UTC)
        if not self.analysis_enabled:
            analysis = failed_analysis(item, cluster_id, "news analysis disabled", started)
            self.repository.save_analysis(analysis)
            self.analyses[analysis.analysis_id] = analysis
            return analysis
        if self.ai_provider is None:
            analysis = failed_analysis(
                item, cluster_id, "remote AI provider not configured", started
            )
            self.repository.save_analysis(analysis)
            self.analyses[analysis.analysis_id] = analysis
            return analysis
        related = self._related_headlines(item)
        prompt = build_user_prompt(
            item,
            related_headlines=related,
            market_context=self._market_context,
        )
        try:
            response, metadata = await self.ai_provider.structured_completion(
                load_system_prompt(),
                prompt,
                NewsAnalysisResponse,
                prompt_name=PROMPT_NAME,
                prompt_version=PROMPT_VERSION,
            )
        except Exception as exc:  # noqa: BLE001 - fail closed, never invent an interpretation
            analysis = failed_analysis(item, cluster_id, f"{type(exc).__name__}: {exc}", started)
            self.repository.save_analysis(analysis)
            self.analyses[analysis.analysis_id] = analysis
            return analysis
        analysis = to_analysis(
            response,
            item=item,
            cluster_id=cluster_id,
            started_at=started,
            completed_at=datetime.now(UTC),
            metadata=metadata,
        )
        self.repository.save_analysis(analysis)
        self.analyses[analysis.analysis_id] = analysis
        return analysis

    def _related_headlines(self, item: NewsItem) -> tuple[str, ...]:
        if not item.cluster_id:
            return ()
        members = self.repository.cluster_members(item.cluster_id)
        return tuple(self.items[member].headline for member in members if member in self.items)

    async def analyse_latest(self, limit: int = 5) -> list[NewsAnalysis]:
        """Analyse the most recent not-yet-analysed items, newest first.

        Items that already have a completed or failed analysis are skipped so a
        repeated command does not re-bill the remote model.
        """
        pending: list[NewsItem] = []
        for item in sorted(self.items.values(), key=lambda entry: entry.published_at, reverse=True):
            existing = self.repository.latest_analysis_for_item(item.item_id)
            if existing is not None and existing.status.value in ("COMPLETED", "FAILED"):
                continue
            pending.append(item)
            if len(pending) >= limit:
                break
        return [await self.analyse_item(item) for item in pending]

    # --- documents ---------------------------------------------------------
    async def attach_documents(
        self, client: httpx.AsyncClient, item: NewsItem
    ) -> list[NewsDocument]:
        """Fetch linked documents for trusted sources only."""
        if not self.settings.news_documents_enabled or not is_trusted_source(item):
            return []
        from pathlib import Path

        documents: list[NewsDocument] = []
        try:
            response = await client.get(item.canonical_url)
        except httpx.HTTPError:
            return []
        if response.status_code >= 400:
            return []
        from ai_trader.news.documents import discover_document_urls

        urls = discover_document_urls(
            item, response.text, self.settings.news_document_allowed_hosts
        )
        for url in urls[:3]:
            try:
                document = await download_document(
                    client,
                    url,
                    self.settings.news_document_allowed_hosts,
                    Path(self.settings.news_document_storage),
                )
            except Exception:  # noqa: BLE001 - a failed document must not fail the item
                continue
            self.repository.save_document(document, item.item_id)
            documents.append(document)
        return documents

    # --- context -----------------------------------------------------------
    def review_context(self, item: NewsItem, analysis: NewsAnalysis) -> NewsPortfolioReviewContext:
        members = len(self.repository.cluster_members(analysis.cluster_id))
        return to_review_context(analysis, item, related_count=max(1, members))

    def analyses_needing_review(self) -> list[tuple[NewsItem, NewsAnalysis]]:
        """Completed analyses that asked for a review and have not had one."""
        out: list[tuple[NewsItem, NewsAnalysis]] = []
        for analysis in self.repository.recent_analyses(limit=200):
            if analysis.status is not AnalysisStatus.COMPLETED:
                continue
            if not analysis.requires_portfolio_review or analysis.portfolio_review_at is not None:
                continue
            item = self.repository.get_item(analysis.item_id)
            if item is not None:
                out.append((item, analysis))
        return out


def _noop_emit(category: str, kind: str, message: str, *, symbol: str | None = None) -> None:
    """No-op emitter used by CLI commands that print their own summary."""


# Headline similarity required to treat a same-URL re-poll as jitter rather
# than a correction. Deliberately stricter than the clustering threshold: this
# gate suppresses AI analysis, so near-identity is required.
_JITTER_SIMILARITY = 0.8


def _is_repoll_jitter(previous: NewsItem, item: NewsItem) -> bool:
    """True when a re-polled item carries no new information.

    Publisher feeds flip headline variants ("a slice of" vs "slice of") and
    refresh volatile fragments (timestamps, "updated" stamps) every poll. When
    the headline is near-identical and the normalized content matches, this is
    the same story re-polled — not a correction — and must not trigger another
    AI analysis.
    """
    if not similar_headline(previous.headline, item.headline, _JITTER_SIMILARITY):
        return False
    prev_text = previous.content or previous.excerpt or ""
    new_text = item.content or item.excerpt or ""
    return normalize_content_for_hash(prev_text) == normalize_content_for_hash(new_text)


__all__ = ["IngestOutcome", "NewsIntelligence", "PollSummary", "source_by_id"]
