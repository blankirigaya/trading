"""Universe refresh: price filtering, re-sharding, and held-symbol safety."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from ai_trader.application.fyers_session import ReviewShard, ShadowSession
from ai_trader.brokers.paper import PaperBroker
from ai_trader.config import Settings
from ai_trader.domain.common import FakeClock
from ai_trader.domain.market import Candle, Instrument, MarketState, MarketTick
from ai_trader.domain.portfolio import Position
from ai_trader.journal.service import InMemoryDecisionJournal
from ai_trader.safety.envelope import KillSwitch

NOW = datetime(2026, 9, 1, 4, 0, tzinfo=UTC)


def _state(symbol: str, price: str) -> MarketState:
    tick = MarketTick(
        symbol=symbol,
        exchange="NSE",
        segment="EQ",
        last_price=Decimal(price),
        exchange_timestamp=NOW,
        received_timestamp=NOW,
        processed_timestamp=NOW,
        raw_payload={},
    )
    candle = Candle(
        start_timestamp=NOW,
        end_timestamp=NOW,
        open=Decimal(price),
        high=Decimal(price),
        low=Decimal(price),
        close=Decimal(price),
        volume=1000,
    )
    return MarketState(
        snapshot_id=f"s-{symbol}",
        instrument=Instrument(instrument_key=f"NSE:{symbol}-EQ", symbol=symbol, exchange="NSE"),
        timestamp=NOW,
        last_price=Decimal(price),
        previous_close=Decimal(price),
        recent_ticks=[tick],
        current_1m_candle=candle,
        recent_1m_candles=[candle],
    )


class QuoteFeed:
    """Feed double with REST quotes and a controllable subscription set."""

    def __init__(self, states: list[MarketState]) -> None:
        self._states = states
        self.socket = self
        self.symbol_to_topic = {
            f"NSE:{state.instrument.symbol}-EQ": f"topic-{state.instrument.symbol}"
            for state in states
        }
        self.subscribed: list[str] = []

    async def quote_prices(self, symbols: list[str]) -> dict[str, Decimal]:
        return {
            f"NSE:{state.instrument.symbol}-EQ": state.last_price
            for state in self._states
        }

    async def subscribe_symbols(self, symbols: list[str]) -> bool:
        self.subscribed = list(symbols)
        return True

    def states(self) -> list[MarketState]:
        return list(self._states)

    async def run(self) -> None:
        await asyncio.sleep(3600)

    async def stop(self) -> None:
        return None


def _session(
    feed: QuoteFeed,
    clock: FakeClock,
    master: tuple[str, ...],
    shard_count: int = 2,
) -> ShadowSession:
    settings = Settings(news_enabled=False, database_url="sqlite://", ai_review_shards=shard_count)
    session = ShadowSession(
        settings=settings,
        feed=feed,  # type: ignore[arg-type]
        journal=InMemoryDecisionJournal(),
        clock=clock,
        interval_seconds=0,
        kill_switch=KillSwitch(),
        universe_master=master,
        universe_size=3,
    )
    for index in range(shard_count):
        broker = PaperBroker(Decimal("100000"), clock)
        session.shards.append(
            ReviewShard(
                index=index,
                symbols=(),
                broker=broker,
                workflow=object(),  # type: ignore[arg-type]
                provider=object(),  # type: ignore[arg-type]
            )
        )
    return session


def test_refresh_selects_cheap_names_and_keeps_held_symbols() -> None:
    states = [
        _state("C0", "100"),
        _state("C1", "120"),
        _state("C2", "150"),
        _state("E0", "900"),
        _state("E1", "700"),
        _state("E2", "500"),
    ]
    feed = QuoteFeed(states)
    clock = FakeClock(NOW)
    master = tuple(f"NSE:{state.instrument.symbol}-EQ" for state in states)
    session = _session(feed, clock, master)
    session.universe_mode = "cheap"
    # Shard 0 holds an expensive name; it must stay visible and subscribed.
    session.shards[0].broker.positions["E0"] = Position(
        symbol="E0", quantity=5, average_entry_price=Decimal("900")
    )

    asyncio.run(session._refresh_universe())

    assert set(session.universe_selected) == {"NSE:C0-EQ", "NSE:C1-EQ", "NSE:C2-EQ"}
    assert "NSE:E0-EQ" in feed.subscribed
    assert "NSE:E1-EQ" not in feed.subscribed
    shard_zero = set(session.shards[0].symbols)
    assert "NSE:E0-EQ" in shard_zero
    assert all(not symbol.startswith("NSE:E1") for shard in session.shards for symbol in shard.symbols)

    boards = session._shard_boards()
    symbols = {state.instrument.symbol for board in boards for state in board}
    assert symbols == {"C0", "C1", "C2", "E0"}


def test_switching_to_expensive_resubscribes_and_filters_cheap_names() -> None:
    states = [
        _state("C0", "100"),
        _state("C1", "120"),
        _state("C2", "150"),
        _state("E0", "900"),
        _state("E1", "700"),
        _state("E2", "500"),
    ]
    feed = QuoteFeed(states)
    clock = FakeClock(NOW)
    master = tuple(f"NSE:{state.instrument.symbol}-EQ" for state in states)
    session = _session(feed, clock, master)
    session.universe_mode = "cheap"
    asyncio.run(session._refresh_universe())

    session.universe_mode = "expensive"
    asyncio.run(session._refresh_universe())

    assert set(session.universe_selected) == {"NSE:E0-EQ", "NSE:E1-EQ", "NSE:E2-EQ"}
    assert set(feed.subscribed) == {"NSE:E0-EQ", "NSE:E1-EQ", "NSE:E2-EQ"}
    boards = session._shard_boards()
    symbols = {state.instrument.symbol for board in boards for state in board}
    assert symbols == {"E0", "E1", "E2"}


def test_board_filter_hides_names_that_crossed_the_cap() -> None:
    states = [_state("C0", "100"), _state("C1", "250")]
    feed = QuoteFeed(states)
    clock = FakeClock(NOW)
    master = ("NSE:C0-EQ", "NSE:C1-EQ")
    session = _session(feed, clock, master, shard_count=1)
    session.universe_master = master
    session.universe_mode = "cheap"
    session.shards[0].symbols = master

    symbols = {state.instrument.symbol for state in session._shard_boards()[0]}
    assert symbols == {"C0"}


def test_quote_failure_keeps_the_previous_universe() -> None:
    states = [_state("C0", "100")]
    feed = QuoteFeed(states)
    clock = FakeClock(NOW)
    session = _session(feed, clock, ("NSE:C0-EQ",), shard_count=1)
    session.universe_selected = ("NSE:C0-EQ",)
    session.shards[0].symbols = ("NSE:C0-EQ",)

    async def boom(_symbols: list[str]) -> dict[str, Decimal]:
        raise RuntimeError("network down")

    feed.quote_prices = boom  # type: ignore[method-assign]
    asyncio.run(session._refresh_universe())

    assert session.universe_selected == ("NSE:C0-EQ",)
    assert session.shards[0].symbols == ("NSE:C0-EQ",)
