"""The FYERS HSM feed silently drops whole subscriptions over 100 topics.

Verified live 2026-11-02: 100 topics stream, 101 topics deliver zero frames
while the handshake still succeeds. The feed layer must therefore trim, and
callers must order held positions first so the trim never strands them.
"""

from __future__ import annotations

from ai_trader.domain.common import SystemClock
from ai_trader.integrations.fyers.feed import MAX_SUBSCRIBE_TOPICS, FyersFeed
from ai_trader.integrations.fyers.market_data import FyersMarketDataService


class FakeSocket:
    def __init__(self) -> None:
        self.subscribed: list[str] | None = None
        self.reconnects = 0

    async def subscribe(self, topics: list[str], _mapping: dict[str, str]) -> None:
        self.subscribed = list(topics)

    async def reconnect(self) -> bool:
        self.reconnects += 1
        return True


def _feed(n_symbols: int) -> tuple[FyersFeed, FakeSocket]:
    symbols = [f"NSE:S{i:03d}-EQ" for i in range(n_symbols)]
    topics = [f"t{i}" for i in range(n_symbols)]
    symbol_to_topic = {s.upper(): t for s, t in zip(symbols, topics, strict=True)}
    all_topic_to_symbol = {t: s for s, t in zip(symbols, topics, strict=True)}
    service = FyersMarketDataService({}, SystemClock())
    socket = FakeSocket()
    feed = FyersFeed(
        service,
        socket,  # type: ignore[arg-type]
        {},
        [],
        {},
        symbol_to_topic,
        all_topic_to_symbol,
    )
    return feed, socket


async def test_subscribe_under_cap_passes_through() -> None:
    feed, socket = _feed(50)
    symbols = [f"NSE:S{i:03d}-EQ" for i in range(50)]
    assert await feed.subscribe_symbols(symbols) is True
    assert socket.subscribed is not None and len(socket.subscribed) == 50
    assert socket.reconnects == 1
    # Same set again: no-op, no reconnect.
    assert await feed.subscribe_symbols(symbols) is False
    assert socket.reconnects == 1


async def test_subscribe_over_cap_trims_to_first_100() -> None:
    assert MAX_SUBSCRIBE_TOPICS == 100
    feed, socket = _feed(105)
    symbols = [f"NSE:S{i:03d}-EQ" for i in range(105)]
    assert await feed.subscribe_symbols(symbols) is True
    assert socket.subscribed is not None
    assert len(socket.subscribed) == MAX_SUBSCRIBE_TOPICS
    # Order preserved: first 100 win, so held-first callers keep positions live.
    assert socket.subscribed == [f"t{i}" for i in range(100)]


async def test_subscribe_exactly_at_cap_is_untouched() -> None:
    feed, socket = _feed(100)
    symbols = [f"NSE:S{i:03d}-EQ" for i in range(100)]
    assert await feed.subscribe_symbols(symbols) is True
    assert socket.subscribed is not None and len(socket.subscribed) == 100


async def test_unresolvable_symbols_are_skipped_before_cap() -> None:
    feed, socket = _feed(10)
    symbols = [f"NSE:S{i:03d}-EQ" for i in range(10)] + ["NSE:UNKNOWN-EQ"]
    assert await feed.subscribe_symbols(symbols) is True
    assert socket.subscribed is not None and len(socket.subscribed) == 10
