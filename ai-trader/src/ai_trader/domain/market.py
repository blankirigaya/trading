from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class MarketTick(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str
    exchange: str = "NSE"
    segment: str = "EQ"
    last_price: Decimal = Field(gt=0)
    exchange_timestamp: datetime
    received_timestamp: datetime
    processed_timestamp: datetime
    raw_payload: dict[str, object] = Field(default_factory=dict)


class Instrument(BaseModel):
    model_config = ConfigDict(frozen=True)
    instrument_key: str
    symbol: str
    exchange: str
    segment: str = "EQ"


class Candle(BaseModel):
    model_config = ConfigDict(frozen=True)
    start_timestamp: datetime
    end_timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int | None = None


class RawMarketEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    provider: str
    connection_id: str
    instrument_key: str
    received_timestamp: datetime
    exchange_timestamp: datetime | None = None
    schema_version: str
    feed_mode: str
    raw_payload: bytes


class MarketState(BaseModel):
    model_config = ConfigDict(frozen=True)
    snapshot_id: str
    instrument: Instrument
    timestamp: datetime
    last_price: Decimal
    previous_close: Decimal | None = None
    recent_ticks: list[MarketTick]
    current_1m_candle: Candle | None = None
    recent_1m_candles: list[Candle] = Field(default_factory=list)
    bid: Decimal | None = None
    ask: Decimal | None = None


def stale_data_reason(
    state: MarketState, now: datetime, max_age_seconds: int
) -> str | None:
    """Return why ``state`` must not drive a review/trade, or None if it may.

    A review is only allowed when the AI would see a fresh, complete picture:
    a positive last price, a current OHLCV candle, and a tick received within
    ``max_age_seconds``. Anything less skips the review instead of trading blind.
    """
    if state.last_price <= 0:
        return f"no usable last price for {state.instrument.symbol}"
    if state.current_1m_candle is None:
        return f"no current OHLCV candle for {state.instrument.symbol}"
    if not state.recent_ticks:
        return f"no ticks for {state.instrument.symbol}"
    latest = max(tick.received_timestamp for tick in state.recent_ticks)
    age = (now - latest).total_seconds()
    if age > max_age_seconds:
        return (
            f"market data for {state.instrument.symbol} is {age:.0f}s old "
            f"(limit {max_age_seconds}s)"
        )
    return None
