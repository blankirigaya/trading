from decimal import Decimal

from sqlalchemy import Table

from ai_trader.ai.jev import MockJevCritic, TypeSafeJevCritic
from ai_trader.ai.mock import MockAIProvider
from ai_trader.ai.portfolio_manager import PortfolioManagerAgent
from ai_trader.ai.remote_model import (
    AIHealth,
    OpenAICompatibleAIProvider,
    sanitize_server_identifier,
)
from ai_trader.ai.retries import RetryingAIProvider
from ai_trader.ai.scouts import MockMarketScout
from ai_trader.api.events import EventHub, new_event
from ai_trader.application.orchestrator import ApplicationOrchestrator
from ai_trader.application.runtime import ShadowRuntime
from ai_trader.application.workflows.portfolio_review import PortfolioReviewWorkflow
from ai_trader.application.workflows.shadow_review import ShadowReviewWorkflow
from ai_trader.brokers.paper import PaperBroker
from ai_trader.config import Settings
from ai_trader.domain.common import Clock, SystemClock
from ai_trader.domain.decisions import (
    PortfolioAction,
    PortfolioDecision,
    TradeProposal,
)
from ai_trader.domain.events import MarketTickReceived
from ai_trader.domain.market import MarketState
from ai_trader.domain.news import (
    NewsAnalysis,
    NewsItem,
    NewsPortfolioReviewContext,
)
from ai_trader.domain.orders import OrderSide
from ai_trader.events.bus import EventBus
from ai_trader.execution.service import ExecutionService
from ai_trader.journal.service import InMemoryDecisionJournal
from ai_trader.news.realtime import RealtimeNewsService
from ai_trader.news.service import NewsIntelligence
from ai_trader.persistence.database import create_database_engine
from ai_trader.persistence.models import Base
from ai_trader.persistence.news_repository import NewsRepository
from ai_trader.safety.envelope import KillSwitch, SafetyEnvelope


def news_tables() -> list[Table]:
    """The news table objects. Loaded lazily to keep container import-light."""
    from ai_trader.persistence.news_models import news_table_objects

    return news_table_objects()


class Container:
    def __init__(self, settings: Settings | None = None, clock: Clock | None = None) -> None:
        self.settings = settings or Settings()
        self.clock = clock or SystemClock()
        self.bus = EventBus()
        self.kill_switch = KillSwitch()
        self.broker = PaperBroker(self.settings.paper_initial_capital, self.clock)
        response = PortfolioDecision(
            cash_percent=90.0,
            allocations=[],
            market_summary="Mock market context.",
            decision_summary="Mock proposal for the deterministic Phase 1 demo.",
            proposals=[
                TradeProposal(
                    proposal_id="mock-proposal-v1",
                    symbol="TESTSTOCK",
                    action=PortfolioAction.OPEN_POSITION,
                    side=OrderSide.BUY,
                    capital_allocation_inr=Decimal("10000"),
                    confidence=0.65,
                    thesis="Mock thesis for exercising the safety and execution path.",
                    evidence=["Synthetic replay tick"],
                    conditions_that_change_thesis=["Mock input changes"],
                )
            ],
        )
        self.journal = InMemoryDecisionJournal()
        self.execution = ExecutionService(self.broker)
        self.workflow = PortfolioReviewWorkflow(
            bus=self.bus,
            scout=MockMarketScout(),
            manager=PortfolioManagerAgent(MockAIProvider(response)),
            critic=MockJevCritic(),
            broker=self.broker,
            safety=SafetyEnvelope(self.settings, self.kill_switch, self.clock),
            execution=self.execution,
            journal=self.journal,
            clock=self.clock,
        )
        self.orchestrator = ApplicationOrchestrator(self.workflow)
        self.bus.subscribe(MarketTickReceived, self.orchestrator.on_market_tick)
        self.ai_health = AIHealth()
        self.ai_provider: OpenAICompatibleAIProvider | None = None
        self.market_connected = False
        self.event_hub = EventHub()
        # Optional live shadow runtime, attached by the session runner. The API
        # never constructs one, so no market or AI orchestration lives in routes.
        self.runtime: ShadowRuntime | None = None
        self._market_snapshots: dict[str, dict[str, object]] = {}
        self._market_candles: dict[str, list[dict[str, object]]] = {}
        self._news: NewsIntelligence | None = None
        # Set when a shadow session owns continuous ingestion. The API only reads it.
        self.realtime_news: RealtimeNewsService | None = None
        self.event_hub.publish(
            new_event(
                "SYSTEM",
                "backend_started",
                f"AI trader backend started in {self.settings.app_mode} mode",
            )
        )
        self.shadow_workflow = self._build_shadow_workflow()

    def publish_event(self, category: str, kind: str, message: str) -> None:
        """Sink used by workflows to surface timeline events without coupling."""
        self.event_hub.publish(new_event(category, kind, message))

    def record_market_state(self, states: list[MarketState]) -> None:
        """Store the latest normalized state per instrument for UI snapshots."""
        self.market_connected = bool(states)
        for state in states:
            self._market_snapshots[state.instrument.symbol] = {
                **state.model_dump(mode="json"),
                "instrument": state.instrument.model_dump(mode="json"),
            }
            if state.current_1m_candle is not None:
                bucket = self._market_candles.setdefault(state.instrument.symbol, [])
                candle = state.current_1m_candle.model_dump(mode="json")
                if not bucket or bucket[-1]["end_timestamp"] != candle["end_timestamp"]:
                    bucket.append(candle)
                    del bucket[:-240]
            self.event_hub.publish(
                new_event(
                    "MARKET",
                    "market_update",
                    f"{state.instrument.symbol} at {state.last_price}",
                    symbol=state.instrument.symbol,
                )
            )

    def market_snapshots(self) -> list[dict[str, object]]:
        return list(self._market_snapshots.values())

    def market_candles(self, symbol: str) -> list[dict[str, object]]:
        return list(self._market_candles.get(symbol, []))

    def news_store(self) -> NewsRepository:
        return NewsRepository(create_database_engine(self.settings.database_url))

    def news_intelligence(self) -> NewsIntelligence:
        """The shared news service, including its event emitter and review handler.

        Only constructed on demand so a process that never reads news pays nothing.
        """
        if self._news is None:
            self.ensure_news_schema()
            self._news = NewsIntelligence(
                self.settings,
                self.news_store(),
                ai_provider=self.ai_provider,
                emit=self._emit_news_event,
            )
            self._news.set_review_handler(self._on_news_review)
        return self._news

    def _emit_news_event(self, category: str, kind: str, message: str) -> None:
        self.event_hub.publish(new_event(category, kind, message))

    async def _on_news_review(
        self,
        item: NewsItem,
        analysis: NewsAnalysis,
        context: NewsPortfolioReviewContext,
    ) -> None:
        """Queue a news item for the normal portfolio review.

        News can only add context to a review. It never places an order and never
        reaches the broker directly.
        """
        self.event_hub.publish(
            new_event(
                "SYSTEM",
                "news_portfolio_review_scheduled",
                f"Queued portfolio review for {context.symbols or 'the market'}",
                data={"item_id": item.item_id, "event_type": context.ai_event_type},
            )
        )

    def ensure_news_schema(self) -> None:
        """Create the news tables for SQLite so the UI works without alembic."""
        if not self.settings.database_url.startswith("sqlite"):
            return
        engine = create_database_engine(self.settings.database_url)
        try:
            Base.metadata.create_all(engine, tables=news_tables())
        finally:
            engine.dispose()

    async def aclose(self) -> None:
        """Release pooled network resources on application shutdown."""
        if self.ai_provider is not None:
            await self.ai_provider.aclose()
            self.ai_provider = None

    @property
    def configured_ai_server(self) -> str | None:
        """Sanitized host[:port] for reporting. No credentials, scheme, or path."""
        if not self.settings.ai_base_url:
            return None
        return sanitize_server_identifier(self.settings.ai_base_url)

    def _build_shadow_workflow(self) -> ShadowReviewWorkflow | None:
        if self.settings.app_mode != "LIVE_SHADOW":
            return None
        if not self.settings.jev_enabled or not self.settings.jev_api_key:
            return None
        if not self.settings.ai_base_url or not self.settings.ai_model:
            return None
        self.ai_provider = OpenAICompatibleAIProvider(
            self.settings.ai_base_url,
            self.settings.ai_model,
            self.settings.ai_api_key,
            self.settings.ai_timeout_seconds,
            self.settings.ai_max_concurrent_requests,
            health=self.ai_health,
        )
        provider = RetryingAIProvider(self.ai_provider, self.settings.ai_schema_max_retries)
        # LIVE_SHADOW permanently injects the already-created PaperBroker, never UpstoxBroker.
        return ShadowReviewWorkflow(
            PortfolioManagerAgent(provider, "v2"),
            TypeSafeJevCritic(
                self.settings.jev_base_url, self.settings.jev_api_key, self.settings.jev_model
            ),
            self.broker,
            SafetyEnvelope(self.settings, self.kill_switch, self.clock),
            ExecutionService(self.broker),
            self.journal,
            self.clock,
        )
