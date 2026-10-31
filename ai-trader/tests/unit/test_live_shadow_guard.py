from decimal import Decimal

from ai_trader.ai.jev import DisabledJevCritic
from ai_trader.application.container import Container
from ai_trader.brokers.paper import PaperBroker
from ai_trader.brokers.upstox import LiveBrokerNotConfigured, UpstoxBroker
from ai_trader.config import Settings
from ai_trader.domain.orders import OrderRequest, OrderSide, OrderType


def test_live_shadow_container_uses_only_paper_broker() -> None:
    container = Container(Settings(app_mode="LIVE_SHADOW", jev_enabled=False))
    assert isinstance(container.broker, PaperBroker)
    assert container.shadow_workflow is not None
    assert isinstance(container.shadow_workflow.broker, PaperBroker)
    assert isinstance(container.shadow_workflow.critic, DisabledJevCritic)


async def test_upstox_order_operation_is_forbidden() -> None:
    broker = UpstoxBroker()
    request = OrderRequest(
        client_order_id="x",
        decision_id="d",
        correlation_id="c",
        symbol="INFY",
        exchange="NSE",
        segment="EQ",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=1,
        requested_allocation=Decimal("1"),
        actual_order_value=Decimal("1"),
        conversion_reason="test",
    )
    import pytest

    with pytest.raises(LiveBrokerNotConfigured):
        await broker.place_order(request)
