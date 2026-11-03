"""FYERS market data service assembly for the shadow runtime.

Resolves configured symbols to HSM topics and wires the socket to the market-state
service. FYERS objects do not leave this package.

The feed resolves the whole candidate master list up front (so a later universe
switch can subscribe without another symbol-token round trip) but only subscribes
to the symbols the session currently asks for. A switch replaces the topic set
and drops the live socket; ``run`` reconnects and subscribes to the new set.
"""

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from ai_trader.config import Settings
from ai_trader.domain.common import Clock, SystemClock
from ai_trader.domain.market import Instrument, MarketState
from ai_trader.integrations.fyers.auth import FyersAuth
from ai_trader.integrations.fyers.client import FyersDataSocket
from ai_trader.integrations.fyers.instruments import (
    InvalidFyersSymbol,
    hsm_token_suffix,
    parse_symbol,
    to_instrument,
)
from ai_trader.integrations.fyers.market_data import FyersMarketDataService

logger = logging.getLogger(__name__)

# FYERS HSM data socket accepts at most 100 scrips per subscription.
# Verified live 2026-11-02: 100 topics stream, 101 topics silence the ENTIRE
# subscription (handshake still succeeds, so `connected` stays true and only
# feed_idle_seconds reveals it). The universe is 100 symbols by itself, so any
# held position appended on top pushes it over — trim, never exceed.
MAX_SUBSCRIBE_TOPICS = 100


@dataclass
class FyersFeed:
    """A live FYERS market data subscription plus the state it accumulates."""

    service: FyersMarketDataService
    socket: FyersDataSocket
    instruments: dict[str, Instrument]
    topics: list[str] = field(default_factory=list)
    topic_to_symbol: dict[str, str] = field(default_factory=dict)
    # Every resolved symbol (the full candidate list), so the session can move
    # the subscription between universes without re-resolving tokens.
    symbol_to_topic: dict[str, str] = field(default_factory=dict)
    all_topic_to_symbol: dict[str, str] = field(default_factory=dict)

    def latest(self) -> MarketState | None:
        for topic in self.topics:
            state = self.service.latest(topic)
            if state is not None:
                return state
        return None

    def mark_prices(self) -> dict[str, Decimal]:
        """Latest real prices keyed by internal instrument key, for paper valuation."""
        return self.service.mark_prices()

    def states(self) -> list[MarketState]:
        return [state for topic in self.topics if (state := self.service.latest(topic)) is not None]

    async def quote_prices(self, symbols: Iterable[str]) -> dict[str, Decimal]:
        """REST quotes for the master list, independent of the socket topics."""
        return await self.socket.auth.quotes(symbols)

    async def subscribe_symbols(self, symbols: Iterable[str]) -> bool:
        """Point the socket at a new symbol set; returns True when it changed.

        The service already knows every resolved instrument, so the new topics
        decode without rebuilding it. ``reconnect`` drops the live session and
        lets ``run`` resubscribe; it is a no-op before the first connection.

        The wanted set is trimmed to MAX_SUBSCRIBE_TOPICS (held positions must
        come first in ``symbols`` so the trim never strands an open position
        without live prices). Oversize requests are rejected wholesale by
        FYERS, so exceeding the cap would silence the whole feed, not just the
        extra symbols.
        """
        wanted: list[str] = []
        for symbol in symbols:
            topic = self.symbol_to_topic.get(symbol.upper())
            if topic is not None and topic not in wanted:
                wanted.append(topic)
        if len(wanted) > MAX_SUBSCRIBE_TOPICS:
            logger.warning(
                "feed subscription trimmed %d -> %d topics; "
                "symbols beyond the cap get no live prices",
                len(wanted),
                MAX_SUBSCRIBE_TOPICS,
            )
            wanted = wanted[:MAX_SUBSCRIBE_TOPICS]
        if wanted == self.topics:
            return False
        self.topics = wanted
        self.topic_to_symbol = {
            topic: self.all_topic_to_symbol[topic]
            for topic in wanted
            if topic in self.all_topic_to_symbol
        }
        await self.socket.subscribe(wanted, self.topic_to_symbol)
        await self.socket.reconnect()
        return True


def build_auth(settings: Settings) -> FyersAuth:
    return FyersAuth(
        settings.fyers_client_id,
        settings.fyers_access_token,
        settings.fyers_api_base_url,
        settings.fyers_data_ws_url,
    )


async def build_feed(
    settings: Settings,
    symbols: list[str] | None = None,
    clock: Clock | None = None,
    market_sink: Callable[[list[MarketState]], None] | None = None,
    master: Iterable[str] | None = None,
) -> FyersFeed:
    """Resolve symbols to HSM topics and prepare the market data service.

    ``master`` is the full candidate list to resolve (the review universe is a
    price-based subset of it). Only ``symbols`` is subscribed initially.
    ``market_sink`` is called with every batch of decoded ticks, on the socket
    task. That keeps the UI's snapshot store current at tick speed instead of
    review speed; a slow sink must never block ingestion, so keep it cheap.
    """
    auth = build_auth(settings)
    wanted = list(symbols or settings.shadow_symbols)
    desired = list(dict.fromkeys([*wanted, *(master or ())]))

    parsed_items = []
    for text in desired:
        try:
            parsed_items.append(parse_symbol(text))
        except InvalidFyersSymbol:
            logger.warning("FYERS symbol %r is not parseable; skipping", text)

    tokens = await auth.hsm_symbols([item.text for item in parsed_items])
    instruments: dict[str, Instrument] = {}
    symbol_to_topic: dict[str, str] = {}
    all_topic_to_symbol: dict[str, str] = {}
    for item in parsed_items:
        token = tokens.get(item.text)
        if token is None:
            # One delisted/renamed ticker must not kill the whole session:
            # skip it loudly so the watchlist visibly shrinks instead of the
            # backend refusing to start.
            logger.warning(
                "FYERS did not resolve an exchange token for %s; skipping", item.text
            )
            continue
        topic = item.hsm_topic(token)
        instruments[topic] = to_instrument(item.text, hsm_token_suffix(token))
        symbol_to_topic[item.text.upper()] = topic
        all_topic_to_symbol[topic] = item.text

    initial = [
        wanted_topic
        for symbol in wanted
        if (wanted_topic := symbol_to_topic.get(symbol.upper())) is not None
    ]
    topics = list(dict.fromkeys(initial))
    topic_to_symbol = {
        topic: all_topic_to_symbol[topic]
        for topic in topics
        if topic in all_topic_to_symbol
    }

    service = FyersMarketDataService(instruments, clock or SystemClock())

    if not topics:
        raise LookupError("FYERS resolved none of the configured symbols")

    def on_frame(connection_id: str, payload: bytes) -> None:
        """Socket callback: decode only. Never awaits a reviewer."""
        states = service.ingest_frame(connection_id, payload)
        if states and market_sink is not None:
            market_sink(states)

    socket = FyersDataSocket(
        auth,
        on_frame=on_frame,
        feed_mode=settings.fyers_feed_mode,
    )
    await socket.subscribe(topics, topic_to_symbol)
    return FyersFeed(
        service=service,
        socket=socket,
        instruments=instruments,
        topics=topics,
        topic_to_symbol=topic_to_symbol,
        symbol_to_topic=symbol_to_topic,
        all_topic_to_symbol=all_topic_to_symbol,
    )
