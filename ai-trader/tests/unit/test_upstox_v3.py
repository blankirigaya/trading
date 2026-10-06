"""Binary protobuf -> generated V3 classes -> provider normalizer -> internal MarketState.

No AI or trading logic is involved in these tests.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from ai_trader.domain.market import Instrument, RawMarketEvent
from ai_trader.integrations.upstox.instruments import InstrumentResolutionError, resolve_instrument
from ai_trader.integrations.upstox.market_data import UpstoxMarketDataService
from ai_trader.integrations.upstox.protobuf.decoder import FeedType, V3DecodeError, decode_v3_frame

FIXTURES = Path(__file__).parent.parent / "fixtures"
INSTRUMENT_KEY = "NSE_EQ|INE009A01021"
INSTRUMENT = Instrument(instrument_key=INSTRUMENT_KEY, symbol="INFY", exchange="NSE")


def load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_market_info_fixture_decodes_status(clock) -> None:
    decoded = decode_v3_frame(load("v3_market_info.bin"))
    assert decoded.feed_type is FeedType.MARKET_INFO
    assert decoded.market_status["NSE"] == "NORMAL_OPEN"
    assert decoded.market_status["BSE"] == "CLOSING_END"


def test_initial_snapshot_decodes_to_market_state(clock) -> None:
    decoded = decode_v3_frame(load("v3_initial_snapshot.bin"))
    assert decoded.feed_type is FeedType.INITIAL_FEED
    assert INSTRUMENT_KEY in decoded.instrument_keys

    service = UpstoxMarketDataService(INSTRUMENT, clock)
    state = service.ingest("conn-1", load("v3_initial_snapshot.bin"))

    assert state.last_price == Decimal("1500.25")
    assert state.previous_close == Decimal("1490.1")
    assert state.recent_ticks[0].symbol == "INFY"
    assert state.recent_ticks[0].exchange_timestamp.tzinfo is not None
    assert state.bid == Decimal("1500.20")
    assert state.ask == Decimal("1500.30")
    assert len(state.recent_1m_candles) == 2


def test_live_update_decodes_to_market_state(clock) -> None:
    decoded = decode_v3_frame(load("v3_live_update.bin"))
    assert decoded.feed_type is FeedType.LIVE_FEED

    service = UpstoxMarketDataService(INSTRUMENT, clock)
    state = service.ingest("conn-1", load("v3_live_update.bin"))

    assert state.last_price == Decimal("1501.80")
    assert state.bid == Decimal("1501.75")
    assert service.live_update_count == 1
    assert service.decoded_count == 1


def test_service_retains_bounded_history(clock) -> None:
    service = UpstoxMarketDataService(INSTRUMENT, clock)
    for _ in range(125):
        service.ingest("conn-1", load("v3_live_update.bin"))
    assert len(service.states) == 120
    assert service.decoded_count == 125


def test_raw_bytes_are_preserved_for_journaling(clock) -> None:
    payload = load("v3_live_update.bin")
    service = UpstoxMarketDataService(INSTRUMENT, clock)
    state = service.ingest("conn-1", payload)
    assert state.recent_ticks[0].raw_payload["feeds"][INSTRUMENT_KEY]["feed_union"] == "fullFeed"


def test_invalid_binary_frame_is_rejected() -> None:
    with pytest.raises(V3DecodeError):
        decode_v3_frame(b"not-a-protobuf-frame-at-all\xff\xfe")


def test_market_info_frame_without_instrument_is_rejected(clock) -> None:
    service = UpstoxMarketDataService(INSTRUMENT, clock)
    with pytest.raises(ValueError, match="does not include the subscribed instrument"):
        service.ingest("conn-1", load("v3_market_info.bin"))


def test_instrument_resolution_uses_instrument_key() -> None:
    rows = [{"trading_symbol": "INFY", "exchange": "NSE", "instrument_key": INSTRUMENT_KEY}]
    assert resolve_instrument(rows, "INFY", "NSE").instrument_key == INSTRUMENT_KEY
    with pytest.raises(InstrumentResolutionError):
        resolve_instrument(rows, "TCS", "NSE")


def test_raw_market_event_is_immutable_and_typed(clock) -> None:
    raw = RawMarketEvent(
        provider="upstox",
        connection_id="conn-1",
        instrument_key=INSTRUMENT_KEY,
        received_timestamp=clock.now(),
        schema_version="market-data-feed-v3",
        feed_mode="full",
        raw_payload=load("v3_live_update.bin"),
    )
    with pytest.raises(ValueError):
        raw.raw_payload = b"mutated"  # type: ignore[misc]
