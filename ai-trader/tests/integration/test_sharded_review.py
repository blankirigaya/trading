"""Parallel reviewer shards: partitioning, capital split, and isolation."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from ai_trader.ai.base import AIProvider
from ai_trader.ai.jev import DisabledJevCritic
from ai_trader.ai.portfolio_manager import PortfolioManagerAgent
from ai_trader.application.fyers_session import (
    ReviewShard,
    ShadowSession,
    build_session,
    shard_symbols,
)
from ai_trader.application.workflows.shadow_review import ShadowReviewWorkflow
from ai_trader.brokers.paper import PaperBroker
from ai_trader.config import Settings
from ai_trader.domain.common import SystemClock
from ai_trader.domain.decisions import (
    PortfolioAction,
    PortfolioDecision,
    ProviderMetadata,
    TradeProposal,
)
from ai_trader.domain.market import Candle, Instrument, MarketState, MarketTick
from ai_trader.domain.orders import OrderSide
from ai_trader.execution.service import ExecutionService
from ai_trader.journal.service import InMemoryDecisionJournal
from ai_trader.safety.envelope import KillSwitch, SafetyEnvelope


def test_shards_partition_round_robin() -> None:
    symbols = [f"NSE:S{i}-EQ" for i in range(7)]
    groups = shard_symbols(symbols, 3)
    assert [len(g) for g in groups] == [3, 2, 2]
    assert sorted(s for g in groups for s in g) == sorted(symbols)
    # Deterministic: same input always yields the same shards.
    assert shard_symbols(symbols, 3) == groups
    assert shard_symbols(symbols, 1) == [tuple(symbols)]
    assert shard_symbols([], 4) == []


def test_capital_splits_evenly_across_shards() -> None:
    from ai_trader.integrations.fyers.auth import FyersAuth
    from ai_trader.integrations.fyers.client import FyersDataSocket
    from ai_trader.integrations.fyers.feed import FyersFeed
    from ai_trader.integrations.fyers.market_data import FyersMarketDataService

    clock = SystemClock()
    auth = FyersAuth("app", "token")
    instrument = Instrument(instrument_key="3045", symbol="INFY", exchange="NSE")
    service = FyersMarketDataService({"sf|nse_cm|3045": instrument}, clock)
    socket = FyersDataSocket(auth, on_frame=lambda *_: None)
    feed = FyersFeed(service, socket, {"sf|nse_cm|3045": instrument}, ["sf|nse_cm|3045"], {})

    settings = Settings(
        app_mode="LIVE_SHADOW",
        shadow_initial_capital_inr=Decimal("100000"),
        ai_review_shards=4,
        news_enabled=False,
        database_url="sqlite://",
        ai_base_url="http://ai:8080/v1",
        ai_model="m",
    )
    session = build_session(settings, feed, clock=clock)
    assert len(session.shards) == 4
    totals = sum((shard.broker.cash for shard in session.shards), Decimal("0"))
    assert totals == Decimal("100000")
    assert {shard.broker.cash for shard in session.shards} == {Decimal("25000")}
    assert all(isinstance(shard.provider, AIProvider) for shard in session.shards)


def _tick(symbol: str, price: str, at: datetime) -> MarketTick:
    return MarketTick(
        symbol=symbol,
        exchange="NSE",
        segment="EQ",
        last_price=Decimal(price),
        exchange_timestamp=at,
        received_timestamp=at,
        processed_timestamp=at,
        raw_payload={},
    )


def _state(symbol: str, price: str, at: datetime) -> MarketState:
    tick = _tick(symbol, price, at)
    candle = Candle(
        start_timestamp=at,
        end_timestamp=at,
        open=Decimal(price),
        high=Decimal(price),
        low=Decimal(price),
        close=Decimal(price),
        volume=1000,
    )
    return MarketState(
        snapshot_id=f"s-{symbol}",
        instrument=Instrument(instrument_key=f"NSE:{symbol}-EQ", symbol=symbol, exchange="NSE"),
        timestamp=at,
        last_price=Decimal(price),
        previous_close=Decimal(price),
        recent_ticks=[tick],
        current_1m_candle=candle,
        recent_1m_candles=[candle],
    )


class StubFeed:
    def __init__(self, states: list[MarketState]) -> None:
        self._states = {s.instrument.symbol: s for s in states}
        self.socket = self

    async def run(self) -> None:
        await asyncio.sleep(3600)

    async def stop(self) -> None:
        self.stopped = True

    def states(self) -> list[MarketState]:
        return list(self._states.values())


class BuyingAI(AIProvider):
    """Buys its assigned symbol every review."""

    def __init__(self, symbol: str, price: str) -> None:
        self.symbol = symbol
        self.price = price
        self.calls = 0

    @property
    def model_name(self) -> str:
        return "buy-model"

    async def structured_completion(self, system_prompt, user_prompt, response_model, *, prompt_name, prompt_version):
        self.calls += 1
        return PortfolioDecision(
            cash_percent=0.0,
            allocations=[],
            proposals=[
                TradeProposal(
                    proposal_id="p-1",
                    symbol=self.symbol,
                    action=PortfolioAction.OPEN_POSITION,
                    side=OrderSide.BUY,
                    capital_allocation_inr=Decimal("1000"),
                    quantity_shares=2,
                    confidence=0.6,
                    thesis="test",
                    evidence=["test"],
                    conditions_that_change_thesis=["test"],
                )
            ],
            market_summary="m",
            decision_summary="buy",
        ), ProviderMetadata(
            provider="test",
            model=self.model_name,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
        )


def _sharded_session(
    symbols: list[str],
    clock: SystemClock,
    price: str = "100",
    journal: InMemoryDecisionJournal | None = None,
    **settings_kwargs: object,
) -> ShadowSession:
    from ai_trader.journal.service import DecisionJournal

    now = datetime.now(UTC)
    settings = Settings(
        trading_enabled=True,
        news_enabled=False,
        shadow_initial_capital_inr=Decimal("100000"),
        ai_review_shards=len(symbols),
        database_url="sqlite://",
        ai_base_url="http://ai:8080/v1",
        ai_model="m",
        **settings_kwargs,  # type: ignore[arg-type]
    )
    active_journal: DecisionJournal = journal or InMemoryDecisionJournal()
    session = ShadowSession(
        settings=settings,
        feed=StubFeed([_state(s, price, now) for s in symbols]),
        journal=active_journal,
        clock=clock,
        interval_seconds=0,
        kill_switch=KillSwitch(),
    )
    for index, symbol in enumerate(symbols):
        broker = PaperBroker(Decimal("100000"), clock)
        ai = BuyingAI(symbol, price)
        workflow = ShadowReviewWorkflow(
            PortfolioManagerAgent(ai, "v9"),
            DisabledJevCritic(),
            broker,
            SafetyEnvelope(settings, session.kill_switch, clock),
            ExecutionService(broker),
            active_journal,
            clock,
        )
        session.shards.append(
            ReviewShard(
                index=index,
                symbols=(f"NSE:{symbol}-EQ",),
                broker=broker,
                workflow=workflow,
                provider=ai,
            )
        )
    return session


def test_concurrent_shards_trade_independently(clock) -> None:
    session = _sharded_session(["INFY", "TCS"], clock)
    for symbol in ("INFY", "TCS"):
        session.shards[0 if symbol == "INFY" else 1].broker.set_price(symbol, Decimal("100"))
    results = asyncio.run(session.run_loop(max_reviews=2))
    assert len(results) == 2
    infy, tcs = session.shards
    assert infy.broker.positions["INFY"].quantity == 2
    assert "INFY" not in tcs.broker.positions or tcs.broker.positions["INFY"].quantity == 0
    assert tcs.broker.positions["TCS"].quantity == 2
    assert "TCS" not in infy.broker.positions or infy.broker.positions["TCS"].quantity == 0
    # Cash moved only in the owning shard.
    assert infy.broker.cash == Decimal("100000") - Decimal("200")
    assert tcs.broker.cash == Decimal("100000") - Decimal("200")


def test_news_bundle_routes_to_naming_shard(clock) -> None:
    from ai_trader.domain.news import (
        NewsCategory,
        NewsItem,
        SourceType,
        TrustLevel,
    )
    from ai_trader.news.dedup import content_hash_for, headline_hash_for
    from ai_trader.news.review_context import NewsReviewBundle

    def item(item_id: str, symbols: tuple[str, ...]) -> NewsItem:
        now = datetime.now(UTC)
        return NewsItem(
            item_id=item_id,
            source_id="src",
            source_name="Test",
            source_type=SourceType.NEWS_MEDIA,
            trust_level=TrustLevel.SECONDARY,
            headline=f"headline {item_id}",
            canonical_url=f"https://example.com/{item_id}",
            url=f"https://example.com/{item_id}",
            published_at=now,
            received_at=now,
            category=NewsCategory.MARKET_NEWS,
            symbols=symbols,
            content_hash=content_hash_for(item_id),
            headline_hash=headline_hash_for(item_id),
        )

    session = _sharded_session(["INFY", "TCS"], clock)
    bundle = NewsReviewBundle(source_items=(item("i1", ("TCS",)),))
    targets = session._shards_for_bundle(bundle)
    assert [s.index for s in targets] == [1]
    bundle_none = NewsReviewBundle(source_items=(item("i2", ("UNKNOWNCO",)),))
    assert session._shards_for_bundle(bundle_none) == []


def _costed_journal() -> InMemoryDecisionJournal:
    from ai_trader.domain.decisions import DecisionStatus, PortfolioDecision, ProviderMetadata
    from ai_trader.domain.portfolio import PortfolioState
    from ai_trader.journal.service import DecisionRecord

    journal = InMemoryDecisionJournal()
    now = datetime.now(UTC)
    meta = ProviderMetadata(
        provider="bedrock",
        model="m",
        prompt_name="p",
        prompt_version="v1",
        input_tokens=2000,
        output_tokens=500,
        provider_metadata={"estimated_total_usd": 0.005},
    )
    record = DecisionRecord(
        decision_id="d-cap",
        correlation_id="c",
        event_id="e",
        timestamp=now,
        status=DecisionStatus.APPROVED_BY_AI,
        input_context_hash="h",
        initial_ai_proposal=PortfolioDecision(
            cash_percent=100.0, allocations=[], proposals=[], market_summary="m", decision_summary="d"
        ),
        initial_provider_metadata=meta,
        portfolio_before=PortfolioState(cash=Decimal("100000")),
    )
    return journal, record


def _save(journal, record):
    async def _go():
        await journal.save(record)

    import asyncio

    asyncio.run(_go())
    return journal


def test_spend_cap_pauses_cadence_reviews(clock) -> None:
    import asyncio

    journal, record = _costed_journal()
    _save(journal, record)
    session = _sharded_session(
        ["INFY"],
        clock,
        journal=journal,
        ai_max_daily_spend_usd=0.001,
        bedrock_input_usd_per_million_tokens=0.94,
        bedrock_output_usd_per_million_tokens=3.76,
    )
    results = asyncio.run(session.run_loop(max_reviews=1))
    assert results == []
    assert session.shards[0].provider.calls == 0


def test_manual_review_bypasses_spend_cap(clock) -> None:
    import asyncio

    journal, record = _costed_journal()
    _save(journal, record)
    session = _sharded_session(
        ["INFY"],
        clock,
        journal=journal,
        ai_max_daily_spend_usd=0.001,
        bedrock_input_usd_per_million_tokens=0.94,
        bedrock_output_usd_per_million_tokens=3.76,
    )
    result = asyncio.run(session.review_once())
    assert result is not None
    assert session.shards[0].provider.calls == 1


def test_one_failing_shard_does_not_cancel_its_siblings(clock) -> None:
    """A shard fault is contained: the other shards still review and trade."""
    session = _sharded_session(["INFY", "TCS"], clock)
    for symbol in ("INFY", "TCS"):
        session.shards[0 if symbol == "INFY" else 1].broker.set_price(symbol, Decimal("100"))

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("shard exploded")

    session.shards[0].workflow.run = boom  # type: ignore[method-assign]

    results = asyncio.run(session.run_loop(max_reviews=1))
    assert len(results) == 1
    # The healthy shard traded; the broken one neither crashed the loop nor traded.
    assert session.shards[1].broker.positions["TCS"].quantity == 2
    assert "INFY" not in session.shards[0].broker.positions


def test_persist_failure_fails_the_decision_without_killing_the_loop(clock) -> None:
    """The restart-reuse fill id failure (a DB error inside submit) used to escape."""
    from sqlalchemy.exc import OperationalError

    from ai_trader.domain.decisions import DecisionStatus
    from ai_trader.journal.service import InMemoryDecisionJournal

    class ExplodingJournal(InMemoryDecisionJournal):
        """Succeeds for normal records, fails when journalling a failure."""

        async def save(self, record) -> None:  # type: ignore[no-untyped-def]
            if record.status is DecisionStatus.FAILED:
                raise RuntimeError("journal unavailable")
            await super().save(record)

    session = _sharded_session(["INFY"], clock, journal=ExplodingJournal())
    broker = session.shards[0].broker
    broker.set_price("INFY", Decimal("100"))

    async def locked(*_args: object, **_kwargs: object) -> None:
        raise OperationalError("INSERT INTO paper_fills", {}, Exception("UNIQUE constraint failed"))

    broker.place_order = locked  # type: ignore[method-assign]

    result = asyncio.run(session.review_once())

    assert result is not None
    assert result.status == DecisionStatus.FAILED.value
    assert "execution_error" in (result.failure_reason or "")
    # Nothing was opened, and the loop survived to return a decision.
    assert "INFY" not in broker.positions
