"""FYERS -> internal MarketState normalization and paper mark-to-market."""

from decimal import Decimal
from pathlib import Path

import pytest

from ai_trader.domain.market import Instrument
from ai_trader.integrations.fyers.instruments import (
    InvalidFyersSymbol,
    parse_symbol,
    to_instrument,
)
from ai_trader.integrations.fyers.market_data import FyersMarketDataService
from ai_trader.integrations.fyers.normalizer import (
    FyersDecodeError,
    build_raw_event,
    normalize_ohlcv_rows,
    normalize_scrip,
)
from ai_trader.integrations.fyers.protocol import DataType, parse_datafeed

FIXTURES = Path(__file__).parent.parent / "fixtures"
TOPIC = "sf|nse_cm|3045"
INSTRUMENT = to_instrument("NSE:INFY-EQ", 3045)


def _feed(name: str):
    _, feeds = parse_datafeed((FIXTURES / name).read_bytes())
    return feeds[0]


def _raw(clock):
    return build_raw_event("conn-1", INSTRUMENT.instrument_key, clock.now(), b"frame")


def test_symbol_parsing_and_internal_identity() -> None:
    parsed = parse_symbol("NSE:INFY-EQ")
    assert parsed.exchange == "NSE"
    assert parsed.ticker == "INFY"
    assert parsed.is_equity is True
    assert parsed.hsm_topic(3045) == TOPIC

    instrument = to_instrument("NSE:INFY-EQ", 3045)
    assert isinstance(instrument, Instrument)
    assert instrument.symbol == "INFY"
    assert instrument.exchange == "NSE"
    assert instrument.segment == "EQ"


def test_multiple_symbols_resolve() -> None:
    for symbol, ticker in (
        ("NSE:INFY-EQ", "INFY"),
        ("NSE:TCS-EQ", "TCS"),
        ("NSE:RELIANCE-EQ", "RELIANCE"),
    ):
        assert to_instrument(symbol).symbol == ticker


def test_non_equity_symbol_is_rejected() -> None:
    with pytest.raises(InvalidFyersSymbol):
        to_instrument("NSE:INFY-FUT")


def test_unparseable_symbol_is_rejected() -> None:
    with pytest.raises(InvalidFyersSymbol):
        parse_symbol("INFY")


def test_snapshot_normalizes_to_market_state(clock) -> None:
    state = normalize_scrip(_raw(clock), INSTRUMENT, _feed("fyers_snapshot.bin"))

    assert state.last_price == Decimal("1500.25")
    assert state.previous_close == Decimal("1490.10")
    assert state.instrument.symbol == "INFY"
    assert state.recent_ticks[0].exchange_timestamp.tzinfo is not None
    assert state.bid == Decimal("1500.20")
    assert state.ask == Decimal("1500.30")
    assert state.current_1m_candle is not None
    assert state.current_1m_candle.open == Decimal("1491.00")
    assert state.current_1m_candle.high == Decimal("1510.00")
    assert state.current_1m_candle.low == Decimal("1485.00")
    assert state.current_1m_candle.close == Decimal("1500.25")
    assert state.current_1m_candle.volume == 1_250_000


def test_update_normalizes_newer_price(clock) -> None:
    state = normalize_scrip(_raw(clock), INSTRUMENT, _feed("fyers_update.bin"))
    assert state.last_price == Decimal("1501.80")
    # Partial updates carry no OHLC, so no candle is produced.
    assert state.current_1m_candle is None


def test_lite_normalizes_ltp_only(clock) -> None:
    state = normalize_scrip(_raw(clock), INSTRUMENT, _feed("fyers_lite.bin"))
    assert state.last_price == Decimal("1501.80")
    assert state.previous_close is None


def test_payload_without_price_is_rejected(clock) -> None:
    from ai_trader.integrations.fyers.protocol import ScripFeed

    feed = ScripFeed(DataType.SNAPSHOT, 1, TOPIC, (0, 0))
    with pytest.raises(FyersDecodeError, match="no usable last traded price"):
        normalize_scrip(_raw(clock), INSTRUMENT, feed)


def test_raw_event_preserves_provider_metadata(clock) -> None:
    raw = build_raw_event("conn-9", "3045", clock.now(), b"\x00\x01binary")
    assert raw.provider == "fyers"
    assert raw.connection_id == "conn-9"
    assert raw.schema_version.startswith("fyers")
    assert raw.raw_payload == b"\x00\x01binary"
    assert raw.received_timestamp.tzinfo is not None


def test_service_merges_updates_into_latest_state(clock) -> None:
    service = FyersMarketDataService({TOPIC: INSTRUMENT}, clock)
    snapshot = service.ingest_frame("c1", (FIXTURES / "fyers_snapshot.bin").read_bytes())
    assert len(snapshot) == 1
    assert service.decoded_count == 1
    assert service.snapshot_count == 1

    update = service.ingest_frame("c1", (FIXTURES / "fyers_update.bin").read_bytes())
    assert len(update) == 1
    assert service.update_count == 1
    latest = service.latest(TOPIC)
    assert latest is not None
    assert latest.last_price == Decimal("1501.80")
    # History is retained across updates.
    assert len(latest.recent_ticks) >= 2


def test_service_ignores_unsubscribed_topics(clock) -> None:
    service = FyersMarketDataService({TOPIC: INSTRUMENT}, clock)
    other = bytearray((FIXTURES / "fyers_update.bin").read_bytes())
    # Rewrite the topic name to an untracked symbol of identical length.
    index = other.index(b"sf|nse_cm|3045")
    other[index : index + 14] = b"sf|nse_cm|9999"
    assert service.ingest_frame("c1", bytes(other)) == []
    assert service.decoded_count == 0


def test_service_marks_prices_for_paper_valuation(clock) -> None:
    service = FyersMarketDataService({TOPIC: INSTRUMENT}, clock)
    service.ingest_frame("c1", (FIXTURES / "fyers_snapshot.bin").read_bytes())
    marks = service.mark_prices()
    assert marks[INSTRUMENT.instrument_key] == Decimal("1500.25")


def test_historical_ohlcv_rows_normalize_in_order() -> None:
    rows = [
        {
            "epoch": "1757000600",
            "open": "1502",
            "high": "1506",
            "low": "1500",
            "close": "1504",
            "volume": 900,
        },
        {
            "epoch": "1757000000",
            "open": "1500",
            "high": "1503",
            "low": "1498",
            "close": "1502",
            "volume": 800,
        },
    ]
    candles = normalize_ohlcv_rows(INSTRUMENT, rows)
    assert len(candles) == 2
    assert candles[0].close < candles[1].close
    assert candles[0].open == Decimal("1500")
    assert candles[0].volume == 800


def test_malformed_historical_rows_are_skipped() -> None:
    candles = normalize_ohlcv_rows(
        INSTRUMENT, [{"epoch": "1757000000"}, {"open": "1", "close": "2"}]
    )
    assert candles == []
