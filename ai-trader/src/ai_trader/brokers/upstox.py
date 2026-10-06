from ai_trader.brokers.base import Broker
from ai_trader.domain.orders import Order, OrderRequest
from ai_trader.domain.portfolio import PortfolioState


class LiveBrokerNotConfigured(RuntimeError):
    pass


class UpstoxBroker(Broker):
    async def get_account(self) -> PortfolioState:
        raise LiveBrokerNotConfigured("Upstox integration is not part of Phase 1")

    async def get_positions(self) -> PortfolioState:
        raise LiveBrokerNotConfigured("Upstox integration is not part of Phase 1")

    async def get_orders(self) -> list[Order]:
        raise LiveBrokerNotConfigured("Upstox integration is not part of Phase 1")

    async def place_order(self, request: OrderRequest) -> Order:
        raise LiveBrokerNotConfigured("Upstox integration is not part of Phase 1")

    async def modify_order(self, broker_order_id: str) -> Order:
        raise LiveBrokerNotConfigured("Upstox integration is not part of Phase 1")

    async def cancel_order(self, broker_order_id: str) -> Order:
        raise LiveBrokerNotConfigured("Upstox integration is not part of Phase 1")

    async def get_order_status(self, broker_order_id: str) -> Order:
        raise LiveBrokerNotConfigured("Upstox integration is not part of Phase 1")
