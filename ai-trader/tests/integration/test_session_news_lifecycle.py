"""Session lifecycle: one shadow session runs the market stream *and* news ingestion.

Also covers the news -> AI news analyst -> Portfolio AI -> Jev -> final AI -> PaperBroker
path with news present, asserting that news influences the decision without ever being
able to produce one.
"""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ai_trader.ai.base import AIProvider
from ai_trader.ai.jev import JevCritic
from ai_trader.ai.portfolio_manager import PortfolioManagerAgent
from ai_trader.application.fyers_session import build_session
from ai_trader.application.workflows.shadow_review import ShadowReviewWorkflow
from ai_trader.brokers.paper import PaperBroker
from ai_trader.config import Settings
from ai_trader.domain.common import SystemClock
from ai_trader.domain.decisions import (
    CriticAssessment,
    PortfolioDecision,
    ProviderMetadata,
)
from ai_trader.domain.market import Instrument, MarketState, MarketTick
from ai_trader.domain.news import (
    AnalysisStatus,
    Direction,
    Horizon,
    Materiality,
    NewsAnalysis,
    NewsCategory,
    NewsItem,
    SourceType,
    TrustLevel,
)
from ai_trader.domain.portfolio import PortfolioState, Position
from ai_trader.execution.service import ExecutionService
from ai_trader.journal.service import DecisionJournal, DecisionRecord
from ai_trader.news.review_context import NewsReviewBundle, build_bundle
from ai_trader.safety.envelope import KillSwitch, SafetyEnvelope

BASE = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_mode="LIVE_SHADOW",
        database_url="sqlite://",
        ai_base_url="http://ai:8080/v1",
        ai_model="m",
        news_enabled=True,
        nse_poll_seconds=5,
        financial_news_poll_seconds=10,
    )


def make_tick(tick_id: str = "t1") -> MarketTick:
    return MarketTick(
        symbol="INFY",
        last_price=Decimal("1500"),
        exchange_timestamp=BASE,
        received_timestamp=BASE,
        processed_timestamp=BASE,
    )


class StubFeed:
    """Minimal FyersFeed stand-in: a socket plus one market state."""

    def __init__(self) -> None:
        self.socket = self
        self.stopped = False

    async def run(self) -> None:
        await asyncio.sleep(3600)

    async def stop(self) -> None:
        self.stopped = True

    def latest(self) -> MarketState:
        instrument = Instrument(instrument_key="NSE:INFY-EQ", symbol="INFY", exchange="NSE")
        return MarketState(
            snapshot_id="snap-1",
            instrument=instrument,
            timestamp=BASE,
            last_price=Decimal("1500"),
            previous_close=Decimal("1490"),
            recent_ticks=[make_tick()],
        )

    def states(self) -> list[MarketState]:
        return [self.latest()]


class EmptyJournal(DecisionJournal):
    async def save(self, record: DecisionRecord) -> None:
        return None

    async def get(self, decision_id: str) -> DecisionRecord | None:
        return None

    async def list_recent(self, limit: int | None = None) -> list[DecisionRecord]:
        return []


class RecordingAI(AIProvider):
    """Returns a HOLD. A hold is a valid AI outcome and places no order."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    @property
    def model_name(self) -> str:
        return "idle-model"

    async def structured_completion(
        self, system_prompt, user_prompt, response_model, *, prompt_name, prompt_version
    ):
        self.prompts.append(user_prompt)
        return PortfolioDecision(
            cash_percent=100.0,
            allocations=[],
            market_summary="Market unchanged.",
            decision_summary="Hold; nothing in the news changes the thesis.",
            proposals=[],
        ), ProviderMetadata(
            provider="remote_openai_compatible",
            model=self.model_name,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
        )


class AgreeingCritic(JevCritic):
    async def assess(self, proposal, news=None):
        return (
            CriticAssessment(
                score=0.9,
                agrees=True,
                disagreement_signals=[],
                critique="No objection.",
                bounded_decisions=["No change requested."],
            ),
            ProviderMetadata(
                provider="mock-jev", model="mock-jev", prompt_name="critic", prompt_version="v1"
            ),
        )


def official_item(item_id: str = "i1") -> NewsItem:
    return NewsItem(
        item_id=item_id,
        source_id="nse-corporate",
        source_name="NSE India",
        source_type=SourceType.OFFICIAL,
        trust_level=TrustLevel.PRIMARY,
        headline="Infosys board approves buyback of 5 crore shares",
        canonical_url=f"https://www.nseindia.com/a/{item_id}",
        url=f"https://www.nseindia.com/a/{item_id}",
        published_at=BASE,
        received_at=BASE.replace(second=1),
        category=NewsCategory.CORPORATE_ANNOUNCEMENT,
        symbols=("INFY",),
        content_hash="a" * 64,
        headline_hash="b" * 64,
        excerpt="Board approved the buyback subject to shareholder approval.",
    )


def official_analysis(item_id: str = "i1", cluster_id: str = "c1") -> NewsAnalysis:
    return NewsAnalysis(
        analysis_id=f"an-{item_id}",
        cluster_id=cluster_id,
        item_id=item_id,
        status=AnalysisStatus.COMPLETED,
        event_type="SHARE_BUYBACK",
        summary="The board approved a buyback of up to 5 crore shares.",
        affected_symbols=("INFY",),
        materiality=Materiality.HIGH,
        direction=Direction.POSITIVE,
        horizon=Horizon.WEEKS,
        confidence=0.8,
        requires_portfolio_review=True,
        analysis_started_at=BASE.replace(second=2),
        analysis_completed_at=BASE.replace(second=6),
    )


# --- session lifecycle -------------------------------------------------------


def test_session_builds_continuous_news_ingestion(settings: Settings) -> None:
    session = build_session(settings, StubFeed(), journal=EmptyJournal())

    assert session.news is not None, "news ingestion must be wired into the session"
    assert session.coordinator is not None, "news must be able to request a review"
    assert session.news.running is False
    intervals = {feed.source_id: feed.interval_seconds for feed in session.news.feeds.values()}
    assert intervals["nse-corporate"] == 5
    assert intervals["livemint-markets"] == 10
    assert intervals["rbi-press-releases"] == 30


def test_session_starts_and_stops_news_with_the_market_feed(settings: Settings) -> None:
    async def run() -> None:
        feed = StubFeed()
        session = build_session(settings, feed, journal=EmptyJournal())
        await session.start_ingestion()
        assert session.news is not None
        assert session.news.running is True, "news ingestion starts with the session"
        assert session.socket_task is not None and not session.socket_task.done()

        await session.aclose()
        assert session.news.running is False, "news ingestion stops with the session"
        assert feed.stopped is True

    asyncio.run(run())


def test_session_reports_provider_status_before_any_poll(settings: Settings) -> None:
    session = build_session(settings, StubFeed(), journal=EmptyJournal())
    assert session.news is not None
    status = session.news.provider_status()
    ids = {entry["source_id"] for entry in status}
    assert {"nse-corporate", "livemint-markets", "rbi-press-releases"} <= ids
    # Nothing has been polled yet, so nothing may claim to be live.
    assert all(entry["status"] == "STOPPED" for entry in status)


def test_news_disabled_means_no_ingestion(settings: Settings) -> None:
    off = settings.model_copy(update={"news_enabled": False})
    session = build_session(off, StubFeed(), journal=EmptyJournal())
    assert session.news is None
    assert session.coordinator is None


# --- news reaches the AI, and the AI still decides ---------------------------


def build_workflow(
    settings: Settings, broker: PaperBroker, ai: RecordingAI, clock: SystemClock
) -> ShadowReviewWorkflow:
    return ShadowReviewWorkflow(
        PortfolioManagerAgent(ai, "v4"),
        AgreeingCritic(),
        broker,
        SafetyEnvelope(settings, KillSwitch(), clock),
        ExecutionService(broker),
        EmptyJournal(),  # type: ignore[arg-type]
        clock,
    )


def test_news_context_reaches_the_portfolio_ai_and_still_places_no_order(
    settings: Settings,
) -> None:
    """The whole chain runs with news present; the AI's HOLD is respected."""
    clock = SystemClock()
    broker = PaperBroker(Decimal("100000"), clock)
    broker.set_price("INFY", Decimal("1500"))
    ai = RecordingAI()
    workflow = build_workflow(settings, broker, ai, clock)

    bundle = build_bundle(trigger=((official_item(), official_analysis()),))

    record = asyncio.run(workflow.run(StubFeed().latest(), bundle))

    prompt = ai.prompts[0]
    assert "ORIGINAL SOURCE INFORMATION" in prompt
    assert "AI NEWS INTERPRETATION" in prompt
    assert "Infosys board approves buyback" in prompt
    assert "SHARE_BUYBACK" in prompt
    # The AI's judgement stands: no proposal, no order, book unchanged.
    assert record.final_ai_decision is not None
    assert record.final_ai_decision.proposals == []
    assert not broker.positions
    assert broker.cash == Decimal("100000")


def test_news_concerning_a_held_position_is_marked_in_the_prompt(
    settings: Settings,
) -> None:
    clock = SystemClock()
    broker = PaperBroker(Decimal("100000"), clock)
    broker.set_price("INFY", Decimal("1500"))
    ai = RecordingAI()
    manager = PortfolioManagerAgent(ai, "v4")

    portfolio = PortfolioState(
        cash=Decimal("1000"),
        positions=[Position(symbol="INFY", quantity=10, average_entry_price=Decimal("1400"))],
    )
    bundle = build_bundle(trigger=((official_item(), official_analysis()),))

    asyncio.run(manager.review(make_tick(), portfolio, bundle))

    prompt = ai.prompts[0]
    assert "CONCERNS A HELD POSITION" in prompt
    # The news is still split into source fact and AI interpretation.
    assert prompt.index("ORIGINAL SOURCE INFORMATION") < prompt.index("AI NEWS INTERPRETATION")


def test_held_position_news_prompt_offers_the_ai_every_outcome(
    settings: Settings,
) -> None:
    """The prompt may open, add, reduce, or close; it never instructs a single action."""
    bundle = NewsReviewBundle(source_items=(official_item(),), analyses=(official_analysis(),))
    prompt = bundle.as_prompt(held_symbols={"INFY"})
    lowered = prompt.lower()
    assert "opening, adding to, reducing, or closing" in lowered
    assert "or doing nothing at all" in lowered


def test_no_news_means_the_prompt_is_unchanged() -> None:
    """With no news the agent must not switch prompts or alter the context."""
    ai = RecordingAI()
    manager = PortfolioManagerAgent(ai, "v4")
    portfolio = PortfolioState(cash=Decimal("100000"))

    asyncio.run(manager.review(make_tick(), portfolio, None))
    prompt = ai.prompts[0]
    assert "ORIGINAL SOURCE INFORMATION" not in prompt
    assert "RECENT NEWS CONTEXT" not in prompt


