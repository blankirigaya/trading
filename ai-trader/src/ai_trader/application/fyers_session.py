"""FYERS-backed shadow trading session.

Ingestion runs on its own task so market data keeps flowing while the remote AI is
thinking. Reviews happen on a fixed cadence; the cadence controls only *when* the AI
thinks, never *what* it decides. News can additionally request an immediate review.

FYERS supplies prices. PaperBroker is the only execution provider.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ai_trader.ai.jev import TypeSafeJevCritic
from ai_trader.ai.portfolio_manager import PortfolioManagerAgent
from ai_trader.ai.remote_model import OpenAICompatibleAIProvider
from ai_trader.ai.retries import RetryingAIProvider
from ai_trader.application.workflows.shadow_review import ShadowReviewWorkflow
from ai_trader.brokers.paper import PaperBroker
from ai_trader.config import Settings
from ai_trader.domain.common import Clock, SystemClock
from ai_trader.domain.decisions import DecisionStatus
from ai_trader.domain.news import NewsAnalysis, NewsItem, NewsPortfolioReviewContext
from ai_trader.execution.service import ExecutionService
from ai_trader.integrations.fyers.feed import FyersFeed, build_feed
from ai_trader.journal.service import DecisionJournal
from ai_trader.news.realtime import RealtimeNewsService
from ai_trader.news.review_bridge import LatencyFn, NewsReviewCoordinator
from ai_trader.news.review_context import NewsReviewBundle
from ai_trader.news.service import NewsIntelligence
from ai_trader.persistence.database import create_database_engine
from ai_trader.persistence.news_models import ensure_news_tables
from ai_trader.persistence.news_repository import NewsRepository
from ai_trader.persistence.repositories import SqlDecisionJournal
from ai_trader.safety.envelope import KillSwitch, SafetyEnvelope

logger = logging.getLogger(__name__)

EmitFn = Callable[..., None]


@dataclass
class SessionResult:
    record_id: str
    status: str
    failure_reason: str | None = None


@dataclass
class ShadowSession:
    """Owns the FYERS feed, continuous news ingestion, the paper account, and the AI loop.

    Concurrency: the FYERS socket, each news feed, and the news AI worker are separate
    tasks with separate queues. News backpressure can only slow the news tasks, so the
    market stream is never held up by the news analyst.
    """

    settings: Settings
    feed: FyersFeed
    broker: PaperBroker
    workflow: ShadowReviewWorkflow
    journal: DecisionJournal
    clock: Clock
    interval_seconds: int
    remote_provider: OpenAICompatibleAIProvider
    news: RealtimeNewsService | None = None
    coordinator: NewsReviewCoordinator | None = None
    socket_task: asyncio.Task[None] | None = None
    review_task: asyncio.Task[None] | None = None
    reviews: int = 0
    news_reviews: int = 0
    _review_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def start_ingestion(self) -> None:
        """Start the market feed and, if configured, continuous news ingestion."""
        self.socket_task = asyncio.create_task(self.feed.socket.run(), name="fyers-socket")
        if self.news is not None:
            self.news.intelligence.pipeline().start()
            await self.news.start()
        if self.coordinator is not None:
            # A news-triggered review must not run concurrently with the cadence review.
            self.review_task = asyncio.create_task(
                self._review_from_news_forever(), name="news-review"
            )

    # --- reviews ------------------------------------------------------------
    async def review_once(self) -> SessionResult | None:
        """Run one AI review against the latest real market state."""
        return await self._review(trigger="CADENCE")

    async def _review_from_news_forever(self) -> None:
        """Consume news-triggered reviews as the coordinator produces bundles."""
        assert self.coordinator is not None
        while True:
            bundle = await self.coordinator.wait_for_bundle()
            if bundle is None:
                return
            await self._review(bundle=bundle, trigger="NEWS")

    async def _review(
        self, bundle: NewsReviewBundle | None = None, *, trigger: str = "CADENCE"
    ) -> SessionResult | None:
        """Run a review, serialised so two reviews never overlap on the paper book."""
        state = self.feed.latest()
        if state is None:
            logger.info("no market state yet; skipping %s review", trigger)
            return None
        async with self._review_lock:
            # Mark the paper book to real prices before the AI sees valuation.
            self.broker.mark_to_market(self._paper_prices())
            record = await self.workflow.run(state, bundle)
            self.reviews += 1
            if trigger == "NEWS":
                self.news_reviews += 1
            logger.info(
                "%s review %s status=%s proposals=%d",
                trigger,
                record.decision_id,
                record.status.value,
                len(record.final_ai_decision.proposals) if record.final_ai_decision else 0,
            )
            return SessionResult(record.decision_id, record.status.value, record.failure_reason)

    def _paper_prices(self) -> dict[str, Decimal]:
        """Real FYERS prices keyed by the internal symbol the paper broker tracks."""
        return {state.instrument.symbol: state.last_price for state in self.feed.states()}

    async def run_loop(self, max_reviews: int | None = None) -> list[SessionResult]:
        """Review on the configured cadence until stopped or ``max_reviews`` reached.

        News-triggered reviews run on their own task, so this loop is unaffected by them.
        """
        results: list[SessionResult] = []
        while max_reviews is None or len(results) < max_reviews:
            await asyncio.sleep(self.interval_seconds)
            if self.socket_task is not None and self.socket_task.done():
                logger.warning("market data socket stopped; reconnect handling is active")
            result = await self.review_once()
            if result is not None:
                results.append(result)
        return results

    async def aclose(self) -> None:
        """Stop news ingestion cleanly, then the market feed and remote connections."""
        if self.review_task is not None:
            self.review_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.review_task
            self.review_task = None
        if self.news is not None:
            await self.news.intelligence.pipeline().stop()
            await self.news.stop()
        await self.feed.socket.stop()
        if self.socket_task is not None:
            self.socket_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.socket_task
        await self.remote_provider.aclose()


def build_session(
    settings: Settings,
    feed: FyersFeed,
    clock: Clock | None = None,
    journal: DecisionJournal | None = None,
    event_sink: EmitFn | None = None,
) -> ShadowSession:
    """Assemble a shadow session. Execution is always PaperBroker."""
    active_clock = clock or SystemClock()

    # Hard invariant: the only broker constructed here is PaperBroker.
    broker = PaperBroker(settings.shadow_initial_capital_inr, active_clock)
    for state in feed.states():
        broker.set_price(state.instrument.symbol, state.last_price)

    engine = create_database_engine(settings.database_url)
    active_journal = journal or SqlDecisionJournal(engine)

    remote_provider = OpenAICompatibleAIProvider(
        settings.ai_base_url,
        settings.ai_model,
        settings.ai_api_key,
        settings.ai_timeout_seconds,
        settings.ai_max_concurrent_requests,
    )
    provider = RetryingAIProvider(remote_provider, settings.ai_schema_max_retries)

    news_repository = NewsRepository(engine)
    if settings.news_enabled:
        ensure_news_tables(engine)
    manager = PortfolioManagerAgent(provider, "v2")
    critic = TypeSafeJevCritic(
        settings.jev_base_url, settings.jev_api_key or "", settings.jev_model
    )
    safety = SafetyEnvelope(settings, KillSwitch(), active_clock)
    execution = ExecutionService(broker)

    realtime: RealtimeNewsService | None = None
    coordinator: NewsReviewCoordinator | None = None
    intelligence: NewsIntelligence | None = None

    if settings.news_enabled:
        from ai_trader.news.sources import enabled_sources

        intelligence = NewsIntelligence(
            settings, news_repository, ai_provider=provider, emit=event_sink
        )
        realtime = RealtimeNewsService(
            settings=settings,
            intelligence=intelligence,
            sources=enabled_sources(),
            emit=event_sink,
        )
        coordinator = NewsReviewCoordinator(
            held_symbols=lambda: {
                position.symbol for position in broker.positions.values() if position.quantity
            },
            recent_items=lambda: tuple(news_repository.recent_items(limit=15)),
            recent_analyses=lambda: tuple(news_repository.recent_analyses(limit=15)),
            emit=event_sink,
            review_held_positions=settings.news_review_held_positions,
        )

    workflow = ShadowReviewWorkflow(
        manager,
        critic,
        broker,
        safety,
        execution,
        active_journal,
        active_clock,
        event_sink=event_sink,
        latency_sink=_latency_sink(news_repository) if settings.news_enabled else None,
    )

    session = ShadowSession(
        settings=settings,
        feed=feed,
        broker=broker,
        workflow=workflow,
        journal=active_journal,
        clock=active_clock,
        interval_seconds=settings.portfolio_review_interval_seconds,
        remote_provider=remote_provider,
        news=realtime,
        coordinator=coordinator,
    )
    if intelligence is not None and coordinator is not None:
        _connect_news_to_review(intelligence, coordinator)
    return session


def _latency_sink(repository: NewsRepository) -> LatencyFn:
    """Append workflow stages to the news latency chain."""

    def sink(stage: str, analysis_id: str, when: datetime) -> None:
        repository.record_latency(
            analysis_id,
            review_started_at=when if stage == "review_started" else None,
            decision_at=when if stage == "decision" else None,
            paper_order_at=when if stage == "paper_order" else None,
        )

    return sink


def _connect_news_to_review(
    intelligence: NewsIntelligence, coordinator: NewsReviewCoordinator
) -> None:
    """Wire completed news analyses into the immediate-review buffer.

    This is the only place the news module meets the review trigger. The trigger gate is
    installed on the pipeline so cluster-level review deduplication still applies, and
    the buffer is consumed by the session's own task, so news cannot reach execution.
    """

    async def on_review(
        item: NewsItem, analysis: NewsAnalysis, context: NewsPortfolioReviewContext
    ) -> None:
        reason = coordinator.should_review(item, analysis)
        if reason is None:
            return
        coordinator.request(item, analysis, reason)

    intelligence.set_review_handler(on_review)
    # News about a held position is worth reviewing even when the analyst did not
    # explicitly ask for it, so an open position can be re-examined on new information.
    intelligence.set_review_gate(
        lambda item, analysis: coordinator.should_review(item, analysis) is not None
    )


async def build_session_from_settings(
    settings: Settings, symbols: list[str] | None = None
) -> ShadowSession:
    feed = await build_feed(settings, symbols)
    return build_session(settings, feed)


def is_trade_decision(status: DecisionStatus) -> bool:
    return status in (DecisionStatus.EXECUTED, DecisionStatus.PARTIALLY_EXECUTED)


def summarise_portfolio(broker: PaperBroker) -> dict[str, object]:
    return {
        "cash": str(broker.cash),
        "unrealized_pnl": str(broker.unrealized_pnl()),
        "positions": [
            {
                "symbol": position.symbol,
                "quantity": position.quantity,
                "average_entry_price": str(position.average_entry_price),
            }
            for position in broker.positions.values()
            if position.quantity
        ],
    }
