"""FYERS-backed shadow trading session.

Ingestion runs on its own task so market data keeps flowing while the remote AI is
thinking. Reviews happen on a fixed cadence; the cadence controls only *when* the AI
thinks, never *what* it decides. News can additionally request an immediate review.

FYERS supplies prices. PaperBroker is the only execution provider.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from ai_trader.ai.base import AIProvider
from ai_trader.ai.entry_plan import EntryPlanRequired
from ai_trader.ai.jev import build_critic
from ai_trader.ai.portfolio_manager import PortfolioManagerAgent, position_limits_text
from ai_trader.ai.providers import build_ai_provider
from ai_trader.ai.remote_model import AIHealth
from ai_trader.ai.retries import RetryingAIProvider
from ai_trader.application.market_history import MarketHistory
from ai_trader.application.universe import (
    CHEAP_MAX_PRICE_INR,
    DEFAULT_UNIVERSE_MODE,
    DEFAULT_UNIVERSE_SIZE,
    price_allowed,
    resolve_master,
    select_universe,
)
from ai_trader.application.workflows.shadow_review import ShadowReviewWorkflow
from ai_trader.brokers.charges import charge_rates_for
from ai_trader.brokers.paper import PaperBroker
from ai_trader.config import Settings
from ai_trader.domain.common import Clock, SystemClock
from ai_trader.domain.decisions import DecisionStatus, PortfolioAction, TradeProposal
from ai_trader.domain.market import Candle, MarketState, stale_data_reason
from ai_trader.domain.news import NewsAnalysis, NewsItem, NewsPortfolioReviewContext
from ai_trader.domain.orders import Order, OrderSide
from ai_trader.execution.service import ExecutionAdjustmentRequired, ExecutionService
from ai_trader.integrations.fyers.feed import FyersFeed, build_feed
from ai_trader.journal.service import DecisionJournal
from ai_trader.news.realtime import RealtimeNewsService
from ai_trader.news.review_bridge import LatencyFn, NewsReviewCoordinator
from ai_trader.news.review_context import NewsReviewBundle
from ai_trader.news.service import NewsIntelligence
from ai_trader.persistence.control_models import ensure_control_tables
from ai_trader.persistence.control_store import ControlStore
from ai_trader.persistence.database import create_database_engine
from ai_trader.persistence.news_models import ensure_news_tables
from ai_trader.persistence.news_repository import NewsRepository
from ai_trader.persistence.repositories import SqlDecisionJournal, ensure_journal_tables
from ai_trader.persistence.trade_ledger import TradeLedger
from ai_trader.persistence.trade_models import ensure_trade_tables
from ai_trader.risk.entry_plan import RiskRules
from ai_trader.safety.envelope import KillSwitch, SafetyEnvelope

logger = logging.getLogger(__name__)

EmitFn = Callable[..., None]
MarketSink = Callable[[list[MarketState]], None]


def tradeable_states(
    states: list[MarketState], now: datetime, max_age_seconds: int
) -> list[MarketState]:
    """Return the fresh, complete states a review may trade on, sorted by symbol.

    Anything stale or partial is excluded so the AI never decides blind.
    """
    return sorted(
        (
            state
            for state in states
            if stale_data_reason(state, now, max_age_seconds) is None
        ),
        key=lambda state: state.instrument.symbol,
    )


def _ticker_of(full_symbol: str) -> str:
    """Extract the ticker from a FYERS symbol such as ``NSE:BAJAJ-AUTO-EQ``."""
    return full_symbol.split(":", 1)[-1].rsplit("-", 1)[0]


def within_nse_hours(now: datetime) -> bool:
    """True during the NSE equity session (09:15-15:30 IST, Mon-Fri)."""
    ist = now.astimezone(ZoneInfo("Asia/Kolkata"))
    if ist.weekday() >= 5:
        return False
    return time(9, 15) <= ist.time() <= time(15, 30)


def feed_needs_reconnect(
    idle_seconds: float | None,
    now: datetime,
    last_reconnect_at: datetime | None,
    *,
    idle_threshold_seconds: int,
    cooldown_seconds: int,
) -> bool:
    """True when a silently stalled feed must be prodded during market hours.

    A socket that stays connected but delivers nothing never trips the
    reconnect loop, so without this the bots trade on frozen prices (or skip
    every review) until someone notices. Overnight and weekend silence is
    normal market closure and never triggers; neither does a recent prod.
    """
    if idle_seconds is None or idle_seconds < idle_threshold_seconds:
        return False
    if not within_nse_hours(now):
        return False
    if (
        last_reconnect_at is not None
        and (now - last_reconnect_at).total_seconds() < cooldown_seconds
    ):
        return False
    return True


def _skip_reason(states: list[MarketState], now: datetime, max_age_seconds: int) -> str:
    if not states:
        return "no market state yet"
    freshest = max(
        states,
        key=lambda s: max(
            (t.received_timestamp for t in s.recent_ticks), default=s.timestamp
        ),
    )
    return stale_data_reason(freshest, now, max_age_seconds) or "no tradeable market state"


def shard_symbols(symbols: list[str], shard_count: int) -> list[tuple[str, ...]]:
    """Split symbols round-robin across shards so sectors stay mixed.

    Deterministic: the same watchlist always yields the same shards, so a symbol
    keeps its reviewer (and its paper book) across restarts.
    """
    count = max(1, shard_count)
    groups: list[list[str]] = [[] for _ in range(count)]
    for position, symbol in enumerate(symbols):
        groups[position % count].append(symbol)
    return [tuple(group) for group in groups if group]


@dataclass
class ReviewShard:
    """One parallel reviewer: its own symbol slice, paper book, and AI loop state."""

    index: int
    symbols: tuple[str, ...]
    broker: PaperBroker
    workflow: ShadowReviewWorkflow
    provider: AIProvider
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    reviews: int = 0
    # Sonnet twin sharing the book/safety/execution; used only for
    # news-triggered escalation. None in tests that build shards by hand.
    sonnet_workflow: ShadowReviewWorkflow | None = None


@dataclass
class SessionResult:
    record_id: str
    status: str
    failure_reason: str | None = None


@dataclass
class ShadowSession:
    """Owns the FYERS feed, continuous news ingestion, and parallel paper reviewers.

    Each shard holds its own symbol slice, paper book, and AI loop state, so
    reviewers never contend on money. The feed, journal, news pipeline, and
    kill switch are shared. News backpressure can only slow the news tasks, so
    the market stream is never held up by the AI.
    """

    settings: Settings
    feed: FyersFeed
    journal: DecisionJournal
    clock: Clock
    interval_seconds: int
    shards: list[ReviewShard] = field(default_factory=list)
    kill_switch: KillSwitch = field(default_factory=KillSwitch)
    news: RealtimeNewsService | None = None
    coordinator: NewsReviewCoordinator | None = None
    market_sink: MarketSink | None = None
    event_sink: EmitFn | None = None
    news_analyses: Callable[[int], list[NewsAnalysis]] | None = None
    providers: tuple[AIProvider, ...] = ()
    history: MarketHistory | None = None
    socket_task: asyncio.Task[None] | None = None
    review_task: asyncio.Task[None] | None = None
    reviews: int = 0
    news_reviews: int = 0
    last_feed_reconnect_at: datetime | None = None
    # Auto-classified review universe. Empty master disables selection (as in
    # the small hand-built sessions used by tests).
    universe_master: tuple[str, ...] = ()
    universe_mode: str = DEFAULT_UNIVERSE_MODE
    universe_size: int = DEFAULT_UNIVERSE_SIZE
    universe_cheap_max_price_inr: Decimal = CHEAP_MAX_PRICE_INR
    universe_selected: tuple[str, ...] = ()
    # Called with the watched tickers whenever the subscription changes, so the
    # UI can drop snapshots for symbols that are no longer subscribed.
    universe_sink: Callable[[list[str]], None] | None = None
    # Operator override for the fixed money caps; see SafetyEnvelope.
    capital_cap_disabled: bool = False
    # Operator override for the max-open-positions cap; see SafetyEnvelope.
    position_count_cap_disabled: bool = False
    # Operator kill switches for the news pipeline and the Sonnet lever.
    tier1_news_enabled: bool = True
    news_analysis_enabled: bool = True
    sonnet_escalation_enabled: bool = True
    # Sonnet NEWS reviews used today (IST date below); bounded by
    # ai_max_sonnet_escalations_per_day so a news storm cannot 100x spend.
    sonnet_escalations_today: int = 0
    sonnet_escalation_day: str | None = None

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

    def set_capital_cap_disabled(self, disabled: bool) -> None:
        """Apply the operator's cap override to every shard without a restart."""
        self.capital_cap_disabled = disabled
        self._render_position_limits()

    def set_position_count_cap_disabled(self, disabled: bool) -> None:
        """Apply the operator's position-count override without a restart."""
        self.position_count_cap_disabled = disabled
        self._render_position_limits()

    async def set_tier1_news_enabled(self, enabled: bool) -> None:
        """Apply the Tier-1 feeds kill switch to the running ingestion."""
        self.tier1_news_enabled = enabled
        if self.news is not None:
            from ai_trader.news.sources import TIER1_SOURCE_IDS

            for source_id in TIER1_SOURCE_IDS:
                await self.news.set_source_enabled(source_id, enabled)

    def set_news_analysis_enabled(self, enabled: bool) -> None:
        """Apply the analysis kill switch; ingestion keeps flowing."""
        self.news_analysis_enabled = enabled
        if self.news is not None and self.news.intelligence is not None:
            self.news.intelligence.analysis_enabled = enabled

    def set_sonnet_escalation_enabled(self, enabled: bool) -> None:
        """Apply the Sonnet-escalation kill switch without a restart."""
        self.sonnet_escalation_enabled = enabled

    def _sonnet_escalation_ok(self) -> bool:
        """Whether a NEWS review may run on Sonnet right now."""
        if not self.sonnet_escalation_enabled:
            return False
        cap = self.settings.ai_max_sonnet_escalations_per_day
        if cap <= 0:
            return False
        today = self.clock.now().astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat()
        if self.sonnet_escalation_day != today:
            self.sonnet_escalation_day = today
            self.sonnet_escalations_today = 0
        return self.sonnet_escalations_today < cap

    def _render_position_limits(self) -> None:
        limits = position_limits_text(
            self.settings,
            capital_cap_disabled=self.capital_cap_disabled,
            position_count_cap_disabled=self.position_count_cap_disabled,
        )
        for shard in self.shards:
            shard.workflow.safety.capital_cap_disabled = self.capital_cap_disabled
            shard.workflow.safety.position_count_cap_disabled = (
                self.position_count_cap_disabled
            )
            shard.workflow.manager.set_position_limits(limits)

    # --- reviews ------------------------------------------------------------
    def _shards_for_bundle(self, bundle: NewsReviewBundle) -> list[ReviewShard]:
        """Route news to shards holding (or watching) a named symbol."""
        named = {symbol.upper() for symbol in bundle.affected_symbols}
        matched = [
            shard
            for shard in self.shards
            if named & {_ticker_of(full).upper() for full in shard.symbols}
        ]
        if matched:
            return matched
        held = [
            shard
            for shard in self.shards
            if any(position.quantity for position in shard.broker.positions.values())
        ]
        return held

    def _assign_shard_symbols(
        self,
        selected: list[str],
        held_by_shard: dict[int, list[str]],
    ) -> list[list[str]]:
        """Partition the active universe, keeping held symbols with their book.

        Held symbols are anchored to the shard that owns the position so its
        reviewer can always close them. The rest is dealt round-robin to keep
        sectors mixed, exactly like the initial static split.
        """
        groups = [
            list(dict.fromkeys(held_by_shard.get(shard.index, []))) for shard in self.shards
        ]
        assigned = {symbol for group in groups for symbol in group}
        remaining = [symbol for symbol in selected if symbol not in assigned]
        for position, symbol in enumerate(remaining):
            groups[position % len(groups)].append(symbol)
        return groups

    async def _refresh_universe(self) -> None:
        """Re-classify the master list by live price and apply the active mode.

        Runs at the start of each cycle: the operator's mode switch therefore
        takes effect on the next review without rebuilding the session. A quote
        failure keeps the previous universe; it must never strand open books.
        """
        if not self.universe_master or not self.shards:
            return
        quote_prices = getattr(self.feed, "quote_prices", None)
        if quote_prices is None:  # Test doubles without a REST quote surface.
            return
        try:
            prices: dict[str, Decimal] = dict(await quote_prices(list(self.universe_master)))
        except Exception:
            logger.warning(
                "universe quote refresh failed; keeping the current universe", exc_info=True
            )
            return
        # REST quotes can lag or omit illiquid names; the live socket marks are
        # the freshest fallback for symbols already subscribed.
        for state in self.feed.states():
            full_symbol = f"NSE:{state.instrument.symbol.upper()}-EQ"
            prices.setdefault(full_symbol, state.last_price)
        if not prices:
            return

        resolvable = getattr(self.feed, "symbol_to_topic", {})
        candidates = [
            symbol
            for symbol in self.universe_master
            if not resolvable or symbol.upper() in resolvable
        ]
        selected = select_universe(
            candidates,
            prices,
            self.universe_mode,
            self.universe_size,
            self.universe_cheap_max_price_inr,
        )
        if not selected:
            logger.warning(
                "universe selection for mode=%s returned no symbols; keeping the current"
                " universe",
                self.universe_mode,
            )
            return

        held_by_shard: dict[int, list[str]] = {}
        for shard in self.shards:
            for position in shard.broker.positions.values():
                if position.quantity > 0:
                    held_by_shard.setdefault(shard.index, []).append(
                        f"NSE:{position.symbol.upper()}-EQ"
                    )
        held = [symbol for symbols in held_by_shard.values() for symbol in symbols]
        # Held positions go FIRST: subscribe_symbols trims to the FYERS
        # 100-topic cap, and an open position must never be the thing trimmed
        # off the live feed (it still has to be closable at a real price).
        subscribed = list(dict.fromkeys([*held, *selected]))
        groups = self._assign_shard_symbols(subscribed, held_by_shard)
        self.universe_selected = tuple(selected)
        if groups == [list(shard.symbols) for shard in self.shards]:
            return

        for shard, group in zip(self.shards, groups, strict=True):
            shard.symbols = tuple(group)
        subscribe = getattr(self.feed, "subscribe_symbols", None)
        if subscribe is not None:
            try:
                await subscribe(subscribed)
            except Exception:
                logger.exception("could not resubscribe the feed to the new universe")
        if self.universe_sink is not None:
            self.universe_sink([_ticker_of(symbol) for symbol in subscribed])
        logger.info(
            "universe %s selected %d symbols (%d held) across %d shard(s)",
            self.universe_mode,
            len(selected),
            len(held),
            len(self.shards),
        )

    def _shard_boards(self) -> list[list[MarketState]]:
        """Fresh, tradeable states per shard, in shard order.

        When a master universe is active, names that crossed the price cap since
        the last refresh are hidden from the AI for new entries. Held symbols
        are exempt so an exit is never stranded by a price move.
        """
        now = self.clock.now()
        max_age = self.settings.max_market_data_age_seconds
        by_symbol = {state.instrument.symbol: state for state in self.feed.states()}
        boards: list[list[MarketState]] = []
        for shard in self.shards:
            states = [
                by_symbol[ticker]
                for full in shard.symbols
                if (ticker := full.split(":", 1)[-1].rsplit("-", 1)[0]) in by_symbol
            ]
            board = tradeable_states(states, now, max_age)
            if self.universe_master:
                held = {
                    position.symbol.upper()
                    for position in shard.broker.positions.values()
                    if position.quantity > 0
                }
                board = [
                    state
                    for state in board
                    if state.instrument.symbol.upper() in held
                    or price_allowed(
                        self.universe_mode,
                        state.last_price,
                        self.universe_cheap_max_price_inr,
                    )
                ]
            boards.append(board)
        return boards

    async def review_once(self) -> SessionResult | None:
        """Run one review on every shard concurrently; return the first completed."""
        try:
            await self._refresh_universe()
        except Exception:
            logger.exception("universe refresh failed before manual review")
        boards = self._shard_boards()
        if not any(boards):
            logger.info("skipping manual review: %s", _skip_reason(self.feed.states(), self.clock.now(), self.settings.max_market_data_age_seconds))
            return None
        results = await self._gather_reviews(
            [
                self._review_shard(shard, board, trigger="CADENCE")
                for shard, board in zip(self.shards, boards, strict=True)
                if board
            ],
            trigger="MANUAL",
        )
        return results[0] if results else None

    async def _spend_paused(self) -> bool:
        """True once today's estimated Bedrock spend crosses the configured cap.

        Cadence and news-triggered reviews pause; open paper positions are left
        alone and the 15:10 square-off still runs. Manual reviews bypass the cap.
        """
        cap = self.settings.ai_max_daily_spend_usd
        if cap is None or cap <= 0:
            return False
        rates = (
            self.settings.bedrock_input_usd_per_million_tokens,
            self.settings.bedrock_output_usd_per_million_tokens,
        )
        if rates == (None, None):
            return False
        from ai_trader.reporting.usage import ist_day_start, spend_since

        records = await self.journal.list_recent()
        analyses = self.news_analyses(5000) if self.news_analyses is not None else []
        spent = spend_since(records, analyses, since=ist_day_start(self.clock.now()))
        if spent >= cap:
            logger.warning(
                "daily AI spend cap reached ($%.4f >= $%.2f); pausing reviews",
                spent,
                cap,
            )
            return True
        return False

    async def _review_from_news_forever(self) -> None:
        """Consume news-triggered reviews as the coordinator produces bundles.

        One failed cycle (a journal read, a bundle shape, a provider fault) is
        logged and skipped. It must not end this task: news reviews would then
        silently stop for the rest of the session while the API stayed healthy.
        """
        assert self.coordinator is not None
        while True:
            bundle = await self.coordinator.wait_for_bundle()
            if bundle is None:
                return
            if self._past_square_off(self.clock.now()):
                logger.info("skipping news review: past square-off time")
                return
            try:
                if await self._spend_paused():
                    logger.info("skipping news review: daily spend cap reached")
                    continue
                targets = self._shards_for_bundle(bundle)
                if not targets:
                    logger.info("skipping news review: no shard holds a named symbol")
                    continue
                boards = {index: board for index, board in enumerate(self._shard_boards())}
                await self._gather_reviews(
                    [
                        self._review_shard(
                            shard,
                            boards.get(shard.index, []),
                            bundle=bundle,
                            trigger="NEWS",
                            use_sonnet=self._sonnet_escalation_ok(),
                        )
                        for shard in targets
                    ],
                    trigger="NEWS",
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("news-triggered review cycle failed; continuing")

    async def _gather_reviews(
        self,
        reviews: Iterable[Awaitable[SessionResult | None]],
        *,
        trigger: str,
    ) -> list[SessionResult]:
        """Run shard reviews concurrently, isolating a failing shard.

        A shard's exception (a broker, journal, or provider fault) must not cancel
        its siblings or end the loop that calls this: the other shards still
        trade and the next cycle retries the failed one. Previously a single
        persistence error propagated out of the cadence loop, which then died
        silently while the HTTP API stayed up and the UI kept showing the prices
        it had already fetched.
        """
        outcomes = await asyncio.gather(*reviews, return_exceptions=True)
        results: list[SessionResult] = []
        for outcome in outcomes:
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            if isinstance(outcome, BaseException):
                logger.exception("%s review failed: %s", trigger, outcome, exc_info=outcome)
                continue
            if outcome is not None:
                results.append(outcome)
        return results

    async def _bars_for(self, board: list[MarketState]) -> dict[str, tuple[Candle, ...]]:
        """Recent 5-minute bars for the board, or {} when history is unavailable."""
        if self.history is None:
            return {}
        try:
            return await self.history.for_board(board)
        except Exception:  # noqa: BLE001 - history must never fail a review
            logger.exception("market history unavailable; falling back to session range")
            return {}

    async def _review_shard(
        self,
        shard: ReviewShard,
        board: list[MarketState],
        bundle: NewsReviewBundle | None = None,
        *,
        trigger: str = "CADENCE",
        use_sonnet: bool = False,
    ) -> SessionResult | None:
        """Run one shard's review. Shards never share a broker, so they never block each other.

        ``use_sonnet`` routes a news-triggered review through the shard's
        Sonnet twin workflow (same book, safety, and execution; only the
        manager's model differs) and counts it against the daily cap.
        """
        if not board:
            return None
        state = board[0]
        async with shard.lock:
            # Mark the shard's paper book to real prices before the AI sees valuation.
            prices = self._paper_prices()
            shard.broker.mark_to_market(prices)
            # Prices let the workflow close a held symbol whose own state is too
            # stale to be on the board.
            workflow = shard.workflow
            if use_sonnet and shard.sonnet_workflow is not None and self._sonnet_escalation_ok():
                workflow = shard.sonnet_workflow
                self.sonnet_escalations_today += 1
            record = await workflow.run(
                state,
                bundle,
                board=board,
                prices=prices,
                bars=await self._bars_for(board),
            )
            shard.reviews += 1
            self.reviews += 1
            if trigger == "NEWS":
                self.news_reviews += 1
            logger.info(
                "shard %d %s review %s status=%s proposals=%d",
                shard.index,
                trigger,
                record.decision_id,
                record.status.value,
                len(record.final_ai_decision.proposals) if record.final_ai_decision else 0,
            )
            return SessionResult(record.decision_id, record.status.value, record.failure_reason)

    async def _prod_stalled_feed(self) -> None:
        """Force a socket reconnect when frames stop arriving in market hours.

        Runs every cadence; the cooldown inside ``feed_needs_reconnect`` keeps
        a persistent outage from spinning reconnects.
        """
        now = self.clock.now()
        service = getattr(self.feed, "service", None)
        if service is None:  # Test doubles without a market-data service.
            return
        idle = service.idle_seconds(now)
        if not feed_needs_reconnect(
            idle,
            now,
            self.last_feed_reconnect_at,
            idle_threshold_seconds=self.settings.feed_idle_reconnect_seconds,
            cooldown_seconds=self.settings.feed_reconnect_cooldown_seconds,
        ):
            return
        self.last_feed_reconnect_at = now
        logger.warning(
            "market feed idle for %.0fs during NSE hours; forcing socket reconnect",
            idle if idle is not None else -1,
        )
        if self.event_sink is not None:
            self.event_sink(
                "MARKET",
                "feed_reconnect",
                f"market feed idle for {idle:.0f}s; forcing socket reconnect",
            )
        await self.feed.socket.reconnect()

    def _paper_prices(self) -> dict[str, Decimal]:
        """Real FYERS prices keyed by the internal symbol the paper broker tracks."""
        return {state.instrument.symbol: state.last_price for state in self.feed.states()}

    def _past_square_off(self, now: datetime) -> bool:
        """True once the IST wall clock reaches the intraday square-off time."""
        try:
            hour, _, minute = self.settings.intraday_square_off_time_ist.strip().partition(":")
            cutoff = f"{int(hour):02d}:{int(minute or 0):02d}"
        except ValueError:
            logger.warning(
                "invalid intraday_square_off_time_ist=%r; square-off disabled",
                self.settings.intraday_square_off_time_ist,
            )
            return False
        ist_now = now.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%H:%M")
        return ist_now >= cutoff

    async def square_off(self) -> list[Order]:
        """Flatten every open paper position in every shard at live prices.

        No overnight carry. Honours the shared kill switch; anything unpriceable
        or refused by the broker is logged and left alone.
        """
        if self.kill_switch.enabled:
            logger.warning("square-off skipped: kill switch engaged")
            return []
        results = await asyncio.gather(*[self._square_off_shard(shard) for shard in self.shards])
        return [order for orders in results for order in orders]

    async def _square_off_shard(self, shard: ReviewShard) -> list[Order]:
        async with shard.lock:
            shard.broker.mark_to_market(self._paper_prices())
            account = await shard.broker.get_account()
            prices = self._paper_prices()
            orders: list[Order] = []
            for position in account.positions:
                if position.quantity <= 0:
                    continue
                price = prices.get(position.symbol)
                if price is None or price <= 0:
                    logger.warning("square-off skipped %s: no live price", position.symbol)
                    continue
                proposal = TradeProposal(
                    proposal_id=f"squareoff-{position.symbol}-{uuid4().hex[:8]}",
                    symbol=position.symbol,
                    exchange="NSE",
                    action=PortfolioAction.CLOSE_POSITION,
                    side=OrderSide.SELL,
                    capital_allocation_inr=price * position.quantity,
                    quantity_shares=position.quantity,
                    confidence=1.0,
                    thesis="Intraday square-off: flatten before the close; no overnight carry.",
                    evidence=["square-off window reached"],
                    conditions_that_change_thesis=[],
                )
                try:
                    request = shard.workflow.execution.prepare(
                        f"squareoff-{uuid4().hex[:12]}",
                        position.symbol,
                        proposal,
                        price,
                    )
                    result = await shard.workflow.execution.submit(request)
                except (ExecutionAdjustmentRequired, ValueError) as exc:
                    logger.warning("square-off refused %s: %s", position.symbol, exc)
                    continue
                orders.append(result.order)
                if self.event_sink is not None:
                    self.event_sink(
                        "PAPER",
                        "paper_square_off",
                        f"FLAT {position.symbol} {position.quantity} @ {price} (paper)",
                        symbol=position.symbol,
                    )
            return orders

    async def run_loop(self, max_reviews: int | None = None) -> list[SessionResult]:
        """Review on the configured cadence until stopped or ``max_reviews`` reached.

        Every shard reviews concurrently each cadence. News-triggered reviews run
        on their own task, so this loop is unaffected by them.
        """
        results: list[SessionResult] = []
        squared_off = False
        while max_reviews is None or len(results) < max_reviews:
            # Applied at the start of the next cycle, so a mode switch from the
            # UI never interrupts an in-flight review.
            try:
                await self._refresh_universe()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("universe refresh failed; keeping the current universe")
            await asyncio.sleep(self.interval_seconds)
            try:
                if self.socket_task is not None and self.socket_task.done():
                    logger.warning("market data socket stopped; reconnect handling is active")
                if self._past_square_off(self.clock.now()):
                    if not squared_off:
                        squared_off = True
                        flattened = await self.square_off()
                        logger.info(
                            "intraday square-off flattened %d position(s)", len(flattened)
                        )
                    if max_reviews is not None:
                        break
                    continue
                if await self._spend_paused():
                    if max_reviews is not None:
                        break
                    await asyncio.sleep(self.interval_seconds)
                    continue
                await self._prod_stalled_feed()
                boards = self._shard_boards()
                if not any(boards):
                    logger.info(
                        "skipping cadence review: %s",
                        _skip_reason(
                            self.feed.states(),
                            self.clock.now(),
                            self.settings.max_market_data_age_seconds,
                        ),
                    )
                    continue
                completed = await self._gather_reviews(
                    [
                        self._review_shard(shard, board, trigger="CADENCE")
                        for shard, board in zip(self.shards, boards, strict=True)
                        if board
                    ],
                    trigger="CADENCE",
                )
                results.extend(completed)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A cycle that fails (feed prods, spend accounting, square-off) is
                # retried next cadence. Letting it escape would kill this task and
                # stop every future review while the API kept answering.
                logger.exception("cadence review cycle failed; retrying next interval")
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
        for provider in self.providers:
            await provider.aclose()


def build_session(
    settings: Settings,
    feed: FyersFeed,
    clock: Clock | None = None,
    journal: DecisionJournal | None = None,
    event_sink: EmitFn | None = None,
    health: AIHealth | None = None,
    market_sink: MarketSink | None = None,
    kill_switch: KillSwitch | None = None,
    universe_mode: str | None = None,
    universe_master: tuple[str, ...] | None = None,
    universe_sink: Callable[[list[str]], None] | None = None,
    capital_cap_disabled: bool = False,
    position_count_cap_disabled: bool = False,
    tier1_news_enabled: bool = True,
    news_analysis_enabled: bool = True,
    sonnet_escalation_enabled: bool = True,
) -> ShadowSession:
    """Assemble a shadow session with sharded parallel reviewers.

    Each shard owns a disjoint slice of the watchlist and its own paper book,
    so reviewers never contend on money. The feed, journal, news pipeline, and
    kill switch are shared. The universe is re-selected from ``universe_master``
    at the start of each cycle using the active mode.
    """
    active_clock = clock or SystemClock()
    active_kill = kill_switch or KillSwitch()

    engine = create_database_engine(settings.database_url)
    active_journal = journal or SqlDecisionJournal(engine)

    symbols = list(dict.fromkeys(settings.shadow_symbols))
    shard_count = max(1, settings.ai_review_shards)
    groups = shard_symbols(symbols, shard_count) or [()]
    # Split capital evenly; the remainder goes to the first shard.
    total = settings.shadow_initial_capital_inr
    per_shard = total // len(groups)
    leftovers = total - per_shard * len(groups)

    critic = build_critic(settings)
    news_repository = NewsRepository(engine)
    ensure_journal_tables(engine)
    ensure_trade_tables(engine)
    if settings.news_enabled:
        ensure_news_tables(engine)

    shards: list[ReviewShard] = []
    providers: list[AIProvider] = []
    ledger = TradeLedger(engine)
    for index, group in enumerate(groups):
        capital = per_shard + (leftovers if index == 0 else Decimal("0"))
        # Hard invariant: the only broker constructed here is PaperBroker.
        broker = PaperBroker(
            capital,
            active_clock,
            margin_multiple=settings.paper_margin_multiple,
            shard=index,
            ledger=ledger,
            charge_rates=charge_rates_for(settings),
        )
        for state in feed.states():
            broker.set_price(state.instrument.symbol, state.last_price)
        remote_provider = build_ai_provider(settings, health, cadence=True)
        providers.append(remote_provider)
        provider = RetryingAIProvider(
            EntryPlanRequired(remote_provider), settings.ai_schema_max_retries
        )
        manager = PortfolioManagerAgent(
            provider,
            "v17",
            position_limits=position_limits_text(
                settings,
                capital_cap_disabled=capital_cap_disabled,
                position_count_cap_disabled=position_count_cap_disabled,
            ),
        )
        safety = SafetyEnvelope(
            settings,
            active_kill,
            active_clock,
            capital_cap_disabled=capital_cap_disabled,
            position_count_cap_disabled=position_count_cap_disabled,
        )
        execution = ExecutionService(broker)
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
            jev_min_entry_score=settings.jev_min_entry_score,
            risk_rules=RiskRules.from_settings(settings),
        )
        # Sonnet twin of the shard workflow for news-triggered escalation:
        # same book, safety, execution and journal, only the manager's model
        # differs. Built always so the toggle can flip at runtime; the per-day
        # cap bounds the spend.
        sonnet_provider = build_ai_provider(settings, health)
        providers.append(sonnet_provider)
        sonnet_workflow = ShadowReviewWorkflow(
            PortfolioManagerAgent(
                RetryingAIProvider(
                    EntryPlanRequired(sonnet_provider), settings.ai_schema_max_retries
                ),
                "v17",
                position_limits=position_limits_text(
                    settings,
                    capital_cap_disabled=capital_cap_disabled,
                    position_count_cap_disabled=position_count_cap_disabled,
                ),
            ),
            critic,
            broker,
            safety,
            execution,
            active_journal,
            active_clock,
            event_sink=event_sink,
            latency_sink=_latency_sink(news_repository) if settings.news_enabled else None,
            jev_min_entry_score=settings.jev_min_entry_score,
            risk_rules=RiskRules.from_settings(settings),
        )
        shards.append(
            ReviewShard(
                index=index,
                symbols=tuple(group),
                broker=broker,
                workflow=workflow,
                provider=remote_provider,
                sonnet_workflow=sonnet_workflow,
            )
        )

    news_provider = build_ai_provider(settings, health)
    providers.append(news_provider)

    realtime: RealtimeNewsService | None = None
    coordinator: NewsReviewCoordinator | None = None
    intelligence: NewsIntelligence | None = None

    if settings.news_enabled:
        from ai_trader.news.sources import TIER1_SOURCE_IDS, enabled_sources

        intelligence = NewsIntelligence(
            settings, news_repository, ai_provider=news_provider, emit=event_sink
        )
        intelligence.analysis_enabled = news_analysis_enabled
        realtime = RealtimeNewsService(
            settings=settings,
            intelligence=intelligence,
            sources=enabled_sources(),
            emit=event_sink,
        )
        if not tier1_news_enabled:
            realtime.disable_sources(TIER1_SOURCE_IDS)
        coordinator = NewsReviewCoordinator(
            held_symbols=lambda: {
                position.symbol
                for shard in shards
                for position in shard.broker.positions.values()
                if position.quantity
            },
            recent_items=lambda: tuple(news_repository.recent_items(limit=15)),
            recent_analyses=lambda: tuple(news_repository.recent_analyses(limit=15)),
            emit=event_sink,
            review_held_positions=settings.news_review_held_positions,
        )

    # Historical bars are only available on a real FYERS feed; test doubles and
    # other providers simply run without them and keep the range fallback.
    history: MarketHistory | None = None
    auth = getattr(getattr(feed, "socket", None), "auth", None)
    if auth is not None:
        from ai_trader.application.market_history import MarketHistory as _MarketHistory
        from ai_trader.integrations.fyers.historical import fetch_ohlcv

        history = _MarketHistory(
            lambda instrument, symbol: fetch_ohlcv(
                auth, symbol, instrument, resolution="5", days_back=1
            ),
            active_clock,
        )

    session = ShadowSession(
        settings=settings,
        feed=feed,
        journal=active_journal,
        clock=active_clock,
        interval_seconds=settings.portfolio_review_interval_seconds,
        shards=shards,
        kill_switch=active_kill,
        news=realtime,
        coordinator=coordinator,
        market_sink=market_sink,
        event_sink=event_sink,
        providers=tuple(providers),
        history=history,
        universe_master=tuple(universe_master if universe_master is not None else resolve_master(settings)),
        universe_mode=universe_mode or settings.universe_mode,
        universe_size=settings.universe_size,
        universe_cheap_max_price_inr=settings.universe_cheap_max_price_inr,
        universe_selected=tuple(symbols),
        universe_sink=universe_sink,
        capital_cap_disabled=capital_cap_disabled,
        position_count_cap_disabled=position_count_cap_disabled,
        tier1_news_enabled=tier1_news_enabled,
        news_analysis_enabled=news_analysis_enabled,
        sonnet_escalation_enabled=sonnet_escalation_enabled,
        news_analyses=(
            lambda limit: news_repository.recent_analyses(limit=limit)
            if settings.news_enabled
            else []
        ),
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
    settings: Settings,
    symbols: list[str] | None = None,
    health: AIHealth | None = None,
    event_sink: EmitFn | None = None,
    market_sink: MarketSink | None = None,
    kill_switch: KillSwitch | None = None,
    universe_sink: Callable[[list[str]], None] | None = None,
) -> ShadowSession:
    master = resolve_master(settings)
    feed = await build_feed(settings, symbols, market_sink=market_sink, master=master)
    engine = create_database_engine(settings.database_url)
    ensure_control_tables(engine)
    control = ControlStore(engine)
    mode = await control.get_universe_mode(settings.universe_mode)
    capital_cap_disabled = await control.get_capital_cap_disabled(settings.capital_cap_disabled)
    position_count_cap_disabled = await control.get_position_count_cap_disabled(
        settings.position_count_cap_disabled
    )
    tier1_news_enabled = await control.get_tier1_news_enabled(settings.tier1_news_enabled)
    news_analysis_enabled = await control.get_news_analysis_enabled(
        settings.news_analysis_enabled
    )
    sonnet_escalation_enabled = await control.get_sonnet_escalation_enabled(
        settings.sonnet_escalation_enabled
    )
    return build_session(
        settings,
        feed,
        health=health,
        event_sink=event_sink,
        market_sink=market_sink,
        kill_switch=kill_switch,
        universe_mode=mode,
        universe_master=master,
        universe_sink=universe_sink,
        capital_cap_disabled=capital_cap_disabled,
        position_count_cap_disabled=position_count_cap_disabled,
        tier1_news_enabled=tier1_news_enabled,
        news_analysis_enabled=news_analysis_enabled,
        sonnet_escalation_enabled=sonnet_escalation_enabled,
    )


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
