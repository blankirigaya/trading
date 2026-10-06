from datetime import datetime
from decimal import Decimal
from enum import StrEnum

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


class Order(BaseModel):
    model_config = ConfigDict(frozen=True)
    broker_order_id: str
    request: OrderRequest
    status: OrderStatus
    created_at: datetime


class Fill(BaseModel):
    model_config = ConfigDict(frozen=True)
    fill_id: str
    broker_order_id: str
    symbol: str
    side: OrderSide
    quantity: int
    price: Decimal
    fees: Decimal
    filled_at: datetime
