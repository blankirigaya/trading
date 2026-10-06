"""Convert FYERS wire data into internal domain models.

Application and domain code never consume FYERS dicts or protocol objects.
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from ai_trader.domain.market import Candle, Instrument, MarketState, MarketTick, RawMarketEvent
from ai_trader.integrations.fyers.protocol import ScripFeed

SCHEMA_VERSION = "fyers-data-ws-v1-5"


class FyersDecodeError(ValueError):
    """The FYERS payload could not be mapped to a usable market state."""


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def _epoch_to_datetime(value: object) -> datetime | None:
    """FYERS encodes feed times as epoch seconds (sometimes milliseconds)."""
    raw = _decimal(value)
    if raw is None or raw <= 0:
        return None
    seconds = float(raw)
    if seconds > 1e11:
        seconds /= 1000.0
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def build_raw_event(
    connection_id: str,
    instrument_key: str,
    received_at: datetime,
    payload: bytes,
) -> RawMarketEvent:
    """Preserve the raw frame before any normalization."""
    return RawMarketEvent(
        provider="fyers",
        connection_id=connection_id,
        instrument_key=instrument_key,
        received_timestamp=received_at,
        schema_version=SCHEMA_VERSION,
        feed_mode="symbolUpdate",
        raw_payload=payload,
    )


def normalize_scrip(
    raw: RawMarketEvent,
    instrument: Instrument,
    feed: ScripFeed,
) -> MarketState:
    """Map one FYERS scrip payload into a MarketState. No trading judgment here."""
    fields = feed.as_mapping()
    ltp = _decimal(fields.get("ltp"))
    if ltp is None or ltp <= 0:
        raise FyersDecodeError("FYERS payload has no usable last traded price")

    exchange_timestamp = (
        _epoch_to_datetime(fields.get("exch_feed_time"))
        or _epoch_to_datetime(fields.get("last_traded_time"))
        or raw.received_timestamp
    )

    volume = fields.get("vol_traded_today")
    open_price = _decimal(fields.get("open_price"))
    high_price = _decimal(fields.get("high_price"))
    low_price = _decimal(fields.get("low_price"))
    prev_close = _decimal(fields.get("prev_close_price"))

    candle: Candle | None = None
    if open_price and high_price and low_price:
        candle = Candle(
            start_timestamp=exchange_timestamp,
            end_timestamp=exchange_timestamp,
            open=open_price,
            high=high_price,
            low=low_price,
            close=ltp,
            volume=int(volume) if isinstance(volume, int) else None,
        )

    tick = MarketTick(
        symbol=instrument.symbol,
        exchange=instrument.exchange,
        segment=instrument.segment,
        last_price=ltp,
        exchange_timestamp=exchange_timestamp,
        received_timestamp=raw.received_timestamp,
        processed_timestamp=raw.received_timestamp,
        raw_payload={
            "topic": feed.topic_name,
            "data_type": feed.data_type,
            "fields": {key: float(value) for key, value in fields.items()},
        },
    )

    return MarketState(
        snapshot_id=str(uuid4()),
        instrument=instrument,
        timestamp=exchange_timestamp,
        last_price=ltp,
        previous_close=prev_close,
        recent_ticks=[tick],
        current_1m_candle=candle,
        recent_1m_candles=[candle] if candle else [],
        bid=_decimal(fields.get("bid_price")),
        ask=_decimal(fields.get("ask_price")),
    )


def normalize_ohlcv_rows(instrument: Instrument, rows: list[dict[str, object]]) -> list[Candle]:
    """Convert FYERS historical OHLCV rows into ascending Candle models."""
    candles: list[Candle] = []
    for row in rows:
        epoch = row.get("epoch")
        close = _decimal(row.get("close"))
        if epoch is None or close is None:
            continue
        timestamp = _epoch_to_datetime(epoch)
        if timestamp is None:
            continue
        open_price = _decimal(row.get("open")) or close
        high_price = _decimal(row.get("high")) or close
        low_price = _decimal(row.get("low")) or close
        volume = row.get("volume")
        candles.append(
            Candle(
                start_timestamp=timestamp,
                end_timestamp=timestamp,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close,
                volume=int(volume) if isinstance(volume, (int, float)) else None,
            )
        )
    return sorted(candles, key=lambda candle: candle.start_timestamp)
