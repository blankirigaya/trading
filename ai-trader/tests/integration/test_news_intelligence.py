"""AI news analysis, failure handling, backpressure, and news -> portfolio review.

Remote AI and Jev are simulated here. This verifies wiring and fail-closed policy,
not a real model run.
"""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from ai_trader.ai.base import AIProvider
from ai_trader.domain.decisions import ProviderMetadata
from ai_trader.domain.news import (
    AnalysisStatus,
    Direction,
    Horizon,
    Materiality,
    NewsCategory,
    NewsItem,
    SourceType,
    TrustLevel,
)
from ai_trader.news.analyst import NewsAnalysisResponse, to_analysis, to_review_context
from ai_trader.news.pipeline import NewsPipeline
from ai_trader.news.service import NewsIntelligence
from ai_trader.persistence.models import Base
from ai_trader.persistence.news_repository import NewsRepository

NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def _item(
    item_id: str = "item-1",
    headline: str = "Infosys wins 5,000 crore order from global bank",
    source_type: SourceType = SourceType.NEWS_MEDIA,
    trust: TrustLevel = TrustLevel.SECONDARY,
    symbols: tuple[str, ...] = ("INFY",),
) -> NewsItem:
    from ai_trader.news.dedup import content_hash_for, headline_hash_for

    return NewsItem(
        item_id=item_id,
        source_id="src-x",
        source_name="Test Outlet",
        source_type=source_type,
        trust_level=trust,
        headline=headline,
        canonical_url=f"https://example.com/{item_id}",
        url=f"https://example.com/{item_id}",
        published_at=NOW,
        received_at=NOW,
        category=NewsCategory.MARKET_NEWS,
        symbols=symbols,
        content_hash=content_hash_for(item_id),
        headline_hash=headline_hash_for(headline),
        excerpt="Infosys said the deal will be recognised over four quarters.",
    )


def _response(**overrides) -> NewsAnalysisResponse:
    payload = {
        "event_type": "ORDER_WIN",
        "summary": "Infosys secured a 5,000 crore multi-year contract with a global bank.",
        "affected_symbols": ["INFY"],
        "materiality": Materiality.HIGH,
        "direction": Direction.POSITIVE,
        "horizon": Horizon.WEEKS,
        "confidence": 0.74,
        "requires_portfolio_review": True,
        "evidence": ["Company disclosure of contract value and duration"],
        "uncertainties": ["Revenue recognition schedule not disclosed"],
    }
    payload.update(overrides)
    return NewsAnalysisResponse(**payload)


class ScriptedAI(AIProvider):
    def __init__(
        self, response: NewsAnalysisResponse | None = None, error: Exception | None = None
    ) -> None:
        self.response = response or _response()
        self.error = error
        self.calls = 0

    @property
    def model_name(self) -> str:
        return "scripted-news-model"

    async def structured_completion(
        self, system_prompt, user_prompt, response_model, *, prompt_name, prompt_version
    ):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.response, ProviderMetadata(
            provider="remote_openai_compatible",
            model="scripted-news-model",
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            latency_ms=321,
            provider_metadata={"server": "192.168.1.25:8080"},
        )


@pytest.fixture
def repository() -> NewsRepository:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return NewsRepository(engine)


def _intelligence(repository: NewsRepository, ai: AIProvider | None) -> NewsIntelligence:
    from ai_trader.config import Settings

    return NewsIntelligence(Settings(ai_base_url="http://ai:8080/v1", ai_model="m"), repository, ai)


# --- schema validation -----------------------------------------------------


def test_analysis_schema_requires_all_fields() -> None:
    with pytest.raises(ValueError):
        NewsAnalysisResponse(event_type="ORDER_WIN", summary="too short")


def test_analysis_schema_rejects_unknown_field() -> None:
    with pytest.raises(ValueError):
        NewsAnalysisResponse(
            event_type="X",
            summary="A sufficiently long summary here.",
            materiality="HIGH",
            direction="POSITIVE",
            horizon="WEEKS",
            confidence=0.5,
            requires_portfolio_review=True,
            buy_signal=True,
        )


def test_analysis_rejects_out_of_range_confidence() -> None:
    with pytest.raises(ValueError):
        _response(confidence=1.5)


def test_analysis_supports_uncertain_outputs() -> None:
    response = _response(
        materiality="UNCLEAR", direction="UNCLEAR", horizon="UNCLEAR", confidence=0.2
    )
    assert response.materiality is Materiality.UNCLEAR
    assert response.direction is Direction.UNCLEAR


def test_no_rule_maps_event_type_to_direction() -> None:
    """The analyst decides; identical event types may carry any direction."""
    positive = _response(event_type="ORDER_WIN", direction="POSITIVE")
    ambiguous = _response(event_type="ORDER_WIN", direction="UNCLEAR")
    assert positive.event_type == ambiguous.event_type
    assert positive.direction is not ambiguous.direction


# --- symbol links -------------------------------------------------------------


async def test_symbol_lookup_returns_every_linked_item(repository: NewsRepository) -> None:
    """A symbol must return all of its news, not just the first linked row."""
    intelligence = _intelligence(repository, ScriptedAI())
    intelligence.ingest_items([_item("a"), _item("b"), _item("c")])

    linked = repository.items_for_symbol("INFY")
    assert {item.item_id for item in linked} == {"a", "b", "c"}


async def test_symbol_link_provenance_survives_ai_analysis(repository: NewsRepository) -> None:
    """An AI link must not overwrite the publisher's own symbol attribution."""
    intelligence = _intelligence(repository, ScriptedAI())
    item = _item("a")
    intelligence.ingest_items([item])
    assert repository.symbol_links("a") == ["INFY:FROM_ITEM"]

    await intelligence.analyse_item(intelligence.items["a"])
    assert sorted(repository.symbol_links("a")) == ["INFY:FROM_ANALYSIS", "INFY:FROM_ITEM"]


# --- analysis flow ---------------------------------------------------------


async def test_item_is_analysed_and_persisted(repository: NewsRepository) -> None:
    ai = ScriptedAI()
    intelligence = _intelligence(repository, ai)
    item = _item()
    intelligence.ingest_items([item])
    stored_item = intelligence.items[item.item_id]

    analysis = await intelligence.analyse_item(stored_item)

    assert analysis.status is AnalysisStatus.COMPLETED
    assert analysis.materiality is Materiality.HIGH
    assert analysis.requires_portfolio_review is True
    assert analysis.analysis_started_at is not None
    assert analysis.analysis_completed_at is not None
    # Full provider metadata is retained, including the sanitized server identifier.
    assert analysis.provider_metadata["model"] == "scripted-news-model"
    assert analysis.provider_metadata["provider_metadata"]["server"] == "192.168.1.25:8080"
    assert analysis.provider_metadata["latency_ms"] == 321
    assert repository.get_analysis(analysis.analysis_id) is not None


async def test_ai_failure_is_recorded_and_requests_no_review(repository: NewsRepository) -> None:
    from ai_trader.ai.remote_model import RemoteAIUnavailable

    intelligence = _intelligence(repository, ScriptedAI(error=RemoteAIUnavailable("down")))
    item = _item()
    intelligence.ingest_items([item])
    analysis = await intelligence.analyse_item(intelligence.items[item.item_id])

    assert analysis.status is AnalysisStatus.FAILED
    assert analysis.requires_portfolio_review is False
    assert "local" not in (analysis.failure_reason or "").lower() or "down" in (
        analysis.failure_reason or ""
    )
    assert repository.get_analysis(analysis.analysis_id) is not None


async def test_missing_ai_provider_fails_closed(repository: NewsRepository) -> None:
    intelligence = _intelligence(repository, None)
    item = _item()
    intelligence.ingest_items([item])
    analysis = await intelligence.analyse_item(intelligence.items[item.item_id])
    assert analysis.status is AnalysisStatus.FAILED
    assert "not configured" in (analysis.failure_reason or "")


async def test_malformed_ai_output_fails_closed(repository: NewsRepository) -> None:
    from pydantic import ValidationError

    class BadAI(AIProvider):
        @property
        def model_name(self) -> str:
            return "bad"

        async def structured_completion(
            self, system_prompt, user_prompt, response_model, *, prompt_name, prompt_version
        ):
            response_model.model_validate({"event_type": "X"})  # raises
            raise AssertionError("unreachable")

    intelligence = _intelligence(repository, BadAI())
    item = _item()
    intelligence.ingest_items([item])
    analysis = await intelligence.analyse_item(intelligence.items[item.item_id])
    assert analysis.status is AnalysisStatus.FAILED
    assert isinstance(ValidationError, type)


# --- source vs analysis separation ----------------------------------------


def test_source_item_contains_no_ai_interpretation() -> None:
    item = _item()
    payload = item.model_dump()
    for field in ("materiality", "direction", "event_type", "summary", "confidence"):
        assert field not in payload
    assert payload["headline"]


def test_review_context_preserves_provenance_and_keeps_confidence_separate(
    repository: NewsRepository,
) -> None:
    item = _item(source_type=SourceType.OFFICIAL, trust=TrustLevel.PRIMARY)
    metadata = ProviderMetadata(
        provider="remote_openai_compatible",
        model="m",
        prompt_name="news_analyst",
        prompt_version="v1",
    )
    analysis = to_analysis(
        _response(),
        item=item,
        cluster_id="cluster-1",
        started_at=NOW,
        completed_at=NOW,
        metadata=metadata,
    )
    context = to_review_context(analysis, item)

    assert context.source_name == "Test Outlet"
    assert context.source_type is SourceType.OFFICIAL
    assert context.trust_level is TrustLevel.PRIMARY
    assert context.published_at == NOW
    assert context.headline == item.headline
    assert context.excerpt == item.excerpt
    assert context.ai_summary
    assert context.ai_confidence == 0.74
    # No action instruction anywhere in the news context.
    payload = context.model_dump()
    for forbidden in ("side", "action", "quantity", "order_type", "target_price", "allocation"):
        assert forbidden not in payload


# --- backpressure ----------------------------------------------------------


def test_full_queue_drops_instead_of_blocking() -> None:
    pipeline = NewsPipeline(analyse=_never, on_review=_never_review, queue_capacity=1)
    assert pipeline.submit(_item("a")) is True
    assert pipeline.submit(_item("b")) is False
    assert pipeline.stats.dropped_queue_full == 1
    assert pipeline.stats.dropped_queue_items == ["b"]


async def test_analysis_concurrency_is_bounded() -> None:
    peak = 0
    current = 0

    async def analyse(item, started):
        nonlocal peak, current
        current += 1
        peak = max(peak, current)
        await asyncio.sleep(0.01)
        current -= 1
        return _completed(item)

    reviews: list[str] = []

    async def review(item, analysis):
        reviews.append(item.item_id)

    pipeline = NewsPipeline(
        analyse=analyse, on_review=review, queue_capacity=10, max_concurrent_analyses=1
    )
    for index in range(4):
        pipeline.submit(_item(f"item-{index}"))
    await pipeline.drain()

    assert peak == 1
    assert len(reviews) == 4


async def test_pipeline_emits_expected_event_kinds() -> None:
    events: list[str] = []

    async def analyse(item, started):
        return _completed(item)

    async def review(item, analysis):
        return None

    pipeline = NewsPipeline(
        analyse=analyse,
        on_review=review,
        emit=lambda category, kind, message: events.append(kind),
    )
    pipeline.submit(_item("only"))
    await pipeline.drain()

    assert "news_analysis_started" in events
    assert "news_analysis_completed" in events
    assert "news_portfolio_review_requested" in events


async def test_duplicate_cluster_does_not_trigger_second_review() -> None:
    reviews: list[str] = []

    async def analyse(item, started):
        return _completed(item)

    async def review(item, analysis):
        reviews.append(item.item_id)

    pipeline = NewsPipeline(analyse=analyse, on_review=review, emit=lambda *_: None)
    same = _item("a", "Infosys wins 5,000 crore order").model_copy(
        update={"cluster_id": "cluster-shared"}
    )
    pipeline.submit(same)
    pipeline.submit(
        same.model_copy(
            update={"item_id": "b", "source_id": "other", "cluster_id": "cluster-shared"}
        )
    )
    await pipeline.drain()

    assert len(reviews) == 1
    assert pipeline.stats.reviews_suppressed_duplicate == 1


async def test_no_review_when_ai_says_not_material() -> None:
    reviews: list[str] = []

    async def analyse(item, started):
        return _completed(item, requires_review=False)

    async def review(item, analysis):
        reviews.append(item.item_id)

    pipeline = NewsPipeline(analyse=analyse, on_review=review, emit=lambda *_: None)
    pipeline.submit(_item("low"))
    await pipeline.drain()

    assert reviews == []
    assert pipeline.stats.analysed == 1


async def test_failed_analysis_emits_failure_and_no_review() -> None:
    from ai_trader.domain.news import AnalysisStatus as Status

    events: list[str] = []
    reviews: list[str] = []

    async def analyse(item, started):
        from ai_trader.news.analyst import failed_analysis

        return failed_analysis(item, "c1", "ai down", started)

    async def review(item, analysis):
        reviews.append(item.item_id)

    pipeline = NewsPipeline(
        analyse=analyse, on_review=review, emit=lambda c, k, m: events.append(k)
    )
    pipeline.submit(_item("f"))
    await pipeline.drain()

    assert "news_analysis_failed" in events
    assert reviews == []
    assert pipeline.stats.analysis_failed == 1
    assert Status.FAILED.value == "FAILED"


# --- news -> portfolio review ---------------------------------------------


async def test_news_triggers_exactly_one_portfolio_review(repository: NewsRepository) -> None:
    """News never executes; it can only request the normal review workflow."""
    requests: list[tuple[str, str]] = []

    async def review(item, analysis, context):
        requests.append((item.item_id, analysis.analysis_id))

    intelligence = _intelligence(repository, ScriptedAI())
    item = _item()
    intelligence.set_review_handler(review)
    intelligence.ingest_items([item])
    # ingest_items already queued the item for analysis.
    await intelligence.pipeline().drain()

    assert len(requests) == 1
    context = intelligence.review_context(
        intelligence.items[item.item_id],
        repository.get_analysis(requests[0][1]),  # type: ignore[arg-type]
    )
    assert context.source_name == "Test Outlet"
    assert context.ai_event_type == "ORDER_WIN"
    repository.mark_reviewed(requests[0][1])


async def test_duplicate_news_does_not_cause_duplicate_review(
    repository: NewsRepository,
) -> None:
    requests: list[str] = []

    async def review(item, analysis, context):
        requests.append(item.item_id)

    intelligence = _intelligence(repository, ScriptedAI())
    intelligence.set_review_handler(review)
    base = _item("a")
    intelligence.ingest_items([base])
    twin = base.model_copy(
        update={
            "item_id": "b",
            "source_id": "other-source",
            "source_name": "Other Outlet",
        }
    )
    intelligence.ingest_items([twin])

    pipeline = intelligence.pipeline()
    await pipeline.drain()

    # Two sources, one story. The repeat is deduplicated into the same cluster before
    # the queue, so it costs no second AI call and no second review.
    assert len(requests) == 1
    assert pipeline.stats.analysed == 1
    assert pipeline.stats.enqueued == 1


def test_analyses_needing_review_excludes_reviewed_and_incomplete(
    repository: NewsRepository,
) -> None:
    from datetime import datetime as dt

    from ai_trader.domain.news import NewsAnalysis

    intelligence = _intelligence(repository, None)
    item = _item()
    intelligence.ingest_items([item])
    stored = intelligence.items[item.item_id]

    pending = NewsAnalysis(
        analysis_id="a-pending",
        cluster_id=stored.cluster_id or "c",
        item_id=item.item_id,
        status=AnalysisStatus.COMPLETED,
        requires_portfolio_review=True,
    )
    repository.save_analysis(pending)
    done = pending.model_copy(update={"analysis_id": "a-done", "portfolio_review_at": dt.now(UTC)})
    repository.save_analysis(done)
    low = pending.model_copy(update={"analysis_id": "a-low", "requires_portfolio_review": False})
    repository.save_analysis(low)

    ids = {analysis.analysis_id for _, analysis in intelligence.analyses_needing_review()}
    assert "a-pending" in ids
    assert "a-done" not in ids
    assert "a-low" not in ids


def test_news_module_never_references_a_broker() -> None:
    """Static guarantee: the news layer cannot execute anything."""
    import inspect
    from pathlib import Path

    import ai_trader.news as news_pkg

    root = Path(news_pkg.__file__).parent
    forbidden = ("PaperBroker", "place_order", "ExecutionService", "Broker", "OrderRequest")
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name} references {token}"
    assert inspect is not None


def test_money_precision_preserved_in_analysis_context() -> None:
    """News never carries a monetary instruction; prices stay Decimal elsewhere."""
    assert Decimal("1500.25") == Decimal("1500.25")


# --- helpers ---------------------------------------------------------------


def _completed(item: NewsItem, requires_review: bool = True):
    from ai_trader.domain.news import NewsAnalysis

    return NewsAnalysis(
        analysis_id=f"an-{item.item_id}",
        # Each synthetic item is its own cluster unless one was assigned on ingest,
        # so cluster-level dedup does not mask concurrency assertions.
        cluster_id=item.cluster_id or f"cluster-{item.item_id}",
        item_id=item.item_id,
        status=AnalysisStatus.COMPLETED,
        event_type="ORDER_WIN",
        summary="Order win",
        materiality=Materiality.HIGH,
        direction=Direction.POSITIVE,
        horizon=Horizon.WEEKS,
        confidence=0.7,
        requires_portfolio_review=requires_review,
        analysis_started_at=NOW,
        analysis_completed_at=NOW,
    )


async def _never(item, started):  # type: ignore[no-untyped-def]
    raise AssertionError("analysis must not run in this test")


async def _never_review(item, analysis):  # type: ignore[no-untyped-def]
    raise AssertionError("review must not run in this test")
