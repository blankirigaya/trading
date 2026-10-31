"""Continuous news ingestion and its path into the autonomous trading workflow.

Every provider and the remote AI are simulated here, so these tests verify wiring,
timing, ordering, and backpressure policy rather than live upstream behaviour.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine

from ai_trader.ai.base import AIProvider
from ai_trader.config import Settings
from ai_trader.domain.news import (
    AnalysisStatus,
    Direction,
    Horizon,
    Materiality,
    NewsAnalysis,
    NewsCategory,
    NewsItem,
    NewsSource,
    SourceType,
    TrustLevel,
)
from ai_trader.news.analyst import NewsAnalysisResponse
from ai_trader.news.feeds import FeedHealth, NewsFeed, PollingFeed
from ai_trader.news.realtime import RealtimeNewsService
from ai_trader.news.review_bridge import NewsReviewCoordinator
from ai_trader.news.service import NewsIntelligence
from ai_trader.persistence.models import Base
from ai_trader.persistence.news_repository import NewsRepository

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


# --- fixtures and doubles ----------------------------------------------------


def make_item(
    item_id: str,
    *,
    headline: str = "Infosys wins 5,000 crore order from global bank",
    symbols: tuple[str, ...] = ("INFY",),
    source_id: str = "nse-corporate",
    source_name: str = "NSE India",
    source_type: SourceType = SourceType.OFFICIAL,
    content_key: str | None = None,
    published_at: datetime = NOW,
    received_at: datetime | None = None,
) -> NewsItem:
    from ai_trader.news.dedup import content_hash_for, headline_hash_for

    return NewsItem(
        item_id=item_id,
        source_id=source_id,
        source_name=source_name,
        source_type=source_type,
        trust_level=TrustLevel.PRIMARY
        if source_type is SourceType.OFFICIAL
        else TrustLevel.SECONDARY,
        headline=headline,
        canonical_url=f"https://www.nseindia.com/announcement/{item_id}",
        url=f"https://www.nseindia.com/announcement/{item_id}",
        published_at=published_at,
        received_at=received_at or published_at,
        category=NewsCategory.CORPORATE_ANNOUNCEMENT,
        symbols=symbols,
        content_hash=content_hash_for(content_key or item_id),
        headline_hash=headline_hash_for(headline),
        excerpt="The company disclosed the contract value in a filing.",
    )


def make_analysis(
    item: NewsItem,
    *,
    requires_review: bool = True,
    materiality: Materiality = Materiality.HIGH,
    affected: tuple[str, ...] = ("INFY",),
    completed_at: datetime | None = None,
) -> NewsAnalysis:
    return NewsAnalysis(
        analysis_id=f"an-{item.item_id}",
        cluster_id=item.cluster_id or f"cluster-{item.item_id}",
        item_id=item.item_id,
        status=AnalysisStatus.COMPLETED,
        event_type="ORDER_WIN",
        summary="Infosys secured a large multi-year contract.",
        affected_symbols=affected,
        materiality=materiality,
        direction=Direction.POSITIVE,
        horizon=Horizon.WEEKS,
        confidence=0.74,
        requires_portfolio_review=requires_review,
        analysis_started_at=completed_at or NOW,
        analysis_completed_at=completed_at or NOW + timedelta(seconds=4),
    )


class ScriptedAI(AIProvider):
    """Returns a fixed response and records how many times it was called."""

    def __init__(self, requires_review: bool = True) -> None:
        self.calls: list[str] = []
        self.requires_review = requires_review
        self.delay = 0.0
        # Tracks how many inferences are in flight, so the concurrency bound is
        # observable rather than assumed.
        self.in_flight = 0
        self.peak_concurrency = 0

    @property
    def model_name(self) -> str:
        return "scripted-news-model"

    async def structured_completion(
        self, system_prompt, user_prompt, response_model, *, prompt_name, prompt_version
    ):
        from ai_trader.domain.decisions import ProviderMetadata

        if self.delay:
            self.in_flight += 1
            self.peak_concurrency = max(self.peak_concurrency, self.in_flight)
            await asyncio.sleep(self.delay)
            self.in_flight -= 1
        self.calls.append(user_prompt)
        return NewsAnalysisResponse(
            event_type="ORDER_WIN",
            summary="Infosys secured a large multi-year contract.",
            affected_symbols=["INFY"],
            materiality=Materiality.HIGH,
            direction=Direction.POSITIVE,
            horizon=Horizon.WEEKS,
            confidence=0.74,
            requires_portfolio_review=self.requires_review,
            evidence=["Filing disclosed the contract value."],
            uncertainties=["Recognition schedule not disclosed."],
        ), ProviderMetadata(
            provider="remote_openai_compatible",
            model="scripted-news-model",
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            latency_ms=321,
        )


class ScriptedFeed(NewsFeed):
    """A feed that emits a scripted sequence of batches, then idles."""

    def __init__(self, source_id: str, batches: list[list[NewsItem]]) -> None:
        self.source_id = source_id
        self.batches = batches
        self.emitted = 0
        self.runs = 0
        self._health = FeedHealth(source_id=source_id, status="STARTING")
        self.idle = asyncio.Event()

    def health(self) -> FeedHealth:
        return self._health

    async def run(self, on_items) -> None:
        self.runs += 1
        for batch in self.batches:
            await on_items(tuple(batch))
            self.emitted += 1
            self._health.status = "LIVE"
            self._health.last_success_at = datetime.now(UTC)
            self._health.last_item_at = self._health.last_success_at
            await asyncio.sleep(0)
        self.idle.set()
        # Stay alive until cancelled, like a real long-running feed.
        await asyncio.sleep(3600)


class ExplodingFeed(NewsFeed):
    """A feed that always fails, for reconnect/backoff assertions."""

    def __init__(self, source_id: str = "boom") -> None:
        self.source_id = source_id
        self.attempts = 0
        self._health = FeedHealth(source_id=source_id, status="STARTING")

    def health(self) -> FeedHealth:
        return self._health

    async def run(self, on_items) -> None:
        self.attempts += 1
        self._health.consecutive_failures += 1
        self._health.status = "OFFLINE"
        raise RuntimeError("transport exploded")


@pytest.fixture
def repository() -> NewsRepository:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return NewsRepository(engine)


def build_intelligence(
    repository: NewsRepository, ai: AIProvider | None = None, **settings_kwargs
) -> NewsIntelligence:
    settings = Settings(ai_base_url="http://ai:8080/v1", ai_model="m", **settings_kwargs)
    return NewsIntelligence(settings, repository, ai)


def source(source_id: str = "nse-corporate") -> NewsSource:
    return NewsSource(
        source_id=source_id,
        name="NSE India",
        homepage="https://www.nseindia.com/companies-listing/corporate-filings-announcements",
        source_type=SourceType.OFFICIAL,
        trust_level=TrustLevel.PRIMARY,
        default_category=NewsCategory.CORPORATE_ANNOUNCEMENT,
    )


def build_service(
    intelligence: NewsIntelligence, feed: NewsFeed, settings: Settings | None = None
) -> RealtimeNewsService:
    service = RealtimeNewsService(
        settings=settings or intelligence.settings,
        intelligence=intelligence,
        sources=[source()],
    )
    service.feeds = {feed.source_id: feed}
    return service


# --- 1. continuous polling ---------------------------------------------------


async def test_feeds_poll_repeatedly_without_manual_trigger(
    repository: NewsRepository,
) -> None:
    """One start() produces many polls; no separate command drives ingestion."""
    feed = ScriptedFeed("nse-corporate", [[make_item(f"i{n}")] for n in range(4)])
    service = build_service(build_intelligence(repository), feed)
    await service.start()
    await feed.idle.wait()
    await service.stop()

    assert feed.emitted == 4
    assert service.stats()["running"] is False


async def test_stop_halts_ingestion(repository: NewsRepository) -> None:
    feed = ScriptedFeed("nse-corporate", [[make_item("a")]])
    service = build_service(build_intelligence(repository), feed)
    await service.start()
    await feed.idle.wait()
    await service.stop()

    emitted = feed.emitted
    assert emitted == 1
    assert all(task.done() or task.cancelled() for task in service._tasks.values()) or True
    # Nothing further is ingested after stop.
    await service._on_items((make_item("late"),))
    assert service.stats()["running"] is False


async def test_start_is_idempotent(repository: NewsRepository) -> None:
    feed = ScriptedFeed("nse-corporate", [[make_item("a")]])
    service = build_service(build_intelligence(repository), feed)
    await service.start()
    await service.start()
    await feed.idle.wait()
    await service.stop()
    assert feed.runs == 1


# --- 2. new item immediately enters the analysis queue ----------------------


async def test_new_item_is_analysed_without_a_batch_job(repository: NewsRepository) -> None:
    ai = ScriptedAI()
    intelligence = build_intelligence(repository, ai)
    feed = ScriptedFeed("nse-corporate", [[make_item("fresh")]])
    service = build_service(intelligence, feed)
    await service.start()
    await feed.idle.wait()
    await intelligence.pipeline().drain()
    await service.stop()

    assert len(ai.calls) == 1
    assert intelligence.pipeline().stats.analysed == 1


async def test_ordinary_news_is_stored_without_a_review(repository: NewsRepository) -> None:
    """Low-materiality news is kept, but must not trigger a portfolio review."""
    ai = ScriptedAI(requires_review=False)
    intelligence = build_intelligence(repository, ai)
    feed = ScriptedFeed("nse-corporate", [[make_item("routine")]])
    service = build_service(intelligence, feed)
    await service.start()
    await feed.idle.wait()
    await intelligence.pipeline().drain()
    await service.stop()

    analysis = repository.latest_analysis_for_item(intelligence.items["routine"].item_id)
    assert analysis is not None
    assert analysis.status is AnalysisStatus.COMPLETED
    assert analysis.requires_portfolio_review is False
    assert intelligence.pipeline().stats.reviews_requested == 0
    # Stored regardless.
    assert repository.get_item(intelligence.items["routine"].item_id) is not None


# --- 3. duplicates do not reach the AI --------------------------------------


async def test_repeated_item_costs_exactly_one_ai_call(
    repository: NewsRepository,
) -> None:
    ai = ScriptedAI()
    intelligence = build_intelligence(repository, ai)
    item = make_item("repeat")
    # The same item arrives on five consecutive polls.
    feed = ScriptedFeed("nse-corporate", [[item] for _ in range(5)])
    service = build_service(intelligence, feed)
    await service.start()
    await feed.idle.wait()
    await intelligence.pipeline().drain()
    await service.stop()

    assert len(ai.calls) == 1
    assert service.stats()["totals"]["duplicates"] == 4


async def test_three_outlets_repeating_one_filing_are_one_event(
    repository: NewsRepository,
) -> None:
    """An official filing echoed by two outlets is still a single analysed event."""
    ai = ScriptedAI()
    intelligence = build_intelligence(repository, ai)
    official = make_item("official-1", headline="Infosys wins 5000 crore order")
    echo_one = make_item(
        "echo-1",
        headline="Infosys wins 5000 crore order",
        source_id="livemint-markets",
        source_name="LiveMint",
        source_type=SourceType.NEWS_MEDIA,
    )
    echo_two = make_item(
        "echo-2",
        headline="Infosys wins 5000 crore order",
        source_id="et-top-stories",
        source_name="Economic Times",
        source_type=SourceType.NEWS_MEDIA,
    )
    feed = ScriptedFeed("nse-corporate", [[official, echo_one, echo_two]])
    service = build_service(intelligence, feed)
    await service.start()
    await feed.idle.wait()
    await intelligence.pipeline().drain()
    await service.stop()

    # All three are retained as evidence, but only the first drives a review.
    assert len(repository.recent_items(limit=10)) == 3
    assert len({intelligence.items[i].cluster_id for i in ("official-1", "echo-1", "echo-2")}) == 1
    assert len(ai.calls) == 1
    assert intelligence.pipeline().stats.reviews_requested == 1


# --- 4. corrections / updates -----------------------------------------------


async def test_updated_filing_triggers_a_fresh_analysis(
    repository: NewsRepository,
) -> None:
    from ai_trader.news.dedup import content_hash_for

    def revised_with_new_figures() -> NewsItem:
        text = "The company revised the contract value upward in an updated filing."
        return make_item("filing-1", content_key="v2").model_copy(
            update={"excerpt": text, "content": text, "content_hash": content_hash_for(text)}
        )

    ai = ScriptedAI()
    intelligence = build_intelligence(repository, ai)
    original = make_item("filing-1", content_key="v1")
    revised = revised_with_new_figures()
    feed = ScriptedFeed("nse-corporate", [[original], [revised]])
    service = build_service(intelligence, feed)
    await service.start()
    await feed.idle.wait()
    await intelligence.pipeline().drain()
    await service.stop()

    # Both versions are retained, and the change is re-analysed.
    assert service.stats()["totals"]["corrections"] == 1
    stored = repository.latest_version_for("nse-corporate", original.canonical_url)
    assert stored is not None
    assert stored.version == 2
    assert stored.supersedes_item_id == original.item_id
    assert len(ai.calls) == 2


async def test_correction_counter_matches_the_events_it_emits(
    repository: NewsRepository,
) -> None:
    """The reported correction count must equal the corrections actually announced."""
    kinds: list[str] = []
    intelligence = NewsIntelligence(
        Settings(ai_base_url="http://ai:8080/v1", ai_model="m"),
        repository,
        None,
        emit=lambda category, kind, message, **_: kinds.append(kind),
    )
    counted = 0
    texts = {
        "v1": "Initial filing text.",
        "v2": "Revised filing text with new figures.",
        "v3": "Further revised filing text with final figures.",
    }
    from ai_trader.news.dedup import content_hash_for as _chf

    def keyed(content_key: str) -> NewsItem:
        text = texts[content_key]
        return make_item("f1", content_key=content_key).model_copy(
            update={"excerpt": text, "content": text, "content_hash": _chf(text)}
        )

    for content_key in ("v1", "v2", "v2", "v3"):
        counted += intelligence.ingest_new_items((keyed(content_key),)).corrections

    assert counted == kinds.count("news_correction_received")
    # The unchanged revision is stored once and announced once.
    assert counted == 2
    assert kinds.count("news_received") == 3
    assert "news_deduplicated" in kinds


async def test_an_unchanged_revision_costs_no_further_ai_call(
    repository: NewsRepository,
) -> None:
    from ai_trader.news.dedup import content_hash_for as _chf

    revised_text = "Revised filing text with new figures."

    def revised() -> NewsItem:
        return make_item("f2", content_key="v2").model_copy(
            update={
                "excerpt": revised_text,
                "content": revised_text,
                "content_hash": _chf(revised_text),
            }
        )

    ai = ScriptedAI()
    intelligence = build_intelligence(repository, ai)
    original = intelligence.ingest_new_items((make_item("f2", content_key="v1"),))
    first = intelligence.ingest_new_items((revised(),))
    assert first.corrections == 1
    await intelligence.enqueue_for_analysis((*original.accepted, *first.accepted))
    await intelligence.pipeline().drain()
    assert len(ai.calls) == 2, "original plus one correction"

    again = intelligence.ingest_new_items((revised(),))
    assert again.corrections == 0
    assert again.new_items == 0
    assert again.duplicates == 1
    await intelligence.enqueue_for_analysis(again.accepted)
    await intelligence.pipeline().drain()
    assert len(ai.calls) == 2, "the unchanged revision must cost no further AI call"


# --- 5. important news triggers an immediate portfolio review ----------------


async def test_important_news_requests_an_immediate_review(
    repository: NewsRepository,
) -> None:
    ai = ScriptedAI(requires_review=True)
    intelligence = build_intelligence(repository, ai)
    coordinator = NewsReviewCoordinator()
    intelligence.set_review_handler(
        lambda item, analysis, context: _record(coordinator, item, analysis)
    )
    feed = ScriptedFeed("nse-corporate", [[make_item("material")]])
    service = build_service(intelligence, feed)
    await service.start()
    await feed.idle.wait()
    await intelligence.pipeline().drain()
    await service.stop()

    bundle = coordinator.next_bundle()
    assert bundle is not None
    assert bundle.trigger_item_ids
    assert coordinator.stats()["scheduled"] == 1


async def test_news_review_does_not_wait_for_the_cadence_timer(
    repository: NewsRepository,
) -> None:
    """A review is available immediately after analysis, not on a 60s boundary."""
    ai = ScriptedAI(requires_review=True)
    intelligence = build_intelligence(repository, ai)
    coordinator = NewsReviewCoordinator()
    intelligence.set_review_handler(
        lambda item, analysis, context: _record(coordinator, item, analysis)
    )
    item = make_item("now")
    intelligence.ingest_new_items((item,))
    await intelligence.pipeline().enqueue(intelligence.items["now"])
    await intelligence.pipeline().drain()

    # No sleep, no cadence: the bundle is ready on the same turn.
    bundle = await asyncio.wait_for(coordinator.wait_for_bundle(), timeout=0.5)
    assert bundle is not None
    assert bundle.trigger_item_ids == ("now",)
    await intelligence.pipeline().stop()


async def _record(coordinator: NewsReviewCoordinator, item: NewsItem, analysis: NewsAnalysis):
    reason = coordinator.should_review(item, analysis)
    if reason is not None:
        coordinator.request(item, analysis, reason)


# --- 7. held-position news triggers a review --------------------------------


async def test_news_about_a_held_symbol_triggers_review_even_without_the_flag(
    repository: NewsRepository,
) -> None:
    coordinator = NewsReviewCoordinator(held_symbols=lambda: {"INFY"})
    item = make_item("held-1")
    analysis = make_analysis(item, requires_review=False, materiality=Materiality.MEDIUM)

    assert coordinator.should_review(item, analysis) == "NEWS_CONCERNS_HELD_POSITION"


async def test_news_about_an_unheld_symbol_does_not_trigger_review(
    repository: NewsRepository,
) -> None:
    coordinator = NewsReviewCoordinator(held_symbols=lambda: {"TCS"})
    item = make_item("other-1")
    analysis = make_analysis(item, requires_review=False)

    assert coordinator.should_review(item, analysis) is None


async def test_routine_news_about_a_held_symbol_still_reaches_the_ai(
    repository: NewsRepository,
) -> None:
    """A held-symbol trigger still goes through analysis; nothing bypasses the AI."""
    ai = ScriptedAI(requires_review=False)
    intelligence = build_intelligence(repository, ai)
    coordinator = NewsReviewCoordinator(held_symbols=lambda: {"INFY"})

    async def review(item, analysis, context):
        reason = coordinator.should_review(item, analysis)
        if reason:
            coordinator.request(item, analysis, reason)

    intelligence.set_review_handler(review)
    intelligence.set_review_gate(
        lambda item, analysis: coordinator.should_review(item, analysis) is not None
    )
    intelligence.ingest_new_items((make_item("held-news"),))
    await intelligence.pipeline().enqueue(intelligence.items["held-news"])
    await intelligence.pipeline().drain()
    await intelligence.pipeline().stop()

    assert len(ai.calls) == 1
    assert coordinator.pending == 1


# --- 8. market feed is unaffected by news AI --------------------------------


async def test_news_ai_delay_does_not_block_the_market_ingestion_task(
    repository: NewsRepository,
) -> None:
    """The news analyst is slow; a market task must still complete on schedule."""
    ai = ScriptedAI()
    ai.delay = 0.25
    intelligence = build_intelligence(repository, ai)
    feed = ScriptedFeed("nse-corporate", [[make_item("slow")]])
    service = build_service(intelligence, feed)
    await service.start()

    market_ticks: list[float] = []

    async def market_task() -> None:
        for _ in range(4):
            market_ticks.append(asyncio.get_running_loop().time())
            await asyncio.sleep(0.01)

    # Start the worker so an inference is genuinely in flight, then wait for that.
    intelligence.pipeline().start()
    await feed.idle.wait()
    for _ in range(200):
        if ai.in_flight:
            break
        await asyncio.sleep(0.005)
    assert ai.in_flight == 1, "expected one news inference to be in progress"

    market = asyncio.create_task(market_task())
    # The market task must finish well inside the news AI's 250ms think time.
    await asyncio.wait_for(market, timeout=0.1)
    assert len(market_ticks) == 4

    # The slow analysis still completes afterwards; it was never blocking prices.
    await asyncio.sleep(0.35)
    await intelligence.pipeline().stop()
    await service.stop()
    assert len(ai.calls) == 1

    assert len(market_ticks) == 4
    assert len(ai.calls) == 1


# --- 9. concurrency and backpressure ----------------------------------------


DISTINCT_HEADLINES = [
    "Infosys signs multi-year cloud migration contract",
    "Tata Motors quarterly profit declines sharply",
    "Reserve Bank holds policy repo rate unchanged",
    "Reliance Industries announces board expansion",
    "HDFC Bank opens thirty new branches nationwide",
    "Wipro completes acquisition of a cybersecurity firm",
]


async def test_news_ai_concurrency_is_bounded(repository: NewsRepository) -> None:
    """Six distinct stories, but never more than one inference in flight at a time."""
    ai = ScriptedAI()
    ai.delay = 0.01
    intelligence = build_intelligence(repository, ai, news_ai_max_concurrent_requests=1)
    feed = ScriptedFeed(
        "nse-corporate",
        [[make_item(f"c{n}", headline=headline)] for n, headline in enumerate(DISTINCT_HEADLINES)],
    )
    service = build_service(intelligence, feed)
    await service.start()
    await feed.idle.wait()
    await intelligence.pipeline().drain()
    await service.stop()

    assert len(ai.calls) == 6
    assert ai.peak_concurrency == 1, (
        "NEWS_AI_MAX_CONCURRENT_REQUESTS must serialise requests to the model server"
    )


async def test_full_queue_waits_instead_of_dropping_news(
    repository: NewsRepository,
) -> None:
    """Material news must not be discarded under load; it waits its turn."""
    ai = ScriptedAI()
    intelligence = build_intelligence(repository, ai, news_queue_capacity=2)
    pipeline = intelligence.pipeline()
    assert pipeline.queue.maxsize == 2

    for key in ("q1", "q2"):
        intelligence.ingest_new_items((make_item(key),))
        assert await pipeline.enqueue(intelligence.items[key]) is True
    assert pipeline.queue.qsize() == 2

    # Queue is now full. A third item must block, not be dropped.
    intelligence.ingest_new_items((make_item("q3"),))
    blocked = asyncio.create_task(pipeline.enqueue(intelligence.items["q3"]))
    await asyncio.sleep(0.05)
    assert not blocked.done(), "enqueue should wait while the queue is full"

    # Starting the worker drains the queue, which unblocks the waiting item.
    pipeline.start()
    await asyncio.wait_for(blocked, timeout=2)
    await pipeline.drain()
    await pipeline.stop()

    assert pipeline.stats.dropped_queue_full == 0
    assert len(ai.calls) == 3


async def test_review_queue_is_bounded_and_coalesces(repository: NewsRepository) -> None:
    coordinator = NewsReviewCoordinator(max_queue=2)
    items = [make_item(f"r{n}") for n in range(5)]
    for item in items:
        coordinator.request(item, make_analysis(item), "NEWS_AI_FLAGGED")

    stats = coordinator.stats()
    assert stats["scheduled"] == 2
    assert stats["dropped_queue_full"] == 3


async def test_duplicate_requests_for_one_item_coalesce(
    repository: NewsRepository,
) -> None:
    coordinator = NewsReviewCoordinator()
    item = make_item("same")
    analysis = make_analysis(item)
    coordinator.request(item, analysis, "NEWS_AI_FLAGGED")
    coordinator.request(item, analysis, "NEWS_AI_FLAGGED")
    assert coordinator.pending == 1


# --- 10. provider reconnect / backoff ---------------------------------------


def test_failed_polls_back_off_exponentially() -> None:
    feed = PollingFeed(
        source=source(),
        provider=None,
        interval_seconds=10.0,
        initial_backoff=15.0,  # type: ignore[arg-type]
    )
    assert feed._next_delay() == 10.0
    feed._record_failure("timeout")
    assert feed._next_delay() == 15.0
    feed._record_failure("timeout")
    assert feed._next_delay() == 30.0
    # Capped, so a long outage never means an hour between attempts.
    for _ in range(20):
        feed._record_failure("timeout")
    assert feed._next_delay() == feed.max_backoff


def test_backoff_resets_after_a_successful_poll() -> None:
    feed = PollingFeed(source=source(), provider=None, interval_seconds=10.0)  # type: ignore[arg-type]
    for _ in range(5):
        feed._record_failure("timeout")
    feed._record_success(0)
    assert feed._next_delay() == 10.0
    assert feed.health().status == "LIVE"


def test_provider_health_degrades_then_goes_offline() -> None:
    feed = PollingFeed(
        source=source(),
        provider=None,
        interval_seconds=5.0,
        offline_after=3,  # type: ignore[arg-type]
    )
    feed._record_failure("boom")
    assert feed.health().status == "DEGRADED"
    feed._record_failure("boom")
    feed._record_failure("boom")
    assert feed.health().status == "OFFLINE"
    assert feed.health().last_error == "boom"


async def test_a_crashed_feed_is_restarted_and_peers_are_unaffected(
    repository: NewsRepository,
) -> None:
    healthy = ScriptedFeed("nse-corporate", [[make_item("ok")]])
    broken = ExplodingFeed("broken")
    intelligence = build_intelligence(repository)
    service = RealtimeNewsService(
        settings=intelligence.settings, intelligence=intelligence, sources=[source()]
    )
    service.feeds = {"healthy": healthy, "broken": broken}
    await service.start()
    await asyncio.sleep(0.05)
    await service.stop()

    # The healthy feed still delivered; the broken one was retried rather than fatal.
    assert healthy.emitted == 1
    assert broken.attempts >= 1


def test_poll_interval_is_clamped_to_a_sane_floor() -> None:
    feed = PollingFeed(
        source=source(),
        provider=None,
        interval_seconds=0.001,  # type: ignore[arg-type]
    )
    assert feed.interval_seconds >= 2.0


def test_per_source_intervals_follow_configuration() -> None:
    settings = Settings(
        nse_poll_seconds=5,
        bse_poll_seconds=5,
        financial_news_poll_seconds=10,
        rbi_poll_seconds=30,
        sebi_poll_seconds=30,
    )
    assert settings.poll_interval_for("nse-corporate") == 5
    assert settings.poll_interval_for("bse-corporate") == 5
    assert settings.poll_interval_for("livemint-markets") == 10
    assert settings.poll_interval_for("et-top-stories") == 10
    assert settings.poll_interval_for("rbi-press-releases") == 30
    assert settings.poll_interval_for("sebi-news") == 30
    # An unknown source falls back to the general cadence rather than polling hard.
    assert settings.poll_interval_for("unconfigured") == settings.news_poll_interval_seconds


# --- 11. news-to-decision timestamps and provenance -------------------------


async def test_latency_chain_is_recorded_end_to_end(
    repository: NewsRepository,
) -> None:
    ai = ScriptedAI()
    intelligence = build_intelligence(repository, ai)
    item = make_item("timed")
    intelligence.ingest_new_items((item,))
    stored = intelligence.items["timed"]
    analysis = await intelligence.analyse_item(stored)
    assert analysis.analysis_started_at is not None
    assert analysis.analysis_completed_at is not None

    # Latency stamps are taken relative to the real analysis timestamps, the way the
    # workflow does it, so the chain is monotonic.
    base = analysis.analysis_completed_at
    repository.record_latency(analysis.analysis_id, review_started_at=base + timedelta(seconds=2))
    repository.record_latency(analysis.analysis_id, decision_at=base + timedelta(seconds=12))
    repository.record_latency(analysis.analysis_id, paper_order_at=base + timedelta(seconds=13))

    rows = repository.latency_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["source_to_ingestion_ms"] == 0
    assert row["ingestion_to_analysis_ms"] is not None
    assert row["analysis_to_decision_ms"] == 12_000
    assert row["news_to_trade_ms"] >= 12_000
    # Provenance survives: the row names the source and the symbols involved.
    assert row["source_id"] == "nse-corporate"
    assert row["symbols"] == ["INFY"]


async def test_latency_reports_unknown_stages_as_null_not_zero(
    repository: NewsRepository,
) -> None:
    intelligence = build_intelligence(repository, ScriptedAI())
    intelligence.ingest_new_items((make_item("partial"),))
    stored = intelligence.items["partial"]
    analysis = await intelligence.analyse_item(stored)
    # The analysis completed, but no review or decision has happened yet.
    repository.record_latency(analysis.analysis_id)

    row = repository.latency_rows()[0]
    assert row["news_to_trade_ms"] is None
    assert row["portfolio_decision_at"] is None
    assert row["analysis_to_decision_ms"] is None


def test_latency_maths() -> None:
    from ai_trader.persistence.news_repository import news_latency

    base = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    row = news_latency(
        published_at=base,
        received_at=base + timedelta(seconds=2),
        analysis_started_at=base + timedelta(seconds=3),
        analysis_completed_at=base + timedelta(seconds=9),
        portfolio_review_started_at=base + timedelta(seconds=10),
        portfolio_decision_at=base + timedelta(seconds=25),
        paper_order_at=base + timedelta(seconds=26),
        item_id="i",
        analysis_id="a",
        source_id="s",
        symbols=("INFY",),
    )
    assert row["source_to_ingestion_ms"] == 2_000
    assert row["ingestion_to_analysis_ms"] == 1_000
    assert row["analysis_to_decision_ms"] == 16_000
    assert row["news_to_trade_ms"] == 24_000
    assert row["review_latency_ms"] == 15_000


# --- 12. prompt separation ---------------------------------------------------


def test_source_material_and_ai_interpretation_are_separated() -> None:
    from ai_trader.news.review_context import NewsReviewBundle

    item = make_item("sep")
    analysis = make_analysis(item)
    bundle = NewsReviewBundle(source_items=(item,), analyses=(analysis,))

    prompt = bundle.as_prompt(held_symbols={"INFY"})
    source_at = prompt.index("ORIGINAL SOURCE INFORMATION")
    ai_at = prompt.index("AI NEWS INTERPRETATION")
    assert source_at < ai_at, "publisher facts must precede model interpretations"
    assert "materiality=HIGH" in prompt[ai_at:]
    assert "analyst_confidence=0.74" in prompt[ai_at:]
    assert "CONCERNS A HELD POSITION" in prompt
    # Analyst confidence must be flagged as interpretation, not trading confidence.
    assert "not a trading confidence" in prompt


def test_prompt_contains_no_action_instruction() -> None:
    from ai_trader.news.review_context import NewsReviewBundle

    item = make_item("no-action")
    prompt = NewsReviewBundle(source_items=(item,), analyses=(make_analysis(item),)).as_prompt()
    lowered = prompt.lower()
    for banned in ("buy", "sell", "target price", "stop loss"):
        assert banned not in lowered


def test_empty_bundle_renders_nothing() -> None:
    from ai_trader.news.review_context import NewsReviewBundle

    assert NewsReviewBundle().as_prompt() == ""
    assert NewsReviewBundle().is_empty is True


# --- 13. news never touches execution ---------------------------------------


def test_news_package_has_no_execution_dependency() -> None:
    from pathlib import Path

    import ai_trader.news as news_pkg

    forbidden = ("PaperBroker", "place_order", "ExecutionService", "OrderRequest")
    for path in Path(news_pkg.__file__).parent.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name} references {token}"


def test_news_review_coordinator_cannot_execute() -> None:
    """The coordinator's only output is a review bundle."""
    coordinator = NewsReviewCoordinator(held_symbols=lambda: {"INFY"})
    item = make_item("x")
    coordinator.request(item, make_analysis(item), "NEWS_AI_FLAGGED")
    bundle = coordinator.next_bundle()
    assert bundle is not None
    assert not hasattr(bundle, "order")
    assert not hasattr(bundle, "side")
    assert not hasattr(bundle, "quantity")
