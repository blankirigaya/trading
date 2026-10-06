"""FYERS market data service assembly for the shadow runtime.

Resolves configured symbols to HSM topics and wires the socket to the market-state
service. FYERS objects do not leave this package.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from ai_trader.config import Settings
from ai_trader.domain.common import Clock, SystemClock
from ai_trader.domain.market import Instrument, MarketState
from ai_trader.integrations.fyers.auth import FyersAuth
from ai_trader.integrations.fyers.client import FyersDataSocket
from ai_trader.integrations.fyers.instruments import parse_symbol, to_instrument
from ai_trader.integrations.fyers.market_data import FyersMarketDataService


@dataclass
class FyersFeed:
    """A live FYERS market data subscription plus the state it accumulates."""

    service: FyersMarketDataService
    socket: FyersDataSocket
    instruments: dict[str, Instrument]
    topics: list[str] = field(default_factory=list)
    topic_to_symbol: dict[str, str] = field(default_factory=dict)

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
) -> FyersFeed:
    """Resolve symbols to HSM topics and prepare the market data service."""
    auth = build_auth(settings)
    wanted = symbols or list(settings.shadow_symbols)
    parsed = [parse_symbol(symbol) for symbol in wanted]

    tokens = await auth.hsm_symbols([item.text for item in parsed])
    instruments: dict[str, Instrument] = {}
    topic_to_symbol: dict[str, str] = {}
    topics: list[str] = []
    for item in parsed:
        token = tokens.get(item.text)
        if token is None:
            raise LookupError(f"FYERS did not resolve an exchange token for {item.text}")
        topic = item.hsm_topic(token)
        topics.append(topic)
        topic_to_symbol[topic] = item.text
        instruments[topic] = to_instrument(item.text, token)

    service = FyersMarketDataService(instruments, clock or SystemClock())

    def on_frame(connection_id: str, payload: bytes) -> None:
        """Socket callback: decode only. Never awaits a reviewer."""
        service.ingest_frame(connection_id, payload)

    socket = FyersDataSocket(
        auth,
        on_frame=on_frame,
        feed_mode=settings.fyers_feed_mode,
    )
    await socket.subscribe(topics, topic_to_symbol)
    return FyersFeed(service, socket, instruments, topics, topic_to_symbol)
