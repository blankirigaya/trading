from decimal import Decimal

from ai_trader.domain.orders import OrderRequest, OrderSide, OrderType
from ai_trader.domain.portfolio import PortfolioState
from ai_trader.safety.envelope import KillSwitch, SafetyEnvelope


def request() -> OrderRequest:
    return OrderRequest(
        client_order_id="c",
        decision_id="d",
        correlation_id="x",
        symbol="T",
        exchange="NSE",
        segment="EQ",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
        requested_allocation=Decimal("1000"),
        actual_order_value=Decimal("1000"),
        conversion_reason="test",
    )


def test_safety_denies_when_trading_disabled(enabled_settings, clock) -> None:
    disabled = enabled_settings.model_copy(update={"trading_enabled": False})
    assert (
        SafetyEnvelope(disabled, KillSwitch(), clock)
        .check(request(), PortfolioState(cash=Decimal("10000")))
        .reason
        == "Trading disabled"
    )


def test_safety_passes_mechanical_order(enabled_settings, clock) -> None:
    assert (
        SafetyEnvelope(enabled_settings, KillSwitch(), clock)
        .check(request(), PortfolioState(cash=Decimal("10000")))
        .allowed
    )
