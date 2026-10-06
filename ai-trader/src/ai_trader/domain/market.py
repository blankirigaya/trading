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
