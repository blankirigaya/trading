"""Disabled Upstox live-order adapter.

This module contains the only Upstox order URLs in the repository. It is never imported
by the paper-trading session, container, workflow, or routes. Every method refuses to
use the network unless the adapter was explicitly constructed with ``enabled=True``.
"""

import re
from collections.abc import Callable
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
from ai_trader.domain.orders import LiveOrderUpdate, Order, OrderRequest, OrderStatus
from ai_trader.domain.portfolio import PortfolioState, Position
from ai_trader.integrations.upstox.auth import UpstoxAuth

InstrumentResolver = Callable[[str, str], str]


def _order_tag(client_order_id: str) -> str:
    """Derive a broker-safe idempotency tag without broker secrets."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", f"AI-{client_order_id}")[:36]
    if not cleaned:
        return "AI-ORDER"
    return cleaned


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


class LiveUpstoxBroker(LiveBroker):
    """Upstox order adapter. Disabled by default; no paper path constructs it."""

    broker_name = "upstox"

    def __init__(
        self,
        auth: UpstoxAuth,
        instrument_resolver: InstrumentResolver,
        *,
        clock: Clock | None = None,
        client: httpx.AsyncClient | None = None,
        order_base_url: str = "https://api-hft.upstox.com",
        enabled: bool = False,
        timeout_seconds: int = 15,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Live Upstox timeout must be positive")
        self.auth = auth
        self.instrument_resolver = instrument_resolver
        self.clock = clock or SystemClock()
        self._client = client
        self.order_base_url = order_base_url.rstrip("/")
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
            raise LiveTradingDisabled("Live Upstox trading is disabled")

    def _headers(self) -> dict[str, str]:
        return dict(self.auth.headers)

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
            base = (
                self.auth.api_base_url if path.startswith("/v2/portfolio") else self.order_base_url
            )
            try:
                response = await client.request(
                    method,
                    f"{base}{path}",
                    headers=self._headers(),
                    params=params,
                    json=payload,
                )
            except httpx.TimeoutException as exc:
                raise LiveBrokerTimeout("Upstox request timed out; order state is unknown") from exc
            except httpx.HTTPError as exc:
                raise LiveBrokerResponseError(
                    status_code=None,
                    code=None,
                    message=f"Upstox request failed: {type(exc).__name__}",
                ) from exc
            if response.status_code >= 400:
                raise LiveBrokerResponseError(
                    status_code=response.status_code,
                    code=None,
                    message=f"Upstox request failed with HTTP {response.status_code}",
                )
            try:
                body = response.json()
            except ValueError as exc:
                raise LiveBrokerResponseError(
                    status_code=response.status_code,
                    code=None,
                    message="Upstox response was not JSON",
                ) from exc
            if not isinstance(body, dict):
                raise LiveBrokerResponseError(
                    status_code=response.status_code,
                    code=None,
                    message="Upstox response had an unexpected shape",
                )
            return body
        finally:
            if created:
                await client.aclose()

    def _remember(self, request: OrderRequest, broker_order_id: str) -> Order:
        order = Order(
            broker_order_id=broker_order_id,
            request=request,
            status=OrderStatus.ACCEPTED,
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
            raise LiveBrokerUnsupported("Initial live Upstox scope is intraday orders only")
        if request.order_type.value not in {"MARKET", "LIMIT"}:
            raise LiveBrokerUnsupported(
                f"Unsupported live Upstox order type: {request.order_type.value}"
            )
        instrument_token = self.instrument_resolver(request.symbol, request.exchange)
        return {
            "quantity": request.quantity,
            "product": "I",
            "validity": request.validity,
            "price": float(request.limit_price) if request.limit_price is not None else 0,
            "tag": _order_tag(request.client_order_id),
            "instrument_token": instrument_token,
            "order_type": request.order_type.value,
            "transaction_type": request.side.value,
            "disclosed_quantity": 0,
            "trigger_price": 0,
            "is_amo": False,
            "market_protection": -1 if request.order_type.value == "MARKET" else 0,
        }

    async def place_order(self, request: OrderRequest) -> Order:
        self._require_enabled()
        existing = self._by_client.get(request.client_order_id)
        if existing is not None:
            return self._orders[existing]
        self._pending[request.client_order_id] = request
        try:
            body = await self._request("POST", "/v2/order/place", payload=self._payload(request))
        except LiveBrokerResponseError:
            self._pending.pop(request.client_order_id, None)
            raise
        if body.get("status") != "success":
            raise LiveBrokerResponseError(
                status_code=None, code=None, message="Upstox order placement failed"
            )
        data = body.get("data")
        broker_order_id = data.get("order_id") if isinstance(data, dict) else None
        if not isinstance(broker_order_id, str) or not broker_order_id:
            raise LiveOrderUnknown(
                "Upstox accepted the order without returning an ID; reconcile before retrying"
            )
        return self._remember(request, broker_order_id)

    async def modify_order(self, broker_order_id: str) -> Order:
        raise LiveBrokerUnsupported("Use update_order with explicit replacement terms")

    async def update_order(self, update: LiveOrderUpdate) -> Order:
        self._require_enabled()
        stored = self._orders.get(update.broker_order_id)
        if stored is None:
            raise LiveOrderUnknown("Unknown local live order; reconcile it before modifying")
        payload: dict[str, object] = {
            "order_id": update.broker_order_id,
            "validity": stored.request.validity,
            "order_type": stored.request.order_type.value,
        }
        if update.quantity is not None:
            payload["quantity"] = update.quantity
        if update.limit_price is not None:
            payload["price"] = float(update.limit_price)
        body = await self._request("PUT", "/v2/order/modify", payload=payload)
        if body.get("status") != "success":
            raise LiveBrokerResponseError(
                status_code=None, code=None, message="Upstox order modification failed"
            )
        return await self.reconcile_order(update.broker_order_id)

    async def cancel_order(self, broker_order_id: str) -> Order:
        self._require_enabled()
        stored = self._orders.get(broker_order_id)
        if stored is None:
            raise LiveOrderUnknown("Unknown local live order; reconcile it before cancelling")
        body = await self._request(
            "DELETE", "/v2/order/cancel", params={"order_id": broker_order_id}
        )
        if body.get("status") != "success":
            raise LiveBrokerResponseError(
                status_code=None, code=None, message="Upstox order cancellation failed"
            )
        return await self.reconcile_order(broker_order_id)

    async def get_order_status(self, broker_order_id: str) -> Order:
        return await self.reconcile_order(broker_order_id)

    async def reconcile_order(self, broker_order_id: str) -> Order:
        self._require_enabled()
        body = await self._request("GET", "/v2/order/details", params={"order_id": broker_order_id})
        data = body.get("data")
        if not isinstance(data, dict) or str(data.get("order_id")) != broker_order_id:
            raise LiveOrderUnknown("Upstox has no record of this order ID")
        return self._update_from_entry(broker_order_id, data)

    async def reconcile_client_order(self, client_order_id: str) -> Order | None:
        self._require_enabled()
        broker_order_id = self._by_client.get(client_order_id)
        if broker_order_id is not None:
            return await self.reconcile_order(broker_order_id)
        pending = self._pending.get(client_order_id)
        if pending is None:
            return None
        body = await self._request("GET", "/v2/order/retrieve-all")
        data = body.get("data")
        if not isinstance(data, list):
            raise LiveBrokerResponseError(
                status_code=None, code=None, message="Upstox order book had an unexpected shape"
            )
        wanted = _order_tag(client_order_id)
        for entry in data:
            if not isinstance(entry, dict):
                continue
            broker_id = entry.get("order_id")
            if (
                isinstance(broker_id, str)
                and broker_id
                and entry.get("tag") == wanted
            ):
                self._remember(pending, broker_id)
                return await self.reconcile_order(broker_id)
        return None

    def _update_from_entry(self, broker_order_id: str, entry: dict[str, object]) -> Order:
        stored = self._orders.get(broker_order_id)
        if stored is None:
            raise LiveOrderUnknown("Unknown local live order; reconcile it before trusting it")
        status = str(entry.get("status") or "").lower()
        if status == "complete":
            order_status = OrderStatus.FILLED
        elif status == "cancelled":
            order_status = OrderStatus.CANCELLED
        elif status == "rejected":
            order_status = OrderStatus.REJECTED
        else:
            order_status = OrderStatus.ACCEPTED
        filled_quantity = _optional_int(entry.get("filled_quantity"))
        if order_status is OrderStatus.FILLED and filled_quantity is None:
            filled_quantity = stored.request.quantity
        updated = stored.model_copy(
            update={
                "status": order_status,
                "filled_quantity": filled_quantity,
                "average_fill_price": _optional_decimal(entry.get("average_price")),
                "broker_status_detail": str(entry.get("status")) if entry.get("status") else None,
            }
        )
        self._orders[broker_order_id] = updated
        return updated

    async def get_orders(self) -> list[Order]:
        self._require_enabled()
        body = await self._request("GET", "/v2/order/retrieve-all")
        data = body.get("data")
        if not isinstance(data, list):
            raise LiveBrokerResponseError(
                status_code=None, code=None, message="Upstox order book had an unexpected shape"
            )
        orders: list[Order] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            broker_order_id = entry.get("order_id")
            if not isinstance(broker_order_id, str) or not broker_order_id:
                continue
            if broker_order_id in self._orders:
                orders.append(self._update_from_entry(broker_order_id, entry))
        return orders

    async def get_account(self) -> PortfolioState:
        return await self._portfolio()

    async def get_positions(self) -> PortfolioState:
        return await self._portfolio()

    async def _portfolio(self) -> PortfolioState:
        funds = await self._request("GET", "/v2/user/get-funds-and-margin")
        funds_data = funds.get("data")
        equity = funds_data.get("equity") if isinstance(funds_data, dict) else None
        available = (
            _optional_decimal(equity.get("available_margin")) if isinstance(equity, dict) else None
        )
        if available is None:
            raise LiveBrokerResponseError(
                status_code=None, code=None, message="Upstox funds did not include available margin"
            )
        positions_body = await self._request("GET", "/v2/portfolio/short-term-positions")
        positions_data = positions_body.get("data")
        if not isinstance(positions_data, list):
            raise LiveBrokerResponseError(
                status_code=None, code=None, message="Upstox positions had an unexpected shape"
            )
        return PortfolioState(cash=available, positions=self._map_positions(positions_data))

    def _map_positions(self, entries: list[object]) -> list[Position]:
        positions: list[Position] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            symbol = str(entry.get("trading_symbol") or "")
            quantity = _optional_int(entry.get("quantity"))
            average = _optional_decimal(entry.get("average_price"))
            if not symbol or quantity is None or quantity == 0 or average is None:
                continue
            positions.append(
                Position(
                    symbol=symbol,
                    quantity=quantity,
                    average_entry_price=average,
                    realized_pnl=_optional_decimal(entry.get("realised")) or Decimal("0"),
                )
            )
        return positions
