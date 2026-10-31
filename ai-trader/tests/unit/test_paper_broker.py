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
    assert order.broker_order_id == "PAPER-0-000001"
    assert duplicate == order
    assert account.cash == Decimal("890")
    assert account.positions[0].average_entry_price == Decimal("11")


def _buy(symbol: str, quantity: int) -> OrderRequest:
    return OrderRequest(
        client_order_id=f"buy-{symbol}-{quantity}",
        decision_id="d",
        correlation_id="c",
        symbol=symbol,
        exchange="NSE",
        segment="EQ",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=quantity,
        requested_allocation=Decimal("1000"),
        actual_order_value=Decimal("1000"),
        conversion_reason="test",
    )


def _sell(symbol: str, quantity: int) -> OrderRequest:
    return OrderRequest(
        client_order_id=f"sell-{symbol}-{quantity}",
        decision_id="d",
        correlation_id="c",
        symbol=symbol,
        exchange="NSE",
        segment="EQ",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=quantity,
        requested_allocation=Decimal("1000"),
        actual_order_value=Decimal("1000"),
        conversion_reason="test",
    )


async def test_margin_blocks_fraction_of_notional_but_pnl_stays_full(clock) -> None:
    broker = PaperBroker(
        Decimal("1000"), clock, {"T": Decimal("10")}, margin_multiple=Decimal("5")
    )
    await broker.place_order(_buy("T", 50))  # 500 notional, 100 blocked
    assert broker.cash == Decimal("900")
    broker.set_price("T", Decimal("12"))
    account = await broker.get_account()
    assert account.unrealized_pnl == Decimal("100")  # full-notional P&L
    await broker.place_order(_sell("T", 50))
    assert broker.cash == Decimal("1100")  # margin back + 100 profit
    # Full close removes the position row from the account view, but the
    # realized P&L stays on the closed position record.
    assert broker.positions["T"].quantity == 0
    assert broker.positions["T"].realized_pnl == Decimal("100")
    # A restart reloads only open positions, so the round trip's P&L must also
    # live on the book itself.
    assert broker.realized_pnl == Decimal("100")
    assert (await broker.get_account()).realized_pnl == Decimal("100")


async def test_margin_multiple_one_preserves_delivery_blocking(clock) -> None:
    broker = PaperBroker(Decimal("1000"), clock, {"T": Decimal("10")})
    await broker.place_order(_buy("T", 50))
    assert broker.cash == Decimal("500")


async def test_margin_rejects_non_positive_multiple(clock) -> None:
    import pytest

    with pytest.raises(ValueError, match="margin_multiple must be positive"):
        PaperBroker(Decimal("1000"), clock, margin_multiple=Decimal("0"))
