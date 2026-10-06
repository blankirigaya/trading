from abc import ABC, abstractmethod

from ai_trader.domain.orders import Order, OrderRequest
from ai_trader.domain.portfolio import PortfolioState


class Broker(ABC):
    @abstractmethod
    async def get_account(self) -> PortfolioState: ...

    @abstractmethod
    async def get_positions(self) -> PortfolioState: ...

    @abstractmethod
    async def get_orders(self) -> list[Order]: ...

    @abstractmethod
    async def place_order(self, request: OrderRequest) -> Order: ...

    @abstractmethod
    async def modify_order(self, broker_order_id: str) -> Order: ...

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> Order: ...

    @abstractmethod
    async def get_order_status(self, broker_order_id: str) -> Order: ...
