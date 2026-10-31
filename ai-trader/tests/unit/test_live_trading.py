"""Disabled live-execution architecture.

These tests use injected fake transports and fake brokers. They never contact FYERS,
Upstox, or any live endpoint. They prove that live trading stays off by default, that
disabled adapters cannot use the network, and that the idempotent order lifecycle and
live risk gates behave safely when explicitly enabled in a test.
"""

import inspect
import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from ai_trader.brokers.live import (
    LiveBrokerResponseError,
    LiveTradingDisabled,
)
from ai_trader.brokers.live_factory import build_execution_broker
from ai_trader.brokers.live_fyers import LiveFyersBroker
from ai_trader.brokers.live_upstox import LiveUpstoxBroker
from ai_trader.brokers.paper import PaperBroker
from ai_trader.config import Settings
from ai_trader.domain.decisions import PortfolioAction, TradeProposal
from ai_trader.domain.market import MarketTick
from ai_trader.domain.orders import LiveOrderUpdate, OrderRequest, OrderSide, OrderStatus, OrderType
from ai_trader.domain.portfolio import PortfolioState, Position
from ai_trader.execution.live import LiveOrderCoordinator
from ai_trader.execution.service import ExecutionService
from ai_trader.integrations.fyers.auth import FyersAuth
from ai_trader.integrations.upstox.auth import UpstoxAuth
from ai_trader.safety.envelope import KillSwitch, SafetyEnvelope
from ai_trader.safety.live import LiveSafetyEnvelope

NOW = datetime(2026, 10, 16, 4, 0, tzinfo=UTC)


class ScriptedTransport:
    def __init__(self, outcomes):
        self.requests = []
        self._outcomes = list(outcomes)

    def __call__(self, request):
        self.requests.append(request)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _client(scripted):
    return httpx.AsyncClient(transport=httpx.MockTransport(scripted))


def _settings(**overrides):
    values = {
        "execution_broker": "fyers",
        "live_trading_approved": True,
        "live_order_execution": True,
        "trading_enabled": True,
        "fyers_client_id": "APPID",
        "fyers_access_token": "TOKEN",
        "live_allowed_symbols": ("NSE:INFY-EQ",),
        "live_max_order_value_inr": Decimal("2000"),
        "live_max_position_value_inr": Decimal("5000"),
        "live_max_daily_loss_inr": Decimal("500"),
        "live_trading_start_ist": "00:00",
        "live_trading_end_ist": "23:59",
    }
    values.update(overrides)
    return Settings(**values)


def _request(client_order_id: str = "decision-1:proposal-1") -> OrderRequest:
    return OrderRequest(
        client_order_id=client_order_id,
        decision_id="decision-1",
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


def _proposal(amount="1500"):
    return TradeProposal(
        proposal_id="proposal-1",
        symbol="INFY",
        action=PortfolioAction.OPEN_POSITION,
        side=OrderSide.BUY,
        capital_allocation_inr=Decimal(amount),
        confidence=0.5,
        thesis="test",
        evidence=[],
        conditions_that_change_thesis=[],
    )


def _tick():
    return MarketTick(
        symbol="INFY",
        exchange="NSE",
        segment="EQ",
        last_price=Decimal("1500"),
        exchange_timestamp=NOW,
        received_timestamp=NOW,
        processed_timestamp=NOW,
    )


def _portfolio():
    return PortfolioState(cash=Decimal("100000"))


def _safety(settings, clock):
    return LiveSafetyEnvelope(settings, SafetyEnvelope(settings, KillSwitch(), clock), clock)


def _fyers_broker(scripted, clock, enabled=True):
    return LiveFyersBroker(
        FyersAuth("APPID", "TOKEN"),
        clock=clock,
        client=_client(scripted),
        enabled=enabled,
        timeout_seconds=5,
    )


def _upstox_broker(scripted, clock, enabled=True):
    return LiveUpstoxBroker(
        UpstoxAuth("https://api.upstox.com", "TOKEN"),
        lambda symbol, exchange: "NSE_EQ|INETEST1234",
        clock=clock,
        client=_client(scripted),
        enabled=enabled,
        timeout_seconds=5,
    )


def _fyers_place_response(order_id="123"):
    return httpx.Response(200, json={"s": "ok", "code": 1101, "id": order_id})


def _fyers_status_response(status, order_id="123", **fields):
    entry = {"id": order_id, "ord_status": status}
    entry.update(fields)
    return httpx.Response(200, json={"s": "ok", "code": 200, "orderBook": [entry]})


def _upstox_place_response(order_id="U123"):
    return httpx.Response(200, json={"status": "success", "data": {"order_id": order_id}})


def _upstox_status_response(status, **fields):
    entry = {
        "order_id": "U123",
        "status": status,
        "filled_quantity": 0,
        "average_price": 0,
    }
    entry.update(fields)
    return httpx.Response(200, json={"status": "success", "data": entry})


def test_live_trading_is_disabled_by_default():
    settings = Settings()

    assert settings.execution_broker == "paper"
    assert settings.live_trading_approved is False
    assert settings.live_order_execution is False
    assert settings.trading_enabled is False
    assert isinstance(build_execution_broker(settings), PaperBroker)


def test_live_factory_requires_every_approval_and_matching_broker():
    settings = _settings(live_trading_approved=False)

    with pytest.raises(LiveTradingDisabled):
        build_execution_broker(settings)


def test_live_factory_supports_both_configured_brokers(clock):
    fyers_scripted = ScriptedTransport([])
    upstox_scripted = ScriptedTransport([])
    fyers_settings = _settings(
        execution_broker="fyers", fyers_client_id="APPID", fyers_access_token="TOKEN"
    )
    upstox_settings = _settings(
        execution_broker="upstox", upstox_access_token="TOKEN", fyers_access_token="TOKEN"
    )

    fyers = build_execution_broker(fyers_settings, clock, fyers_client=_client(fyers_scripted))
    upstox = build_execution_broker(
        upstox_settings,
        clock,
        upstox_client=_client(upstox_scripted),
        upstox_instruments=lambda symbol, exchange: "NSE_EQ|INETEST1234",
    )

    assert isinstance(fyers, LiveFyersBroker)
    assert isinstance(upstox, LiveUpstoxBroker)


async def test_disabled_live_brokers_make_no_network_calls(clock):
    fyers_scripted = ScriptedTransport([AssertionError("network used")])
    upstox_scripted = ScriptedTransport([AssertionError("network used")])
    fyers = _fyers_broker(fyers_scripted, clock, enabled=False)
    upstox = _upstox_broker(upstox_scripted, clock, enabled=False)
    request = _request()

    for broker in (fyers, upstox):
        with pytest.raises(LiveTradingDisabled):
            await broker.place_order(request)
        with pytest.raises(LiveTradingDisabled):
            await broker.get_order_status("x")
        with pytest.raises(LiveTradingDisabled):
            await broker.reconcile_order("x")
        with pytest.raises(LiveTradingDisabled):
            await broker.reconcile_client_order(request.client_order_id)
        with pytest.raises(LiveTradingDisabled):
            await broker.get_orders()
        with pytest.raises(LiveTradingDisabled):
            await broker.get_account()
        with pytest.raises(LiveTradingDisabled):
            await broker.get_positions()

    assert fyers_scripted.requests == []
    assert upstox_scripted.requests == []


async def test_fyers_place_uses_documented_sync_shape_and_idempotency_key(clock):
    scripted = ScriptedTransport([_fyers_place_response(), _fyers_status_response(6)])
    broker = _fyers_broker(scripted, clock)
    request = _request()

    order = await broker.place_order(request)
    duplicate = await broker.place_order(request)

    assert order.broker_order_id == "123"
    assert duplicate.broker_order_id == "123"
    assert len([item for item in scripted.requests if item.method == "POST"]) == 1
    payload = json.loads(scripted.requests[0].content.decode())
    assert scripted.requests[0].headers["Authorization"] == "APPID:TOKEN"
    assert payload == {
        "symbol": "INFY",
        "qty": 1,
        "type": 2,
        "side": 1,
        "productType": "INTRADAY",
        "limitPrice": 0,
        "stopPrice": 0,
        "validity": "DAY",
        "disclosedQty": 0,
        "offlineOrder": False,
        "stopLoss": 0,
        "takeProfit": 0,
        "orderTag": "AIdecision1proposal1",
        "isSliceOrder": False,
    }


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (2, OrderStatus.FILLED),
        (4, OrderStatus.ACCEPTED),
        (5, OrderStatus.REJECTED),
        (1, OrderStatus.CANCELLED),
        (6, OrderStatus.ACCEPTED),
        (7, OrderStatus.CANCELLED),
        (999, OrderStatus.ACCEPTED),
    ],
)
async def test_fyers_status_codes_map_conservatively(clock, code, expected):
    scripted = ScriptedTransport(
        [_fyers_place_response(), _fyers_status_response(code, tradedQty=1, tradePrice=1500)]
    )
    broker = _fyers_broker(scripted, clock)
    request = _proposal()

    await broker.place_order(request)
    order = await broker.reconcile_order("123")

    assert order.status is expected


async def test_fyers_modify_cancel_and_timeout_recovery(clock):
    scripted = ScriptedTransport(
        [
            _fyers_place_response("A1"),
            httpx.ConnectTimeout("timeout"),
            httpx.Response(
                200,
                json={"s": "ok", "code": 200, "orderBook": [{"id": "A1", "ord_status": 2}]},
            ),
            _fyers_status_response(2, order_id="A1", tradedQty=1, tradePrice=1500),
        ]
    )
    broker = _fyers_broker(scripted, clock)
    request = _request()

    await broker.place_order(request)
    recovered = await broker.reconcile_client_order(request.client_order_id)

    assert recovered is not None
    assert recovered.status is OrderStatus.FILLED
    assert recovered.filled_quantity == 1
    assert len([item for item in scripted.requests if item.method == "POST"]) == 1


async def test_fyers_account_and_positions_use_documented_shapes(clock):
    scripted = ScriptedTransport(
        [
            httpx.Response(
                200,
                json={
                    "s": "ok",
                    "code": 200,
                    "fund_limit": [
                        {"id": 10, "title": "Available Balance", "equityAmount": 1234.5}
                    ],
                },
            ),
            httpx.Response(
                200,
                json={
                    "s": "ok",
                    "code": 200,
                    "netPositions": [
                        {
                            "symbol": "NSE:INFY-EQ",
                            "netQty": 2,
                            "netAvg": 1500,
                            "realized_profit": 10,
                        },
                        {"symbol": "NSE:INFY-EQ", "netQty": 0, "netAvg": 0},
                    ],
                },
            ),
        ]
    )
    broker = _fyers_broker(scripted, clock)

    account = await broker.get_account()

    assert account.cash == Decimal("1234.5")
    assert [(position.symbol, position.quantity) for position in account.positions] == [("INFY", 2)]


async def test_upstox_place_status_and_account_shapes(clock):
    scripted = ScriptedTransport(
        [
            _upstox_place_response(),
            _upstox_status_response("complete", filled_quantity=1, average_price=1500),
            httpx.Response(
                200,
                json={"status": "success", "data": {"equity": {"available_margin": 4321.25}}},
            ),
            httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": [
                        {
                            "trading_symbol": "INFY",
                            "quantity": 3,
                            "average_price": 1500,
                            "realised": 5,
                        },
                        {"trading_symbol": "INFY", "quantity": 0, "average_price": 0},
                    ],
                },
            ),
        ]
    )
    broker = _upstox_broker(scripted, clock)
    request = _proposal()

    placed = await broker.place_order(request)
    completed = await broker.reconcile_order(placed.broker_order_id)
    account = await broker.get_account()

    assert placed.broker_order_id == "U123"
    assert completed.status is OrderStatus.FILLED
    assert completed.filled_quantity == 1
    assert account.cash == Decimal("4321.25")
    assert [(position.symbol, position.quantity) for position in account.positions] == [("INFY", 3)]
    payload = json.loads(scripted.requests[0].content.decode())
    assert scripted.requests[0].headers["Authorization"] == "Bearer TOKEN"
    assert payload["product"] == "I"
    assert payload["market_protection"] == -1
    assert payload["tag"] == "AI-decision-1proposal-1"


async def test_live_safety_requires_approvals_broker_window_and_limits(clock):
    settings = _settings()
    safety = _safety(settings, clock)
    request = _request()

    assert safety.check(request, _portfolio(), broker_name="fyers", now=NOW).allowed is True
    assert (
        safety.check(request, _portfolio(), broker_name="upstox", now=NOW).reason
        == "Order broker does not match EXECUTION_BROKER"
    )
    foreign = request.model_copy(update={"symbol": "TCS"})
    assert (
        safety.check(foreign, _portfolio(), broker_name="fyers", now=NOW).reason
        == "Symbol is not in LIVE_ALLOWED_SYMBOLS"
    )


async def test_live_safety_enforces_trading_window_and_caps(clock):
    settings = _settings(live_trading_start_ist="09:20", live_trading_end_ist="15:15")
    safety = _safety(settings, clock)
    request = _request()
    outside = datetime(2026, 10, 16, 3, 0, tzinfo=UTC)

    assert safety.check(request, _portfolio(), broker_name="fyers", now=outside).allowed is False
    oversized = request.model_copy(update={"actual_order_value": Decimal("10000")})
    assert (
        safety.check(oversized, _portfolio(), broker_name="fyers", now=NOW).reason
        == "Order would exceed LIVE_MAX_ORDER_VALUE_INR"
    )
    loaded = PortfolioState(
        cash=Decimal("100000"),
        positions=[Position(symbol="INFY", quantity=10, average_entry_price=Decimal("1500"))],
    )
    assert (
        safety.check(request, loaded, broker_name="fyers", now=NOW).reason
        == "Order would exceed LIVE_MAX_POSITION_VALUE_INR"
    )


def test_order_domain_records_live_mode_explicitly():
    request = _request()

    assert request.product == "INTRADAY"
    assert request.validity == "DAY"


async def test_live_coordinator_denies_before_any_broker_call(clock):
    scripted = ScriptedTransport([AssertionError("network used")])
    settings = _settings(live_trading_approved=False)
    broker = _fyers_broker(scripted, clock)
    coordinator = LiveOrderCoordinator(
        broker, _safety(settings, clock), ExecutionService(broker), clock
    )

    outcome = await coordinator.submit_decision_order(
        decision_id="d-1",
        correlation_id="c-1",
        proposal=_proposal(),
        tick=_tick(),
        portfolio=_portfolio(),
    )

    assert outcome.status == "DENIED"
    assert scripted.requests == []


async def test_live_coordinator_recovers_timeout_without_second_placement(clock):
    scripted = ScriptedTransport(
        [
            httpx.ConnectTimeout("timeout"),
            httpx.Response(
                200,
                json={"s": "ok", "code": 200, "orderBook": [{"id": "T1", "ord_status": 2}]},
            ),
            _fyers_status_response(2, tradedQty=1, tradePrice=1500),
        ]
    )
    settings = _settings()
    broker = _fyers_broker(scripted, clock)
    coordinator = LiveOrderCoordinator(
        broker, _safety(settings, clock), ExecutionService(broker), clock
    )

    outcome = await coordinator.submit_decision_order(
        decision_id="d-1",
        correlation_id="c-1",
        proposal=_proposal(),
        tick=_tick(),
        portfolio=_portfolio(),
    )

    assert outcome.status == "RECONCILED"
    assert outcome.order is not None
    assert outcome.order.live_order is True
    assert outcome.order.broker_name == "fyers"
    assert len([item for item in scripted.requests if item.method == "POST"]) == 1


async def test_live_coordinator_submits_and_reconciles_success(clock):
    scripted = ScriptedTransport(
        [_fyers_place_response("S1"), _fyers_status_response(2, order_id="S1")]
    )
    settings = _settings()
    broker = _fyers_broker(scripted, clock)
    coordinator = LiveOrderCoordinator(
        broker, _safety(settings, clock), ExecutionService(broker), clock
    )

    outcome = await coordinator.submit_decision_order(
        decision_id="d-9",
        correlation_id="c-9",
        proposal=_proposal(),
        tick=_tick(),
        portfolio=_portfolio(),
    )

    assert outcome.status == "SUBMITTED"
    assert outcome.reconciled is True
    assert outcome.order is not None
    assert outcome.order.status is OrderStatus.FILLED


async def test_fyers_update_uses_explicit_terms(clock):
    limit_request = _request().model_copy(
        update={"order_type": OrderType.LIMIT, "limit_price": Decimal("1600")}
    )
    scripted = ScriptedTransport(
        [
            _fyers_place_response("M1"),
            httpx.Response(200, json={"s": "ok", "code": 1102}),
            _fyers_status_response(6, order_id="M1"),
        ]
    )
    broker = _fyers_broker(scripted, clock)

    await broker.place_order(limit_request)
    updated = await broker.update_order(
        LiveOrderUpdate(broker_order_id="M1", quantity=2, limit_price=Decimal("1590"))
    )

    patch = next(item for item in scripted.requests if item.method == "PATCH")
    assert json.loads(patch.content.decode()) == {
        "id": "M1",
        "type": 1,
        "limitPrice": 1590.0,
        "qty": 2,
    }
    assert updated.status is OrderStatus.ACCEPTED


async def test_broker_errors_never_echo_credentials(clock):
    scripted = ScriptedTransport([httpx.Response(500, json={"s": "error"})])
    broker = _fyers_broker(scripted, clock)

    with pytest.raises(LiveBrokerResponseError) as excinfo:
        await broker.place_order(_request())

    assert "TOKEN" not in str(excinfo.value)


async def test_live_paths_are_not_wired_into_paper_runtime():
    import ai_trader.api.routes as routes
    import ai_trader.application.container as container
    import ai_trader.application.fyers_session as fyers_session
    import ai_trader.application.workflows.shadow_review as shadow_review

    forbidden = (
        "LiveFyersBroker",
        "LiveUpstoxBroker",
        "build_execution_broker",
        "LiveOrderCoordinator",
        "LiveSafetyEnvelope",
    )
    for module in (container, fyers_session, shadow_review, routes):
        source = inspect.getsource(module)
        for token in forbidden:
            assert token not in source
