"""Intraday discipline: square-off flattens the book, then reviews stop."""

from datetime import UTC, datetime
from decimal import Decimal

from ai_trader.ai.base import AIProvider
from ai_trader.ai.jev import DisabledJevCritic
from ai_trader.ai.portfolio_manager import PortfolioManagerAgent
from ai_trader.application.fyers_session import ReviewShard, ShadowSession
from ai_trader.application.workflows.shadow_review import ShadowReviewWorkflow
from ai_trader.brokers.paper import PaperBroker
from ai_trader.config import Settings
from ai_trader.domain.common import SystemClock
from ai_trader.domain.decisions import PortfolioDecision, ProviderMetadata
from ai_trader.domain.market import Candle, Instrument, MarketState, MarketTick
from ai_trader.domain.portfolio import Position
from ai_trader.execution.service import ExecutionService
from ai_trader.journal.service import InMemoryDecisionJournal
from ai_trader.safety.envelope import KillSwitch, SafetyEnvelope


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
        self.stopped = False

    async def run(self) -> None:
        import asyncio

        await asyncio.sleep(3600)

    async def stop(self) -> None:
        self.stopped = True

    def latest(self) -> MarketState | None:
        return next(iter(self._states.values()), None)

    def states(self) -> list[MarketState]:
        return list(self._states.values())

    def topic_to_symbol(self) -> dict[str, str]:
        return {}


class HoldingAI(AIProvider):
    """Always HOLD. Any order placed during these tests comes from square-off."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def model_name(self) -> str:
        return "hold-model"

    async def structured_completion(
        self, system_prompt, user_prompt, response_model, *, prompt_name, prompt_version
    ):
        self.calls += 1
        return PortfolioDecision(
            cash_percent=100.0,
            allocations=[],
            proposals=[],
            market_summary="m",
            decision_summary="hold",
        ), ProviderMetadata(
            provider="test",
            model=self.model_name,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
        )


def _session(
    states: list[MarketState],
    broker: PaperBroker,
    clock: SystemClock,
    **settings_kwargs: object,
) -> ShadowSession:
    settings = Settings(trading_enabled=True, **settings_kwargs)  # type: ignore[arg-type]
    ai = HoldingAI()
    session = ShadowSession(
        settings=settings,
        feed=StubFeed(states),
        journal=InMemoryDecisionJournal(),
        clock=clock,
        interval_seconds=0,
        kill_switch=KillSwitch(),
    )
    session.shards.append(
        ReviewShard(
            index=0,
            symbols=tuple(f"NSE:{s.instrument.symbol}-EQ" for s in states),
            broker=broker,
            workflow=ShadowReviewWorkflow(
                PortfolioManagerAgent(ai, "v9"),
                DisabledJevCritic(),
                broker,
                SafetyEnvelope(settings, session.kill_switch, clock),
                ExecutionService(broker),
                session.journal,
                clock,
            ),
            provider=ai,
        )
    )
    return session


def test_square_off_flattens_open_positions_at_live_prices(clock) -> None:
    now = datetime.now(UTC)
    broker = PaperBroker(Decimal("100000"), clock)
    broker.positions["INFY"] = Position(symbol="INFY", quantity=10, average_entry_price=Decimal("100"))
    broker.set_price("INFY", Decimal("110"))
    session = _session([_state("INFY", "110", now)], broker, clock)
    import asyncio

    orders = asyncio.run(session.square_off())
    assert len(orders) == 1
    assert orders[0].request.symbol == "INFY"
    assert orders[0].request.quantity == 10
    assert orders[0].request.side.value == "SELL"
    assert broker.positions["INFY"].quantity == 0


def test_square_off_respects_kill_switch(clock) -> None:
    now = datetime.now(UTC)
    broker = PaperBroker(Decimal("100000"), clock)
    broker.positions["INFY"] = Position(symbol="INFY", quantity=10, average_entry_price=Decimal("100"))
    broker.set_price("INFY", Decimal("110"))
    session = _session([_state("INFY", "110", now)], broker, clock)
    session.shards[0].workflow.safety.kill_switch.enabled = True
    import asyncio

    assert asyncio.run(session.square_off()) == []
    assert broker.positions["INFY"].quantity == 10


def test_run_loop_squares_off_then_stops_reviewing(clock) -> None:
    now = datetime.now(UTC)
    broker = PaperBroker(Decimal("100000"), clock)
    broker.positions["INFY"] = Position(symbol="INFY", quantity=5, average_entry_price=Decimal("100"))
    broker.set_price("INFY", Decimal("110"))
    session = _session(
        [_state("INFY", "110", now)],
        broker,
        clock,
        intraday_square_off_time_ist="00:00",
    )
    import asyncio

    results = asyncio.run(session.run_loop(max_reviews=5))
    assert results == []
    assert session.shards[0].provider.calls == 0
    assert broker.positions["INFY"].quantity == 0


def test_run_loop_reviews_normally_before_cutoff(clock) -> None:
    now = datetime.now(UTC)
    broker = PaperBroker(Decimal("100000"), clock)
    session = _session(
        [_state("INFY", "110", now)],
        broker,
        clock,
        intraday_square_off_time_ist="23:59",
    )
    import asyncio

    results = asyncio.run(session.run_loop(max_reviews=1))
    assert len(results) == 1
    assert session.shards[0].provider.calls == 1
