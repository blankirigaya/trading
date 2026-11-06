"""Recent 5-minute bars per symbol, cached and fetched on demand.

The live FYERS feed carries only the day's OHLC, which is enough to state a
price but not to see structure: no breakout levels, no trend, no real ATR. This
cache supplies the missing history to the manager prompt and to the trailing
stop, refreshing on a short TTL so one review does not hammer the history API.

Failure is always soft: a symbol with no bars simply keeps the session-range
fallback, and a slow or broken history endpoint must never fail a review.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from ai_trader.domain.common import Clock
from ai_trader.domain.market import Candle, Instrument, MarketState

logger = logging.getLogger(__name__)

BarFetcher = Callable[[Instrument, str], Awaitable[list[Candle]]]


def rest_symbol(instrument: Instrument) -> str:
    """FYERS REST symbol (``NSE:INFY-EQ``) from the normalized instrument."""
    return f"{instrument.exchange}:{instrument.symbol}-{instrument.segment}"


@dataclass(frozen=True)
class _Entry:
    candles: list[Candle]
    fetched_at: datetime


class MarketHistory:
    """TTL cache of recent bars, keyed by symbol."""

    def __init__(
        self,
        fetch: BarFetcher,
        clock: Clock,
        *,
        bars: int = 30,
        ttl_seconds: int = 900,
        failure_ttl_seconds: int = 120,
        max_concurrent: int = 1,
        min_interval_seconds: float = 0.35,
        timeout_seconds: float = 12.0,
    ) -> None:
        self._fetch = fetch
        self._clock = clock
        self._bars = max(1, bars)
        self._ttl = max(1, ttl_seconds)
        self._failure_ttl = max(1, failure_ttl_seconds)
        self._max_concurrent = max(1, max_concurrent)
        self._min_interval = max(0.0, min_interval_seconds)
        self._timeout = timeout_seconds
        self._cache: dict[str, _Entry] = {}

    def _fresh(self, entry: _Entry) -> bool:
        ttl = self._ttl if entry.candles else self._failure_ttl
        age = (self._clock.now() - entry.fetched_at).total_seconds()
        return age < ttl

    async def for_board(
        self,
        states: list[MarketState],
        *,
        only: set[str] | None = None,
    ) -> dict[str, tuple[Candle, ...]]:
        """Return cached bars for every board symbol that has them.

        ``only`` restricts *fetching* to a focus set (the screener shortlist and
        held positions). FYERS rate-limits the history endpoint hard — a burst
        of board-wide requests earns 429s and starves the app — so this is
        deliberately narrow; already-cached symbols are always returned.
        """
        fetchable = {symbol.upper() for symbol in only} if only is not None else None
        pending: dict[str, Instrument] = {}
        for state in states:
            symbol = state.instrument.symbol.upper()
            entry = self._cache.get(symbol)
            if entry is not None and self._fresh(entry):
                continue
            if fetchable is not None and symbol not in fetchable:
                continue
            pending[symbol] = state.instrument

        if pending:
            semaphore = asyncio.Semaphore(self._max_concurrent)

            async def load(symbol: str, instrument: Instrument) -> None:
                async with semaphore:
                    # Serialised and spaced: FYERS rejects bursts of history
                    # requests with 429, which previously took the listener down.
                    if self._min_interval:
                        await asyncio.sleep(self._min_interval)
                    try:
                        candles = await asyncio.wait_for(
                            self._fetch(instrument, rest_symbol(instrument)),
                            timeout=self._timeout,
                        )
                    except Exception as exc:  # noqa: BLE001 - history is best effort
                        logger.debug("history fetch failed for %s: %s", symbol, exc)
                        self._cache[symbol] = _Entry([], self._clock.now())
                        return
                    if candles:
                        self._cache[symbol] = _Entry(
                            sorted(candles, key=lambda c: c.start_timestamp)[-self._bars :],
                            self._clock.now(),
                        )
                    else:
                        self._cache[symbol] = _Entry([], self._clock.now())

            await asyncio.gather(*(load(s, i) for s, i in pending.items()))

        results: dict[str, tuple[Candle, ...]] = {}
        for state in states:
            symbol = state.instrument.symbol.upper()
            entry = self._cache.get(symbol)
            if entry is not None and entry.candles:
                results[symbol] = tuple(entry.candles)
        return results
