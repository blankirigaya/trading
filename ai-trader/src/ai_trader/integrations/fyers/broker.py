"""Explicit marker that FYERS is market data only.

FYERS is the market data provider in this project. It is never the execution provider.
This class exists solely so that the absence of order capability is an enforced,
testable invariant rather than an omission. Every trading method raises.
"""

from ai_trader.brokers.base import Broker
from ai_trader.domain.orders import Order, OrderRequest
from ai_trader.domain.portfolio import PortfolioState

MESSAGE = (
    "FYERS order execution is intentionally disabled. FYERS supplies market data only; "
    "LIVE_SHADOW executes exclusively through PaperBroker."
)


class FyersTradingDisabled(RuntimeError):
    """A FYERS trading operation was attempted. It must never succeed."""


class FyersBroker(Broker):
    """Market-data-only provider. All trading operations raise ``FyersTradingDisabled``."""

    is_execution_provider = False

    async def get_account(self) -> PortfolioState:
        raise FyersTradingDisabled(MESSAGE)

    async def get_positions(self) -> PortfolioState:
        raise FyersTradingDisabled(MESSAGE)

    async def get_orders(self) -> list[Order]:
        raise FyersTradingDisabled(MESSAGE)

    async def place_order(self, request: OrderRequest) -> Order:
        raise FyersTradingDisabled(MESSAGE)

    async def modify_order(self, broker_order_id: str) -> Order:
        raise FyersTradingDisabled(MESSAGE)

    async def cancel_order(self, broker_order_id: str) -> Order:
        raise FyersTradingDisabled(MESSAGE)

    async def get_order_status(self, broker_order_id: str) -> Order:
        raise FyersTradingDisabled(MESSAGE)
