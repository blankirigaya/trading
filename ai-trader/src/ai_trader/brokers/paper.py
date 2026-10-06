from decimal import Decimal

from ai_trader.brokers.base import Broker
from ai_trader.domain.common import Clock
from ai_trader.domain.orders import Fill, Order, OrderRequest, OrderSide, OrderStatus, OrderType
from ai_trader.domain.portfolio import PortfolioState, Position


class PaperBroker(Broker):
    """Deterministic, immediate-fill simulator; it is not a realistic backtester."""

    def __init__(
        self,
        initial_cash: Decimal,
        clock: Clock,
        prices: dict[str, Decimal] | None = None,
        slippage: Decimal = Decimal("0"),
        fee_per_order: Decimal = Decimal("0"),
    ) -> None:
        self.initial_cash = initial_cash
        self.clock = clock
        self.prices = prices or {}
        self.slippage = slippage
        self.fee_per_order = fee_per_order
        self.cash = initial_cash
        self.positions: dict[str, Position] = {}
        self.orders: dict[str, Order] = {}
        self.fills: list[Fill] = []
        self._order_sequence = 0

    def set_price(self, symbol: str, price: Decimal) -> None:
        """Set the reference price used for fills and mark-to-market."""
        self.prices[symbol] = price

    def mark_to_market(self, prices: dict[str, Decimal]) -> None:
        """Update paper prices from real market data and return the new valuation."""
        self.prices.update(prices)

    def unrealized_pnl(self) -> Decimal:
        return sum(
            (
                (
                    self.prices.get(position.symbol, position.average_entry_price)
                    - position.average_entry_price
                )
                * position.quantity
                for position in self.positions.values()
                if position.quantity
            ),
            Decimal("0"),
        )

    def position_age_seconds(self, symbol: str) -> int | None:
        """Seconds since the most recent fill for a symbol, used for holding context."""
        filled = [fill.filled_at for fill in self.fills if fill.symbol == symbol]
        if not filled:
            return None
        return int((self.clock.now() - max(filled)).total_seconds())

    async def get_account(self) -> PortfolioState:
        return self._state()

    async def get_positions(self) -> PortfolioState:
        return self._state()

    async def get_orders(self) -> list[Order]:
        return list(self.orders.values())

    async def place_order(self, request: OrderRequest) -> Order:
        existing = next(
            (
                order
                for order in self.orders.values()
                if order.request.client_order_id == request.client_order_id
            ),
            None,
        )
        if existing is not None:
            return existing
        price = self._fill_price(request)
        value = price * request.quantity
        if request.side == OrderSide.BUY and value + self.fee_per_order > self.cash:
            raise ValueError("insufficient paper cash")
        self._order_sequence += 1
        broker_order_id = f"PAPER-{self._order_sequence:06d}"
        order = Order(
            broker_order_id=broker_order_id,
            request=request,
            status=OrderStatus.FILLED,
            created_at=self.clock.now(),
        )
        self.orders[broker_order_id] = order
        self._apply_fill(order, price)
        return order

    async def modify_order(self, broker_order_id: str) -> Order:
        raise ValueError(f"cannot modify immediately filled paper order {broker_order_id}")

    async def cancel_order(self, broker_order_id: str) -> Order:
        raise ValueError(f"cannot cancel immediately filled paper order {broker_order_id}")

    async def get_order_status(self, broker_order_id: str) -> Order:
        return self.orders[broker_order_id]

    def reset(self) -> None:
        self.cash = self.initial_cash
        self.positions.clear()
        self.orders.clear()
        self.fills.clear()
        self._order_sequence = 0

    def _fill_price(self, request: OrderRequest) -> Decimal:
        market_price = self.prices[request.symbol]
        if request.order_type == OrderType.LIMIT:
            if request.limit_price is None:
                raise ValueError("limit order requires limit price")
            if request.side == OrderSide.BUY and market_price > request.limit_price:
                raise ValueError("paper limit order is not marketable")
            if request.side == OrderSide.SELL and market_price < request.limit_price:
                raise ValueError("paper limit order is not marketable")
        return (
            market_price + self.slippage
            if request.side == OrderSide.BUY
            else market_price - self.slippage
        )

    def _apply_fill(self, order: Order, price: Decimal) -> None:
        request = order.request
        value = price * request.quantity
        old = self.positions.get(
            request.symbol,
            Position(symbol=request.symbol, quantity=0, average_entry_price=Decimal("0")),
        )
        realized = old.realized_pnl
        if request.side == OrderSide.BUY:
            new_quantity = old.quantity + request.quantity
            average = ((old.average_entry_price * old.quantity) + value) / new_quantity
            self.cash -= value + self.fee_per_order
        else:
            if request.quantity > old.quantity:
                raise ValueError("paper broker does not support short selling")
            new_quantity = old.quantity - request.quantity
            average = old.average_entry_price
            realized += (price - old.average_entry_price) * request.quantity - self.fee_per_order
            self.cash += value - self.fee_per_order
        self.positions[request.symbol] = Position(
            symbol=request.symbol,
            quantity=new_quantity,
            average_entry_price=average,
            realized_pnl=realized,
        )
        self.fills.append(
            Fill(
                fill_id=f"FILL-{order.broker_order_id}",
                broker_order_id=order.broker_order_id,
                symbol=request.symbol,
                side=request.side,
                quantity=request.quantity,
                price=price,
                fees=self.fee_per_order,
                filled_at=self.clock.now(),
            )
        )

    def _state(self) -> PortfolioState:
        positions = [position for position in self.positions.values() if position.quantity]
        return PortfolioState(
            cash=self.cash,
            positions=positions,
            realized_pnl=sum((position.realized_pnl for position in positions), Decimal("0")),
            unrealized_pnl=sum(
                (
                    (
                        self.prices.get(position.symbol, position.average_entry_price)
                        - position.average_entry_price
                    )
                    * position.quantity
                    for position in positions
                ),
                Decimal("0"),
            ),
        )
