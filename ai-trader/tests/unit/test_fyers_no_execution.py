"""FYERS is market data only. PaperBroker is the only execution provider.

These tests encode the architectural invariant. They must keep passing.
"""

import inspect
from decimal import Decimal

import pytest

from ai_trader.application.fyers_session import build_session
from ai_trader.application.workflows.shadow_review import ShadowReviewWorkflow
from ai_trader.brokers.base import Broker
from ai_trader.brokers.paper import PaperBroker
from ai_trader.config import Settings
from ai_trader.domain.market import Instrument
from ai_trader.domain.orders import OrderRequest, OrderSide, OrderType
from ai_trader.execution.service import ExecutionService
from ai_trader.integrations.fyers import broker as fyers_broker_module
from ai_trader.integrations.fyers.broker import FyersBroker, FyersTradingDisabled


def _request() -> OrderRequest:
    return OrderRequest(
        client_order_id="cid-1",
        decision_id="d-1",
        correlation_id="c-1",
        symbol="INFY",
        exchange="NSE",
        segment="EQ",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
        requested_allocation=Decimal("15000"),
        actual_order_value=Decimal("15000"),
        conversion_reason="test",
    )


def _settings() -> Settings:
    return Settings(
        app_mode="LIVE_SHADOW",
        market_provider="fyers",
        shadow_initial_capital_inr=Decimal("100000"),
        trading_enabled=True,
        database_url="sqlite://",
        ai_base_url="http://192.168.1.25:8080/v1",
        ai_model="server-model-id",
        jev_enabled=True,
        jev_api_key="jev-secret",
    )


async def test_fyers_place_order_raises() -> None:
    with pytest.raises(FyersTradingDisabled):
        await FyersBroker().place_order(_request())


async def test_fyers_all_trading_methods_raise() -> None:
    broker = FyersBroker()
    calls = (
        broker.place_order(_request()),
        broker.modify_order("x"),
        broker.cancel_order("x"),
        broker.get_account(),
        broker.get_positions(),
        broker.get_orders(),
        broker.get_order_status("x"),
    )
    for call in calls:
        with pytest.raises(FyersTradingDisabled):
            await call


def test_fyers_broker_declares_it_is_not_execution_provider() -> None:
    assert FyersBroker.is_execution_provider is False


def test_shadow_workflow_binds_paper_broker_concretely() -> None:
    annotations = ShadowReviewWorkflow.__init__.__annotations__
    assert annotations["broker"] is PaperBroker


def test_shadow_session_only_constructs_paper_broker() -> None:
    source = inspect.getsource(
        __import__("ai_trader.application.fyers_session", fromlist=["build_session"])
    )
    assert "FyersBroker" not in source
    assert "PaperBroker(" in source


def test_fyers_session_broker_is_paper(clock) -> None:
    from ai_trader.integrations.fyers.auth import FyersAuth
    from ai_trader.integrations.fyers.client import FyersDataSocket
    from ai_trader.integrations.fyers.feed import FyersFeed
    from ai_trader.integrations.fyers.market_data import FyersMarketDataService

    auth = FyersAuth("app", "token")
    instrument = Instrument(instrument_key="3045", symbol="INFY", exchange="NSE")
    service = FyersMarketDataService({"sf|nse_cm|3045": instrument}, clock)
    socket = FyersDataSocket(auth, on_frame=lambda *_: None)
    feed = FyersFeed(service, socket, {"sf|nse_cm|3045": instrument}, ["sf|nse_cm|3045"], {})

    session = build_session(_settings(), feed, clock=clock)
    assert isinstance(session.broker, PaperBroker)
    assert isinstance(session.workflow.broker, PaperBroker)
    assert session.workflow.execution.broker is session.broker


def test_execution_service_in_session_is_paper_backed(clock) -> None:
    from ai_trader.integrations.fyers.auth import FyersAuth
    from ai_trader.integrations.fyers.client import FyersDataSocket
    from ai_trader.integrations.fyers.feed import FyersFeed
    from ai_trader.integrations.fyers.market_data import FyersMarketDataService

    instrument = Instrument(instrument_key="3045", symbol="INFY", exchange="NSE")
    service = FyersMarketDataService({"sf|nse_cm|3045": instrument}, clock)
    feed = FyersFeed(
        service,
        FyersDataSocket(FyersAuth("app", "token"), on_frame=lambda *_: None),
        {"sf|nse_cm|3045": instrument},
        ["sf|nse_cm|3045"],
        {},
    )
    session = build_session(_settings(), feed, clock=clock)
    assert isinstance(session.workflow.execution, ExecutionService)
    assert isinstance(session.workflow.execution.broker, PaperBroker)
    assert not isinstance(session.workflow.execution.broker, FyersBroker)


def test_fyers_package_exposes_no_order_endpoint() -> None:
    """No FYERS module may reference an order-placement path."""
    import ai_trader.integrations.fyers as package

    root = __import__("pathlib").Path(package.__file__).parent
    forbidden = ("/order", "/place-order", "/trade", "place_order(", "cancel_order(")
    for path in root.rglob("*.py"):
        if path.name == "broker.py":
            continue  # the explicit guard raises for every trading method
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{path.name} references {needle}"


def test_fyers_broker_module_only_raises() -> None:
    source = inspect.getsource(fyers_broker_module)
    assert "httpx" not in source
    assert "requests" not in source
    assert "/order" not in source
    assert "FyersTradingDisabled(MESSAGE)" in source


def test_paper_broker_is_the_execution_implementation() -> None:
    assert issubclass(PaperBroker, Broker)
    assert issubclass(FyersBroker, Broker)
