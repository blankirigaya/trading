from decimal import Decimal

from ai_trader.brokers.paper import PaperBroker
from ai_trader.domain.orders import OrderRequest, OrderSide, OrderType


async def test_paper_broker_is_deterministic_and_tracks_position(clock) -> None:
    broker = PaperBroker(Decimal("1000"), clock, {"T": Decimal("10")}, slippage=Decimal("1"))
    request = OrderRequest(
        client_order_id="one",
        decision_id="d",
        correlation_id="c",
        symbol="T",
        exchange="NSE",
        segment="EQ",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
        requested_allocation=Decimal("100"),
        actual_order_value=Decimal("100"),
        conversion_reason="test",
    )
    order = await broker.place_order(request)
    duplicate = await broker.place_order(request)
    account = await broker.get_account()
    assert order.broker_order_id == "PAPER-000001"
    assert duplicate == order
    assert account.cash == Decimal("890")
    assert account.positions[0].average_entry_price == Decimal("11")
