from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class OrderRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    client_order_id: str
    decision_id: str
    correlation_id: str
    symbol: str
    exchange: str
    segment: str
    side: OrderSide
    order_type: OrderType
    quantity: int = Field(gt=0)
    limit_price: Decimal | None = Field(default=None, gt=0)
    requested_allocation: Decimal = Field(gt=0)
    actual_order_value: Decimal = Field(gt=0)
    conversion_reason: str
    product: Literal["INTRADAY", "CNC"] = "INTRADAY"
    validity: Literal["DAY", "IOC"] = "DAY"


class Order(BaseModel):
    model_config = ConfigDict(frozen=True)
    broker_order_id: str
    request: OrderRequest
    status: OrderStatus
    created_at: datetime
    broker_name: str = "paper"
    live_order: bool = False
    filled_quantity: int | None = None
    average_fill_price: Decimal | None = None
    broker_status_detail: str | None = None


class LiveOrderUpdate(BaseModel):
    """Explicit live-order change. Base modify calls cannot express new terms."""

    model_config = ConfigDict(frozen=True)
    broker_order_id: str
    quantity: int | None = Field(default=None, gt=0)
    limit_price: Decimal | None = Field(default=None, gt=0)


class Fill(BaseModel):
    model_config = ConfigDict(frozen=True)
    fill_id: str
    broker_order_id: str
    symbol: str
    side: OrderSide
    quantity: int
    price: Decimal
    fees: Decimal
    # Charges carried from the entry side of the position this fill closes.
    # Kept separate from ``fees`` so a round trip can report both legs.
    entry_fees: Decimal = Decimal("0")
    # Statutory breakdown of ``fees`` (brokerage, STT, exchange, SEBI, stamp, GST).
    charge_breakdown: dict[str, Decimal] = Field(default_factory=dict)
    filled_at: datetime
