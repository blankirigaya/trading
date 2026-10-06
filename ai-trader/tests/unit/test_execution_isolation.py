"""Phase 2A hard guarantee: LIVE_SHADOW can never reach a live broker order method."""

from decimal import Decimal

import pytest

from ai_trader.application.container import Container
from ai_trader.brokers.base import Broker
from ai_trader.brokers.paper import PaperBroker
from ai_trader.brokers.upstox import LiveBrokerNotConfigured, UpstoxBroker
from ai_trader.config import Settings
from ai_trader.domain.orders import OrderRequest, OrderSide, OrderType
from ai_trader.execution.service import ExecutionService


def _request(client_order_id: str = "cid-1") -> OrderRequest:
    return OrderRequest(
        client_order_id=client_order_id,
        decision_id="d-1",
        correlation_id="c-1",
        symbol="INFY",
        exchange="NSE",
        segment="EQ",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=1,
        requested_allocation=Decimal("1500"),
        actual_order_value=Decimal("1500"),
        conversion_reason="test",
    )


def test_live_shadow_container_broker_is_paper_only() -> None:
    container = Container(Settings(app_mode="LIVE_SHADOW", jev_enabled=False))
    assert isinstance(container.broker, PaperBroker)
    assert container.execution.broker is container.broker


def test_live_shadow_never_injects_upstox_broker() -> None:
    container = Container(Settings(app_mode="LIVE_SHADOW", jev_enabled=False))
    injected = [container.broker, container.execution.broker]
    assert not any(isinstance(broker, UpstoxBroker) for broker in injected)


def test_shadow_workflow_requires_paper_broker_concretely() -> None:
    from ai_trader.application.workflows.shadow_review import ShadowReviewWorkflow

    annotations = ShadowReviewWorkflow.__init__.__annotations__
    assert annotations["broker"] is PaperBroker


def test_execution_service_rejects_non_paper_broker_in_live_shadow() -> None:
    container = Container(Settings(app_mode="LIVE_SHADOW", jev_enabled=False))
    service = ExecutionService(container.broker)
    assert isinstance(service.broker, PaperBroker)


async def test_upstox_place_order_cannot_execute() -> None:
    broker = UpstoxBroker()
    with pytest.raises(LiveBrokerNotConfigured):
        await broker.place_order(_request())


async def test_upstox_all_order_methods_are_forbidden() -> None:
    broker = UpstoxBroker()
    for call in (
        broker.place_order(_request()),
        broker.modify_order("x"),
        broker.cancel_order("x"),
    ):
        with pytest.raises(LiveBrokerNotConfigured):
            await call


async def test_live_order_execution_flag_is_not_enforced_anywhere() -> None:
    """LIVE_ORDER_EXECUTION has no code path that can enable live order submission."""
    import inspect

    from ai_trader.execution import service as execution_module

    source = inspect.getsource(execution_module)
    assert "Upstox" not in source
    assert "live_order_execution" not in source


def test_broker_interface_has_no_upstox_specific_types() -> None:
    import inspect

    from ai_trader.brokers import base

    source = inspect.getsource(base)
    assert "upstox" not in source.lower()
    assert "Upstox" not in source


def test_paper_broker_is_a_broker_implementation() -> None:
    assert issubclass(PaperBroker, Broker)
