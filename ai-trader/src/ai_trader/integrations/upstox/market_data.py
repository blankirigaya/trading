from collections import deque

from ai_trader.domain.common import Clock
from ai_trader.domain.market import Instrument, MarketState, RawMarketEvent
from ai_trader.integrations.upstox.normalizer import normalize_v3_feed
from ai_trader.integrations.upstox.protobuf.decoder import (
    SCHEMA_VERSION,
    DecodedFeed,
    FeedType,
    decode_v3_frame,
)


class UpstoxMarketDataService:
    """Keeps ingestion fast; callers schedule AI reviews independently of feed callbacks."""

    def __init__(self, instrument: Instrument, clock: Clock) -> None:
        self.instrument = instrument
        self.clock = clock
        self.states: deque[MarketState] = deque(maxlen=120)
        self.last_decoded: DecodedFeed | None = None
        self.decoded_count = 0
        self.live_update_count = 0

    def ingest(self, connection_id: str, payload: bytes) -> MarketState:
        decoded = decode_v3_frame(payload)
        raw = RawMarketEvent(
            provider="upstox",
            connection_id=connection_id,
            instrument_key=self.instrument.instrument_key,
            received_timestamp=self.clock.now(),
            schema_version=SCHEMA_VERSION,
            feed_mode="full",
            raw_payload=payload,
        )
        self.last_decoded = decoded
        self.decoded_count += 1
        if decoded.feed_type is FeedType.LIVE_FEED:
            self.live_update_count += 1

        # market_info frames carry no instrument prices; they are still decoded and recorded.
        if self.instrument.instrument_key not in decoded.instrument_keys:
            raise ValueError("V3 frame does not include the subscribed instrument")

        state = normalize_v3_feed(raw, self.instrument, decoded)
        self.states.append(state)
        return state

    @property
    def latest(self) -> MarketState | None:
        return self.states[-1] if self.states else None
