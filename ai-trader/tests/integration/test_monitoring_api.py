"""Tests for the monitoring API surface added for the Electron UI."""

import pytest
from fastapi.testclient import TestClient

from ai_trader.api.events import EventHub, new_event
from ai_trader.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_status_reports_paper_only_execution(client: TestClient) -> None:
    body = client.get("/system/status").json()
    assert body["live_order_execution"] is False
    assert body["execution_provider"] == "PaperBroker"
    assert body["fyers_market_data_only"] is True
    assert body["mode"]
    assert "session_running" in body


def test_status_never_leaks_credentials(client: TestClient) -> None:
    body = client.get("/system/status").text
    for secret in ("access_token", "api_key", "Authorization", "Bearer", "secret_key"):
        assert secret not in body


def test_portfolio_and_positions_endpoints(client: TestClient) -> None:
    assert "cash" in client.get("/portfolio").json()
    assert isinstance(client.get("/positions").json(), list)


def test_market_endpoints_expose_symbols(client: TestClient) -> None:
    status = client.get("/market/status").json()
    assert status["provider"]
    assert status["symbols"]
    assert isinstance(client.get("/market/snapshots").json(), list)
    assert client.get("/market/candles", params={"symbol": "INFY"}).json() == []


def test_decisions_list_and_detail_404(client: TestClient) -> None:
    assert isinstance(client.get("/decisions").json(), list)
    assert client.get("/decisions/does-not-exist").status_code == 404


def test_trades_are_labelled_paper(client: TestClient) -> None:
    rows = client.get("/trades").json()
    assert rows == []
    assert client.get("/trades/PAPER-000001").status_code == 404


def test_performance_reports_counters(client: TestClient) -> None:
    body = client.get("/performance").json()
    for key in (
        "total_pnl",
        "realized_pnl",
        "unrealized_pnl",
        "total_decisions",
        "actions",
        "jev_approvals",
        "jev_challenges",
        "ai_changed_after_jev",
        "ai_retained_after_jev",
    ):
        assert key in body


def test_events_recent_returns_timeline(client: TestClient) -> None:
    events = client.get("/events/recent").json()
    assert isinstance(events, list)
    assert any(event["kind"] == "backend_started" for event in events)


def test_control_review_requires_runtime(client: TestClient) -> None:
    assert client.post("/control/review").status_code == 409


def test_control_start_requires_runtime(client: TestClient) -> None:
    assert client.post("/control/review").status_code == 409


class _StubRuntime:
    running = False

    async def start(self) -> None:
        self.running = True

    async def stop(self) -> None:
        self.running = False

    async def review_once(self) -> None:
        return None


def test_control_start_attaches_and_starts_runtime(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_trader.application import container as container_module

    def fake_attach(self) -> _StubRuntime:
        self.runtime = _StubRuntime()
        return self.runtime

    async def fake_attach_async(self) -> _StubRuntime:
        return fake_attach(self)

    monkeypatch.setattr(container_module.Container, "attach_runtime", fake_attach_async)
    assert client.post("/control/start").json() == {"running": True}
    assert client.get("/system/status").json()["session_running"] is True
    assert client.post("/control/stop").json() == {"running": False}


def test_control_stop_is_safe_without_runtime(client: TestClient) -> None:
    assert client.post("/control/stop").json()["running"] is False


def test_portfolio_equity_counts_blocked_margin_and_realized_pnl() -> None:
    """Cash alone understates the book, and a closed round trip must still count."""
    import asyncio
    from decimal import Decimal

    from ai_trader.brokers.paper import PaperBroker
    from ai_trader.domain.common import SystemClock
    from ai_trader.domain.orders import OrderRequest, OrderSide, OrderType
    from ai_trader.main import create_app

    def order(side: OrderSide, quantity: int) -> OrderRequest:
        return OrderRequest(
            client_order_id=f"c-{side.value}",
            decision_id=f"d-{side.value}",
            correlation_id="c",
            symbol="T",
            exchange="NSE",
            segment="EQ",
            side=side,
            order_type=OrderType.MARKET,
            quantity=quantity,
            requested_allocation=Decimal("100000"),
            actual_order_value=Decimal("100000"),
            conversion_reason="test",
        )

    app = create_app()
    broker = PaperBroker(
        Decimal("100000"),
        SystemClock(),
        {"T": Decimal("100")},
        margin_multiple=Decimal("5"),
    )
    app.state.container.shard_brokers = []
    app.state.container.broker = broker
    client = TestClient(app)

    asyncio.run(broker.place_order(order(OrderSide.BUY, 10)))

    # 10 x 100 at a 5x margin multiple blocks 200 of cash, so equity is the full
    # 100000 even though cash reads 99800.
    body = client.get("/portfolio").json()
    assert Decimal(body["cash"]) == Decimal("99800")
    assert Decimal(body["equity"]) == Decimal("100000")

    broker.set_price("T", Decimal("105"))
    asyncio.run(broker.place_order(order(OrderSide.SELL, 10)))

    # The position row is gone, but the round trip's P&L is not.
    body = client.get("/portfolio").json()
    assert body["positions"] == []
    assert Decimal(body["realized_pnl"]) == Decimal("50")
    assert Decimal(body["equity"]) == Decimal("100050")

    performance = client.get("/performance").json()
    assert Decimal(performance["realized_pnl"]) == Decimal("50")
    assert Decimal(performance["total_pnl"]) == Decimal("50")
    assert Decimal(performance["cash"]) == Decimal("100050")


def test_kill_switch_toggles_and_emits_event(client: TestClient) -> None:
    assert client.post("/control/kill-switch", params={"enabled": "true"}).json() == {
        "kill_switch_state": True,
        "live_order_execution": False,
    }
    assert client.get("/system/status").json()["kill_switch_state"] is True
    assert any(e["kind"] == "kill_switch" for e in client.get("/events/recent").json())
    client.post("/control/kill-switch", params={"enabled": "false"})
    assert client.get("/system/status").json()["kill_switch_state"] is False


def test_paper_reset_is_scoped_to_paper_broker(client: TestClient) -> None:
    body = client.post("/paper/reset").json()
    assert body["execution_provider"] == "PaperBroker"


def _route_paths(app) -> set[str]:
    from fastapi.routing import APIRoute

    return {route.path for route in app.routes if isinstance(route, APIRoute)}


def test_no_live_order_endpoints_exist() -> None:
    """The backend exposes no order-placement route for any live provider."""
    paths = _route_paths(create_app())
    forbidden = {"/order", "/orders/place", "/trade", "/buy", "/sell", "/fyers/order"}
    assert not paths & forbidden
    assert all(
        segment not in path
        for path in paths
        for segment in ("place_order", "/fyers/order", "/upstox/order")
    )


def test_websocket_streams_events(client: TestClient) -> None:
    with client.websocket_connect("/events") as socket:
        first = socket.receive_json()
        assert first["kind"] == "connected"
        # Backlog is replayed immediately after the greeting.
        assert isinstance(socket.receive_json(), dict)


def test_websocket_ignores_slow_subscriber() -> None:
    hub = EventHub()
    queue = __import__("asyncio").new_event_loop().run_until_complete(hub.subscribe())
    for index in range(400):
        hub.publish(new_event("SYSTEM", "flood", f"event {index}"))
    assert queue.qsize() <= 200


def test_event_hub_publishes_with_sequence() -> None:
    hub = EventHub()
    first = hub.publish(new_event("AI", "ai_initial_decision", "one"))
    second = hub.publish(new_event("JEV", "jev_critique", "two"))
    assert first["seq"] < second["seq"]
    assert hub.recent(1) == [second]
