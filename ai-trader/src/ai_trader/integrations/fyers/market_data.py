"""Maintain per-instrument MarketState from the FYERS data stream.

Decoding happens on the socket task. Review scheduling is the caller's concern, so a
slow remote AI review never stalls ingestion.
"""

from collections import deque
from datetime import datetime
from decimal import Decimal

from ai_trader.domain.common import Clock
from ai_trader.domain.market import Instrument, MarketState
from ai_trader.integrations.fyers.client import decode_binary_frame, is_market_data_frame
from ai_trader.integrations.fyers.normalizer import (
    FyersDecodeError,
    build_raw_event,
    normalize_scrip,
)
from ai_trader.integrations.fyers.protocol import DataType

MAX_TICKS_PER_INSTRUMENT = 60
MAX_CANDLES_PER_INSTRUMENT = 60


class FyersMarketDataService:
    """Holds the latest real market state for each tracked instrument."""

    def __init__(self, instruments: dict[str, Instrument], clock: Clock) -> None:
        """``instruments`` maps HSM topic (``sf|nse_cm|3045``) to the internal instrument."""
        self.instruments = instruments
        self.clock = clock
        self.states: dict[str, MarketState] = {}
        self.ticks: dict[str, deque[MarketState]] = {
            key: deque(maxlen=MAX_TICKS_PER_INSTRUMENT) for key in instruments
        }
        self.decoded_count = 0
        self.snapshot_count = 0
        self.update_count = 0
        self.last_error: str | None = None

    def ingest_frame(self, connection_id: str, payload: bytes) -> list[MarketState]:
        """Decode one binary frame into zero or more market states."""
        if not is_market_data_frame(payload):
            return []
        _, feeds = decode_binary_frame(payload)
        produced: list[MarketState] = []
        for feed in feeds:
            instrument = self.instruments.get(feed.topic_name)
            if instrument is None:
                continue
            if feed.data_type not in (DataType.SNAPSHOT, DataType.UPDATE, DataType.LITE):
                continue
            raw = build_raw_event(
                connection_id, instrument.instrument_key, self.clock.now(), payload
            )
            try:
                state = normalize_scrip(raw, instrument, feed)
            except FyersDecodeError:
                self.last_error = "fyers payload had no usable last traded price"
                continue
            self.decoded_count += 1
            if feed.data_type == DataType.SNAPSHOT:
                self.snapshot_count += 1
            else:
                self.update_count += 1
            self._merge(feed.topic_name, state)
            produced.append(state)
        return produced

    def _merge(self, topic: str, state: MarketState) -> None:
        history = self.ticks[topic]
        if history:
            previous = history[-1]
            state = state.model_copy(
                update={
                    "recent_ticks": [*previous.recent_ticks[-5:], *state.recent_ticks],
                    "recent_1m_candles": [
                        *previous.recent_1m_candles[-5:],
                        *state.recent_1m_candles,
                    ][-MAX_CANDLES_PER_INSTRUMENT:],
                }
            )
        history.append(state)
        self.states[topic] = state

    def latest(self, topic: str) -> MarketState | None:
        return self.states.get(topic)

    def latest_by_symbol(self, symbol: str) -> MarketState | None:
        for topic, instrument in self.instruments.items():
            if instrument.symbol == symbol:
                return self.states.get(topic)
        return None

    def mark_prices(self) -> dict[str, Decimal]:
        """Latest real price per internal instrument key, for paper mark-to-market."""
        return {
            instrument.instrument_key: state.last_price
            for state in self.states.values()
            for instrument in [state.instrument]
        }

    def price_age_seconds(self, topic: str, now: datetime) -> float | None:
        state = self.states.get(topic)
        if state is None:
            return None
        return (now - state.timestamp).total_seconds()
