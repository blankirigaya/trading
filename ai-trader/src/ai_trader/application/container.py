from datetime import datetime
from decimal import Decimal

from sqlalchemy import Table

from ai_trader.ai.base import AIProvider
from ai_trader.ai.entry_plan import EntryPlanRequired
from ai_trader.ai.jev import MockJevCritic, build_critic
from ai_trader.ai.mock import MockAIProvider
from ai_trader.ai.portfolio_manager import PortfolioManagerAgent, position_limits_text
from ai_trader.ai.providers import build_ai_provider
from ai_trader.ai.remote_model import AIHealth, sanitize_server_identifier
from ai_trader.ai.retries import RetryingAIProvider
from ai_trader.ai.scouts import MockMarketScout
from ai_trader.api.events import EventHub, new_event
from ai_trader.application.orchestrator import ApplicationOrchestrator
from ai_trader.application.runtime import ManagedShadowRuntime, ShadowRuntime
from ai_trader.application.universe import DEFAULT_UNIVERSE_MODE
from ai_trader.application.workflows.portfolio_review import PortfolioReviewWorkflow
from ai_trader.application.workflows.shadow_review import ShadowReviewWorkflow
from ai_trader.brokers.charges import charge_rates_for
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
from ai_trader.journal.service import DecisionJournal, InMemoryDecisionJournal
from ai_trader.news.realtime import RealtimeNewsService
from ai_trader.news.service import NewsIntelligence
from ai_trader.persistence.control_models import ensure_control_tables
from ai_trader.persistence.control_store import ControlStore
from ai_trader.persistence.database import create_database_engine
from ai_trader.persistence.models import Base
from ai_trader.persistence.news_repository import NewsRepository
from ai_trader.risk.entry_plan import RiskRules
from ai_trader.safety.envelope import KillSwitch, SafetyEnvelope


def news_tables() -> list[Table]:
    """The news table objects. Loaded lazily to keep container import-light."""
    from ai_trader.persistence.news_models import news_table_objects

    return news_table_objects()


# The timeline stays useful without one event per tick: one market_update per
# symbol per interval matches the old review-rate volume and keeps decisions in
# the bounded buffer. Snapshots themselves update on every tick.
MARKET_EVENT_INTERVAL_SECONDS = 60.0


class Container:
    def __init__(self, settings: Settings | None = None, clock: Clock | None = None) -> None:
        self.settings = settings or Settings()
        self.clock = clock or SystemClock()
        self.bus = EventBus()
        self.kill_switch = KillSwitch()
        # Operator override for the fixed AI money caps. Loaded from the
        # persisted control table on first read, like the universe mode.
        self.capital_cap_disabled: bool = self.settings.capital_cap_disabled
        self._capital_cap_loaded = False
        # Operator override for the max-open-positions cap, same lifecycle.
        self.position_count_cap_disabled: bool = self.settings.position_count_cap_disabled
        self._position_count_cap_loaded = False
        # News kill switches, same lifecycle: Tier-1 desks, AI analysis of
        # news items, and the Sonnet escalation lever for news reviews.
        self.tier1_news_enabled: bool = self.settings.tier1_news_enabled
        self._tier1_news_loaded = False
        self.news_analysis_enabled: bool = self.settings.news_analysis_enabled
        self._news_analysis_loaded = False
        self.sonnet_escalation_enabled: bool = self.settings.sonnet_escalation_enabled
        self._sonnet_escalation_loaded = False
        self.broker = PaperBroker(
            self.settings.paper_initial_capital,
            self.clock,
            margin_multiple=self.settings.paper_margin_multiple,
            charge_rates=charge_rates_for(self.settings),
        )
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
        self.journal: DecisionJournal = InMemoryDecisionJournal()
        self.shard_brokers: list[PaperBroker] = []
        self.execution = ExecutionService(self.broker)
        self.workflow = PortfolioReviewWorkflow(
            bus=self.bus,
            scout=MockMarketScout(),
            manager=PortfolioManagerAgent(
                MockAIProvider(response),
                position_limits=position_limits_text(
                    self.settings,
                    capital_cap_disabled=self.capital_cap_disabled,
                    position_count_cap_disabled=self.position_count_cap_disabled,
                ),
            ),
            critic=MockJevCritic(),
            broker=self.broker,
            safety=SafetyEnvelope(
                self.settings,
                self.kill_switch,
                self.clock,
                capital_cap_disabled=self.capital_cap_disabled,
                position_count_cap_disabled=self.position_count_cap_disabled,
            ),
            execution=self.execution,
            journal=self.journal,
            clock=self.clock,
        )
        self.orchestrator = ApplicationOrchestrator(self.workflow)
        self.bus.subscribe(MarketTickReceived, self.orchestrator.on_market_tick)
        self.ai_health = AIHealth()
        self.ai_provider: AIProvider | None = None
        self.market_connected = False
        # Operator-selected review universe. Loaded from the persisted control
        # table on first read so a switch survives a restart.
        self.universe_mode: str = self.settings.universe_mode or DEFAULT_UNIVERSE_MODE
        self._universe_mode_loaded = False
        self.event_hub = EventHub()
        # Optional live shadow runtime, attached by the session runner. The API
        # never constructs one, so no market or AI orchestration lives in routes.
        self.runtime: ShadowRuntime | None = None
        self._market_snapshots: dict[str, dict[str, object]] = {}
        self._market_candles: dict[str, list[dict[str, object]]] = {}
        self._last_market_state_at: datetime | None = None
        self._market_event_at: dict[str, datetime] = {}
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

    def publish_event(
        self, category: str, kind: str, message: str, symbol: str | None = None
    ) -> None:
        """Sink used by workflows to surface timeline events without coupling."""
        self.event_hub.publish(new_event(category, kind, message, symbol=symbol))

    async def attach_runtime(self) -> ShadowRuntime:
        """Build the live shadow session in this process and attach it for the API.

        Shares the container's AI health and event hub so the monitoring UI
        reflects the running session, and reports market states for the strip.
        """
        from ai_trader.application.fyers_session import build_session_from_settings
        from ai_trader.application.runtime import ManagedShadowRuntime

        if self.runtime is None:
            session = await build_session_from_settings(
                self.settings,
                health=self.ai_health,
                event_sink=self.publish_event,
                market_sink=self.record_market_state,
                kill_switch=self.kill_switch,
                universe_sink=self.prune_market_snapshots,
            )
            self.runtime = ManagedShadowRuntime(session)
            self.universe_mode = session.universe_mode
            self._universe_mode_loaded = True
            self.capital_cap_disabled = session.capital_cap_disabled
            self._capital_cap_loaded = True
            self.position_count_cap_disabled = session.position_count_cap_disabled
            self._position_count_cap_loaded = True
            self.tier1_news_enabled = session.tier1_news_enabled
            self._tier1_news_loaded = True
            self.news_analysis_enabled = session.news_analysis_enabled
            self._news_analysis_loaded = True
            self.sonnet_escalation_enabled = session.sonnet_escalation_enabled
            self._sonnet_escalation_loaded = True
            # The API reads the paper books and journal off the container, so point
            # them at the live session's stores once it owns trading.
            self.shard_brokers = [shard.broker for shard in session.shards]
            self.journal = session.journal
            # Restore persisted open books (positions + cash) so a backend
            # restart resumes trading instead of flattening it.
            for broker in self.shard_brokers:
                await broker.restore()
        return self.runtime

    def record_market_state(self, states: list[MarketState]) -> None:
        """Store the latest normalized state per instrument for UI snapshots.

        Called from the socket task for every decoded batch, so it must stay
        cheap and never raise. Snapshots update on every tick; only the
        timeline event is throttled.
        """
        if not states:
            return
        now = self.clock.now()
        self.market_connected = True
        for state in states:
            symbol = state.instrument.symbol
            self._market_snapshots[symbol] = {
                **state.model_dump(mode="json"),
                "instrument": state.instrument.model_dump(mode="json"),
            }
            if state.current_1m_candle is not None:
                bucket = self._market_candles.setdefault(symbol, [])
                candle = state.current_1m_candle.model_dump(mode="json")
                if not bucket or bucket[-1]["end_timestamp"] != candle["end_timestamp"]:
                    bucket.append(candle)
                    del bucket[:-240]
            last_event = self._market_event_at.get(symbol)
            if last_event is None or (now - last_event).total_seconds() >= MARKET_EVENT_INTERVAL_SECONDS:
                self._market_event_at[symbol] = now
                self.event_hub.publish(
                    new_event(
                        "MARKET",
                        "market_update",
                        f"{symbol} at {state.last_price}",
                        symbol=symbol,
                    )
                )
        self._last_market_state_at = now

    def market_snapshots(self) -> list[dict[str, object]]:
        """Latest state per instrument, without per-tick history.

        The UI polls this every second. Tick and candle history is ~95% of the
        payload and no panel reads it from here (charts use /market/candles),
        so trimming it keeps a 50-symbol poll at a few KB instead of ~300KB.
        """
        return [
            {
                key: value
                for key, value in snapshot.items()
                if key not in {"recent_ticks", "recent_1m_candles"}
            }
            for snapshot in self._market_snapshots.values()
        ]

    def prune_market_snapshots(self, symbols: list[str]) -> None:
        """Drop snapshots for symbols the feed no longer subscribes to.

        Without this the market strip keeps rendering every universe the
        operator ever selected, with frozen timestamps.
        """
        keep = {symbol.upper() for symbol in symbols}
        if not keep:
            return
        self._market_snapshots = {
            symbol: snapshot
            for symbol, snapshot in self._market_snapshots.items()
            if symbol.upper() in keep
        }
        self._market_candles = {
            symbol: candles
            for symbol, candles in self._market_candles.items()
            if symbol.upper() in keep
        }

    def _control_store(self) -> ControlStore:
        engine = create_database_engine(self.settings.database_url)
        ensure_control_tables(engine)
        return ControlStore(engine)

    async def get_universe_mode(self) -> str:
        """Persisted universe mode, loaded once per process."""
        if not self._universe_mode_loaded:
            try:
                self.universe_mode = await self._control_store().get_universe_mode(
                    self.settings.universe_mode
                )
            except Exception:
                # A control-table hiccup must not take down status reporting.
                self.universe_mode = self.settings.universe_mode or DEFAULT_UNIVERSE_MODE
            self._universe_mode_loaded = True
        return self.universe_mode

    async def set_universe_mode(self, mode: str) -> str:
        normalized = await self._control_store().set_universe_mode(mode)
        self.universe_mode = normalized
        self._universe_mode_loaded = True
        return normalized

    async def get_capital_cap_disabled(self) -> bool:
        """Persisted capital-cap override, loaded once per process."""
        if not self._capital_cap_loaded:
            try:
                self.capital_cap_disabled = await self._control_store().get_capital_cap_disabled(
                    self.settings.capital_cap_disabled
                )
            except Exception:
                # A control-table hiccup must not take down status reporting.
                self.capital_cap_disabled = self.settings.capital_cap_disabled
            self._capital_cap_loaded = True
            self._apply_capital_cap()
        return self.capital_cap_disabled

    async def set_capital_cap_disabled(self, disabled: bool) -> bool:
        applied = await self._control_store().set_capital_cap_disabled(disabled)
        self.capital_cap_disabled = applied
        self._capital_cap_loaded = True
        self._apply_capital_cap()
        return applied

    def _apply_capital_cap(self) -> None:
        """Push the overrides into every live safety envelope and prompt."""
        limits = position_limits_text(
            self.settings,
            capital_cap_disabled=self.capital_cap_disabled,
            position_count_cap_disabled=self.position_count_cap_disabled,
        )
        for workflow in (self.workflow, self.shadow_workflow):
            if workflow is None:
                continue
            workflow.safety.capital_cap_disabled = self.capital_cap_disabled
            workflow.safety.position_count_cap_disabled = self.position_count_cap_disabled
            workflow.manager.set_position_limits(limits)
        if isinstance(self.runtime, ManagedShadowRuntime):
            self.runtime.session.set_capital_cap_disabled(self.capital_cap_disabled)
            self.runtime.session.set_position_count_cap_disabled(
                self.position_count_cap_disabled
            )

    async def get_position_count_cap_disabled(self) -> bool:
        """Persisted position-count override, loaded once per process."""
        if not self._position_count_cap_loaded:
            try:
                self.position_count_cap_disabled = (
                    await self._control_store().get_position_count_cap_disabled(
                        self.settings.position_count_cap_disabled
                    )
                )
            except Exception:
                # A control-table hiccup must not take down status reporting.
                self.position_count_cap_disabled = (
                    self.settings.position_count_cap_disabled
                )
            self._position_count_cap_loaded = True
            self._apply_capital_cap()
        return self.position_count_cap_disabled

    async def set_position_count_cap_disabled(self, disabled: bool) -> bool:
        applied = await self._control_store().set_position_count_cap_disabled(disabled)
        self.position_count_cap_disabled = applied
        self._position_count_cap_loaded = True
        self._apply_capital_cap()
        return applied

    async def get_tier1_news_enabled(self) -> bool:
        """Persisted Tier-1 feeds switch, loaded once per process."""
        if not self._tier1_news_loaded:
            try:
                self.tier1_news_enabled = await self._control_store().get_tier1_news_enabled(
                    self.settings.tier1_news_enabled
                )
            except Exception:
                # A control-table hiccup must not take down status reporting.
                self.tier1_news_enabled = self.settings.tier1_news_enabled
            self._tier1_news_loaded = True
            await self._apply_news_switches()
        return self.tier1_news_enabled

    async def set_tier1_news_enabled(self, enabled: bool) -> bool:
        applied = await self._control_store().set_tier1_news_enabled(enabled)
        self.tier1_news_enabled = applied
        self._tier1_news_loaded = True
        await self._apply_news_switches()
        return applied

    async def get_news_analysis_enabled(self) -> bool:
        """Persisted analysis kill switch, loaded once per process."""
        if not self._news_analysis_loaded:
            try:
                self.news_analysis_enabled = (
                    await self._control_store().get_news_analysis_enabled(
                        self.settings.news_analysis_enabled
                    )
                )
            except Exception:
                # A control-table hiccup must not take down status reporting.
                self.news_analysis_enabled = self.settings.news_analysis_enabled
            self._news_analysis_loaded = True
            await self._apply_news_switches()
        return self.news_analysis_enabled

    async def set_news_analysis_enabled(self, enabled: bool) -> bool:
        applied = await self._control_store().set_news_analysis_enabled(enabled)
        self.news_analysis_enabled = applied
        self._news_analysis_loaded = True
        await self._apply_news_switches()
        return applied

    async def get_sonnet_escalation_enabled(self) -> bool:
        """Persisted Sonnet-escalation lever, loaded once per process."""
        if not self._sonnet_escalation_loaded:
            try:
                self.sonnet_escalation_enabled = (
                    await self._control_store().get_sonnet_escalation_enabled(
                        self.settings.sonnet_escalation_enabled
                    )
                )
            except Exception:
                # A control-table hiccup must not take down status reporting.
                self.sonnet_escalation_enabled = self.settings.sonnet_escalation_enabled
            self._sonnet_escalation_loaded = True
            await self._apply_news_switches()
        return self.sonnet_escalation_enabled

    async def set_sonnet_escalation_enabled(self, enabled: bool) -> bool:
        applied = await self._control_store().set_sonnet_escalation_enabled(enabled)
        self.sonnet_escalation_enabled = applied
        self._sonnet_escalation_loaded = True
        await self._apply_news_switches()
        return applied

    async def _apply_news_switches(self) -> None:
        """Push the news kill switches into every live consumer, no restart."""
        from ai_trader.news.sources import TIER1_SOURCE_IDS

        runtime = self.runtime
        session = (
            runtime.session if isinstance(runtime, ManagedShadowRuntime) else None
        )
        if session is not None:
            if session.news is not None:
                for source_id in TIER1_SOURCE_IDS:
                    await session.news.set_source_enabled(
                        source_id, self.tier1_news_enabled
                    )
            if session.news is not None and session.news.intelligence is not None:
                session.news.intelligence.analysis_enabled = self.news_analysis_enabled
            session.set_sonnet_escalation_enabled(self.sonnet_escalation_enabled)
            session.tier1_news_enabled = self.tier1_news_enabled
            session.news_analysis_enabled = self.news_analysis_enabled
        if self._news is not None:
            self._news.analysis_enabled = self.news_analysis_enabled

    def market_snapshot_age_seconds(self) -> float | None:
        """Age of the newest snapshot served to the UI, or None before any tick.

        This is the honest freshness number: it is driven by the feed, not by
        the review loop, so it keeps rising when ingestion stalls while the
        rest of the backend still answers requests.
        """
        if self._last_market_state_at is None:
            return None
        return max(0.0, (self.clock.now() - self._last_market_state_at).total_seconds())

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
        provider_name = self.settings.ai_provider.strip().lower()
        if provider_name != "bedrock" and (
            not self.settings.ai_base_url or not self.settings.ai_model
        ):
            return None
        try:
            self.ai_provider = build_ai_provider(self.settings, self.ai_health)
        except ValueError:
            return None
        # Routine reviews run on the cadence model when configured (falls back
        # to the main model); news intelligence keeps self.ai_provider.
        provider = RetryingAIProvider(
            EntryPlanRequired(build_ai_provider(self.settings, self.ai_health, cadence=True)),
            self.settings.ai_schema_max_retries,
        )
        # LIVE_SHADOW permanently injects the already-created PaperBroker, never UpstoxBroker.
        return ShadowReviewWorkflow(
            PortfolioManagerAgent(
                provider,
                "v19",
                position_limits=position_limits_text(
                    self.settings,
                    capital_cap_disabled=self.capital_cap_disabled,
                    position_count_cap_disabled=self.position_count_cap_disabled,
                ),
            ),
            build_critic(self.settings),
            self.broker,
            SafetyEnvelope(
                self.settings,
                self.kill_switch,
                self.clock,
                capital_cap_disabled=self.capital_cap_disabled,
                position_count_cap_disabled=self.position_count_cap_disabled,
            ),
            ExecutionService(self.broker),
            self.journal,
            self.clock,
            jev_min_entry_score=self.settings.jev_min_entry_score,
            risk_rules=RiskRules.from_settings(self.settings),
        )
