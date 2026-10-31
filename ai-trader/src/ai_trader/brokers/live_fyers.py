"""Disabled FYERS live-order adapter.

This module contains the only FYERS order URLs in the repository. It is never imported
by the paper-trading session, container, workflow, or routes. Every method refuses to
use the network unless the adapter was explicitly constructed with ``enabled=True``.
"""

import re
from decimal import Decimal

import httpx

from ai_trader.brokers.live import (
    LiveBroker,
    LiveBrokerResponseError,
    LiveBrokerTimeout,
    LiveBrokerUnsupported,
    LiveOrderUnknown,
    LiveTradingDisabled,
)
from ai_trader.domain.common import Clock, SystemClock
from ai_trader.domain.orders import LiveOrderUpdate, Order, OrderRequest, OrderSide, OrderStatus
from ai_trader.domain.portfolio import PortfolioState, Position
from ai_trader.integrations.fyers.auth import FyersAuth

_ORDER_TYPES = {OrderSide.BUY: 1, OrderSide.SELL: -1}
_ORDER_KINDS = {"MARKET": 2, "LIMIT": 1}
_FILLED_STATUSES = {2}
_CANCELLED_STATUSES = {1}
_REJECTED_STATUSES = {5}
_UNKNOWN_FINAL_STATUSES = {3, 7}


def _order_tag(client_order_id: str) -> str:
    """Derive a FYERS-compatible idempotency tag without broker secrets."""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", f"AI{client_order_id}")[:30]
    if not cleaned or cleaned in {"Untagged"}:
        return "AIORDER"
    return cleaned


def _decimal(value: object, *, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise LiveBrokerResponseError(
            status_code=None, code=None, message=f"FYERS response has an invalid {field}"
        ) from exc


def _optional_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


class LiveFyersBroker(LiveBroker):
    """FYERS order adapter. Disabled by default; no paper path constructs it."""

    broker_name = "fyers"

    def __init__(
        self,
        auth: FyersAuth,
        *,
        clock: Clock | None = None,
        client: httpx.AsyncClient | None = None,
        enabled: bool = False,
        timeout_seconds: int = 15,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Live FYERS timeout must be positive")
        self.auth = auth
        self.clock = clock or SystemClock()
        self._client = client
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds
        self._orders: dict[str, Order] = {}
        self._by_client: dict[str, str] = {}
        self._pending: dict[str, OrderRequest] = {}

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise LiveTradingDisabled("Live FYERS trading is disabled")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self.auth.authorization_header, "Content-Type": "application/json"}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        payload: object = None,
    ) -> dict[str, object]:
        self._require_enabled()
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds)
        created = self._client is None
        try:
            try:
                response = await client.request(
                    method,
                    f"{self.auth.api_base_url}{path}",
                    headers=self._headers(),
                    params=params,
                    json=payload,
                )
            except httpx.TimeoutException as exc:
                raise LiveBrokerTimeout("FYERS request timed out; order state is unknown") from exc
            except httpx.HTTPError as exc:
                raise LiveBrokerResponseError(
                    status_code=None,
                    code=None,
                    message=f"FYERS request failed: {type(exc).__name__}",
                ) from exc
            if response.status_code >= 400:
                raise LiveBrokerResponseError(
                    status_code=response.status_code,
                    code=None,
                    message=f"FYERS request failed with HTTP {response.status_code}",
                )
            try:
                body = response.json()
            except ValueError as exc:
                raise LiveBrokerResponseError(
                    status_code=response.status_code,
                    code=None,
                    message="FYERS response was not JSON",
                ) from exc
            if not isinstance(body, dict):
                raise LiveBrokerResponseError(
                    status_code=response.status_code,
                    code=None,
                    message="FYERS response had an unexpected shape",
                )
            return body
        finally:
            if created:
                await client.aclose()

    @staticmethod
    def _require_ok(
        body: dict[str, object], *, accepted: set[int], action: str
    ) -> dict[str, object]:
        if body.get("s") == "ok" and body.get("code") in accepted:
            return body
        code = body.get("code")
        message = body.get("message")
        raise LiveBrokerResponseError(
            status_code=None,
            code=str(code) if code is not None else None,
            message=(
                f"FYERS {action} failed: {message}"
                if isinstance(message, str) and message
                else f"FYERS {action} failed"
            ),
        )

    def _remember(self, request: OrderRequest, broker_order_id: str, status: OrderStatus) -> Order:
        order = Order(
            broker_order_id=broker_order_id,
            request=request,
            status=status,
            created_at=self.clock.now(),
            broker_name=self.broker_name,
            live_order=True,
        )
        self._orders[broker_order_id] = order
        self._by_client[request.client_order_id] = broker_order_id
        self._pending.pop(request.client_order_id, None)
        return order

    def _payload(self, request: OrderRequest) -> dict[str, object]:
        if request.product != "INTRADAY":
            raise LiveBrokerUnsupported("Initial live FYERS scope is intraday orders only")
        if request.order_type.value not in _ORDER_KINDS:
            raise LiveBrokerUnsupported(
                f"Unsupported live FYERS order type: {request.order_type.value}"
            )
        return {
            "symbol": request.symbol,
            "qty": request.quantity,
            "type": _ORDER_KINDS[request.order_type.value],
            "side": _ORDER_TYPES[request.side],
            "productType": "INTRADAY",
            "limitPrice": float(request.limit_price) if request.limit_price is not None else 0,
            "stopPrice": 0,
            "validity": request.validity,
            "disclosedQty": 0,
            "offlineOrder": False,
            "stopLoss": 0,
            "takeProfit": 0,
            "orderTag": _order_tag(request.client_order_id),
            "isSliceOrder": False,
        }

    async def place_order(self, request: OrderRequest) -> Order:
        self._require_enabled()
        existing = self._by_client.get(request.client_order_id)
        if existing is not None:
            return self._orders[existing]
        self._pending[request.client_order_id] = request
        try:
            body = self._require_ok(
                await self._request("POST", "/api/v3/orders/sync", payload=self._payload(request)),
                accepted={1101, 201},
                action="order placement",
            )
        except LiveBrokerResponseError:
            self._pending.pop(request.client_order_id, None)
            raise
        broker_order_id = body.get("id")
        if not isinstance(broker_order_id, (str, int)) or not str(broker_order_id):
            raise LiveOrderUnknown(
                "FYERS accepted the order without returning an ID; reconcile before retrying"
            )
        return self._remember(request, str(broker_order_id), OrderStatus.ACCEPTED)

    async def modify_order(self, broker_order_id: str) -> Order:
        raise LiveBrokerUnsupported("Use update_order with explicit replacement terms")

    async def update_order(self, update: LiveOrderUpdate) -> Order:
        self._require_enabled()
        stored = self._orders.get(update.broker_order_id)
        if stored is None:
            raise LiveOrderUnknown("Unknown local live order; reconcile it before modifying")
        payload: dict[str, object] = {
            "id": update.broker_order_id,
            "type": _ORDER_KINDS[stored.request.order_type.value],
        }
        if update.quantity is not None:
            payload["qty"] = update.quantity
        if update.limit_price is not None:
            payload["limitPrice"] = float(update.limit_price)
        self._require_ok(
            await self._request("PATCH", "/api/v3/orders/sync", payload=payload),
            accepted={1102},
            action="order modification",
        )
        return await self.reconcile_order(update.broker_order_id)

    async def cancel_order(self, broker_order_id: str) -> Order:
        self._require_enabled()
        stored = self._orders.get(broker_order_id)
        if stored is None:
            raise LiveOrderUnknown("Unknown local live order; reconcile it before cancelling")
        self._require_ok(
            await self._request("DELETE", "/api/v3/orders/sync", payload={"id": broker_order_id}),
            accepted={1103},
            action="order cancellation",
        )
        return await self.reconcile_order(broker_order_id)

    async def get_order_status(self, broker_order_id: str) -> Order:
        return await self.reconcile_order(broker_order_id)

    async def reconcile_order(self, broker_order_id: str) -> Order:
        self._require_enabled()
        body = self._require_ok(
            await self._request("GET", "/api/v3/orders", params={"id": broker_order_id}),
            accepted={200},
            action="order lookup",
        )
        entries = body.get("orderBook")
        if not isinstance(entries, list):
            raise LiveBrokerResponseError(
                status_code=None, code=None, message="FYERS order book had an unexpected shape"
            )
        for entry in entries:
            if isinstance(entry, dict) and str(entry.get("id")) == broker_order_id:
                return self._update_from_entry(broker_order_id, entry)
        raise LiveOrderUnknown("FYERS has no record of this order ID")

    async def reconcile_client_order(self, client_order_id: str) -> Order | None:
        self._require_enabled()
        broker_order_id = self._by_client.get(client_order_id)
        if broker_order_id is not None:
            return await self.reconcile_order(broker_order_id)
        pending = self._pending.get(client_order_id)
        if pending is None:
            return None
        body = self._require_ok(
            await self._request(
                "GET",
                "/api/v3/orders",
                params={"order_tag": f"1:{_order_tag(client_order_id)}"},
            ),
            accepted={200},
            action="order lookup",
        )
        entries = body.get("orderBook")
        if not isinstance(entries, list):
            raise LiveBrokerResponseError(
                status_code=None, code=None, message="FYERS order book had an unexpected shape"
            )
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            broker_id = entry.get("id")
            if isinstance(broker_id, (str, int)) and str(broker_id):
                self._remember(pending, str(broker_id), OrderStatus.ACCEPTED)
                return await self.reconcile_order(str(broker_id))
        return None

    def _update_from_entry(self, broker_order_id: str, entry: dict[str, object]) -> Order:
        stored = self._orders.get(broker_order_id)
        if stored is None:
            raise LiveOrderUnknown("Unknown local live order; reconcile it before trusting it")
        status_value = _optional_int(entry.get("ord_status"))
        if status_value in _FILLED_STATUSES:
            status = OrderStatus.FILLED
        elif status_value in _CANCELLED_STATUSES:
            status = OrderStatus.CANCELLED
        elif status_value in _REJECTED_STATUSES:
            status = OrderStatus.REJECTED
        elif status_value in _UNKNOWN_FINAL_STATUSES:
            status = OrderStatus.CANCELLED
        else:
            status = OrderStatus.ACCEPTED
        filled_quantity = _optional_int(entry.get("tradedQty"))
        if status is OrderStatus.FILLED and filled_quantity is None:
            filled_quantity = stored.request.quantity
        updated = stored.model_copy(
            update={
                "status": status,
                "filled_quantity": filled_quantity,
                "average_fill_price": _optional_decimal(entry.get("tradePrice")),
                "broker_status_detail": str(status_value) if status_value is not None else None,
            }
        )
        self._orders[broker_order_id] = updated
        return updated

    async def get_orders(self) -> list[Order]:
        self._require_enabled()
        body = self._require_ok(
            await self._request("GET", "/api/v3/orders"), accepted={200}, action="order lookup"
        )
        entries = body.get("orderBook")
        if not isinstance(entries, list):
            raise LiveBrokerResponseError(
                status_code=None, code=None, message="FYERS order book had an unexpected shape"
            )
        orders: list[Order] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            broker_order_id = entry.get("id")
            if not isinstance(broker_order_id, (str, int)) or not str(broker_order_id):
                continue
            if str(broker_order_id) in self._orders:
                orders.append(self._update_from_entry(str(broker_order_id), entry))
        return orders

    async def get_account(self) -> PortfolioState:
        return PortfolioState(cash=await self._available_cash(), positions=await self._positions())

    async def get_positions(self) -> PortfolioState:
        return PortfolioState(cash=await self._available_cash(), positions=await self._positions())

    async def _available_cash(self) -> Decimal:
        body = self._require_ok(
            await self._request("GET", "/api/v3/funds"), accepted={200}, action="fund lookup"
        )
        entries = body.get("fund_limit")
        if not isinstance(entries, list):
            raise LiveBrokerResponseError(
                status_code=None, code=None, message="FYERS funds had an unexpected shape"
            )
        for entry in entries:
            if isinstance(entry, dict) and (
                entry.get("title") == "Available Balance" or entry.get("id") == 10
            ):
                return _decimal(entry.get("equityAmount"), field="available balance")
        raise LiveBrokerResponseError(
            status_code=None, code=None, message="FYERS funds did not include available balance"
        )

    async def _positions(self) -> list[Position]:
        body = self._require_ok(
            await self._request("GET", "/api/v3/positions"),
            accepted={200},
            action="position lookup",
        )
        entries = body.get("netPositions")
        if not isinstance(entries, list):
            raise LiveBrokerResponseError(
                status_code=None, code=None, message="FYERS positions had an unexpected shape"
            )
        positions: list[Position] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            symbol = str(entry.get("symbol") or "")
            ticker = symbol.split(":")[-1].removesuffix("-EQ") if symbol else ""
            quantity = _optional_int(entry.get("netQty", entry.get("qty")))
            average = _optional_decimal(entry.get("netAvg", entry.get("avgPrice")))
            if not ticker or quantity is None or quantity == 0 or average is None:
                continue
            positions.append(
                Position(
                    symbol=ticker,
                    quantity=quantity,
                    average_entry_price=average,
                    realized_pnl=_optional_decimal(entry.get("realized_profit")) or Decimal("0"),
                )
            )
        return positions
