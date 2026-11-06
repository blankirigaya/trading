"""Recent-bar cache: structure the AI can actually see, fetched only when stale.

The feed carries one day-candle per symbol, so without this the manager cannot
identify a breakout level or a trend. The cache must stay cheap (TTL), stay
bounded (≤ N bars per symbol), and never fail a review when FYERS history is
slow or down.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ai_trader.application.market_history import MarketHistory, rest_symbol
from ai_trader.domain.market import Candle, Instrument, MarketState


class FakeClock:
    def __init__(self) -> None:
        self.now_value = datetime(2026, 11, 6, 6, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.now_value

    def advance(self, seconds: int) -> None:
        self.now_value += timedelta(seconds=seconds)


def _instrument(symbol: str = "INFY") -> Instrument:
    return Instrument(instrument_key="1234", symbol=symbol, exchange="NSE", segment="EQ")


def _state(symbol: str = "INFY") -> MarketState:
    now = datetime(2026, 11, 6, 6, 0, tzinfo=UTC)
    candle = Candle(
        start_timestamp=now,
        end_timestamp=now,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=1000,
    )
    return MarketState(
        snapshot_id="s",
        instrument=_instrument(symbol),
        timestamp=now,
        last_price=Decimal("100"),
        previous_close=Decimal("100"),
        recent_ticks=[],
        current_1m_candle=candle,
        recent_1m_candles=[candle],
    )


def _candles(count: int, start: str = "100") -> list[Candle]:
    base = Decimal(start)
    result = []
    for index in range(count):
        price = base + index
        result.append(
            Candle(
                start_timestamp=datetime(2026, 11, 6, 5, 0, tzinfo=UTC) + timedelta(minutes=5 * index),
                end_timestamp=datetime(2026, 11, 6, 5, 5, tzinfo=UTC) + timedelta(minutes=5 * index),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=1000,
            )
        )
    return result


def _fetcher(candles, calls):
    async def fetch(instrument, symbol):
        calls.append(symbol)
        return list(candles)

    return fetch


def test_rest_symbol_matches_the_fyers_api_form() -> None:
    assert rest_symbol(_instrument("INFY")) == "NSE:INFY-EQ"


def test_bars_are_cached_within_the_ttl() -> None:
    calls: list[str] = []
    clock = FakeClock()
    history = MarketHistory(_fetcher(_candles(10), calls), clock, ttl_seconds=600)

    asyncio.run(history.for_board([_state()]))
    asyncio.run(history.for_board([_state()]))

    assert len(calls) == 1


def test_stale_bars_are_refreshed() -> None:
    calls: list[str] = []
    clock = FakeClock()
    history = MarketHistory(_fetcher(_candles(10), calls), clock, ttl_seconds=60)

    asyncio.run(history.for_board([_state()]))
    clock.advance(61)
    asyncio.run(history.for_board([_state()]))

    assert len(calls) == 2


def test_cache_is_bounded_to_the_requested_bar_count() -> None:
    clock = FakeClock()
    history = MarketHistory(_fetcher(_candles(80), []), clock, bars=30)

    bars = asyncio.run(history.for_board([_state()]))

    assert len(bars["INFY"]) == 30
    assert bars["INFY"][-1].close == Decimal("179")  # newest kept


def test_a_failing_fetch_degrades_to_no_bars() -> None:
    async def broken(instrument, symbol):
        raise RuntimeError("history down")

    clock = FakeClock()
    history = MarketHistory(broken, clock)

    assert asyncio.run(history.for_board([_state()])) == {}


def test_failures_are_retried_sooner_than_successes() -> None:
    calls: list[str] = []
    attempts = {"n": 0}

    async def flaky(instrument, symbol):
        attempts["n"] += 1
        calls.append(symbol)
        if attempts["n"] == 1:
            raise RuntimeError("first call fails")
        return _candles(5)

    clock = FakeClock()
    history = MarketHistory(flaky, clock, ttl_seconds=600, failure_ttl_seconds=30)

    assert asyncio.run(history.for_board([_state()])) == {}
    clock.advance(31)
    bars = asyncio.run(history.for_board([_state()]))

    assert len(bars["INFY"]) == 5
    assert len(calls) == 2
