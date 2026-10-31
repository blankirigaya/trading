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
        # Update frames carry only the numeric topic id; snapshots provide the
        # id -> topic name mapping that resolves them.
        self.topic_names: dict[int, str] = {}
        self.decoded_count = 0
        self.snapshot_count = 0
        self.update_count = 0
        self.last_error: str | None = None
        # Wall-clock of the last datafeed frame, whatever it carried, and of the
        # last frame that actually produced a usable tick. A connected-but-silent
        # socket is the failure mode the reconnect logic cannot see, so the
        # session watchdog reads these to prod it. Freshness is measured from
        # ``last_state_at``: a socket that only delivers undecodable frames is
        # stalled even though it is busy.
        self.last_frame_at: datetime | None = None
        self.last_state_at: datetime | None = None

    def ingest_frame(self, connection_id: str, payload: bytes) -> list[MarketState]:
        """Decode one binary frame into zero or more market states."""
        if not is_market_data_frame(payload):
            return []
        self.last_frame_at = self.clock.now()
        _, feeds = decode_binary_frame(payload)
        produced: list[MarketState] = []
        for feed in feeds:
            if feed.topic_name is not None:
                self.topic_names[feed.topic_id] = feed.topic_name
                topic_name: str | None = feed.topic_name
            else:
                topic_name = self.topic_names.get(feed.topic_id)
            if topic_name is None:
                self.last_error = (
                    f"update for unknown topic id {feed.topic_id}; no snapshot seen yet"
                )
                continue
            instrument = self.instruments.get(topic_name)
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
            self._merge(topic_name, state)
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
        self.last_state_at = self.clock.now()

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

    def idle_seconds(self, now: datetime) -> float | None:
        """Seconds since the last usable tick, or None if none yet.

        Deliberately ignores frames that decoded to nothing (unknown topic ids,
        payloads without a price). Counting those as life would make a socket
        that is chattering but delivering no prices look healthy, and the
        reconnect watchdog would never prod it.
        """
        marker = self.last_state_at or self.last_frame_at
        if marker is None:
            return None
        return (now - marker).total_seconds()

    def newest_state_age_seconds(self, now: datetime) -> float | None:
        """Age of the freshest stored state, or None before the first tick."""
        if not self.states:
            return None
        return min((now - state.timestamp).total_seconds() for state in self.states.values())
