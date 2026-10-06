from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class Position(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str
    quantity: int
    average_entry_price: Decimal
    realized_pnl: Decimal = Decimal("0")


class PortfolioState(BaseModel):
    model_config = ConfigDict(frozen=True)
    cash: Decimal
    reserved_cash: Decimal = Decimal("0")
    positions: list[Position] = Field(default_factory=list)
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
