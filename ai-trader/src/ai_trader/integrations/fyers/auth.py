"""FYERS credential handling for market data.

Credentials are read from settings only. No secret is logged, stored, or echoed.
"""

import base64
import json
import logging
from collections.abc import Iterable, Iterator
from decimal import Decimal

import httpx

DEFAULT_API_BASE_URL = "https://api-t1.fyers.in"
DEFAULT_DATA_WS_URL = "wss://socket.fyers.in/hsm/v1-5/prod"
CHANNEL = 11
CLIENT_SOURCE = "ai-trader/0.2"
# FYERS caps both symbol-token and quote requests at 50 symbols per call.
MAX_SYMBOLS_PER_REQUEST = 50

logger = logging.getLogger(__name__)


def _chunks(items: Iterable[str], size: int) -> Iterator[list[str]]:
    batch: list[str] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


class MissingFyersCredential(RuntimeError):
    """No market-data credential is configured. Values are never included in the message."""


class FyersAuth:
    """Holds the app id and access token used for REST headers and socket auth."""

    def __init__(
        self,
        client_id: str | None,
        access_token: str | None,
        api_base_url: str = DEFAULT_API_BASE_URL,
        data_ws_url: str = DEFAULT_DATA_WS_URL,
    ) -> None:
        if not client_id or not access_token:
            raise MissingFyersCredential(
                "FYERS_CLIENT_ID and FYERS_ACCESS_TOKEN are required for market data access"
            )
        self._client_id = client_id
        self._access_token = access_token
        self.api_base_url = api_base_url.rstrip("/")
        self.data_ws_url = data_ws_url

    @property
    def authorization_header(self) -> str:
        """FYERS REST and socket headers use ``<app_id>:<access_token>``."""
        return f"{self._client_id}:{self._access_token}"

    def __repr__(self) -> str:
        return f"FyersAuth(api_base_url={self.api_base_url!r}, credentials='***')"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        return isinstance(other, FyersAuth) and other.authorization_header == (
            self.authorization_header
        )

    def __hash__(self) -> int:
        return hash(("FyersAuth", self.api_base_url))

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, object]:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{self.api_base_url}{path}",
                headers={"Authorization": self.authorization_header},
                params=params,
            )
            response.raise_for_status()
        body: dict[str, object] = response.json()
        return body

    async def _post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self.api_base_url}{path}",
                headers={
                    "Authorization": self.authorization_header,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
        body: dict[str, object] = response.json()
        return body

    async def hsm_symbols(self, symbols: list[str]) -> dict[str, str]:
        """Resolve trading symbols such as ``NSE:INFY-EQ`` to raw FYERS tokens.

        Returns the exchange token strings verbatim (``10100000001594``); the
        HSM topic uses the segment prefix and token suffix derived from them.
        Requests are chunked because the endpoint accepts at most 50 symbols.
        """
        resolved: dict[str, str] = {}
        failure: LookupError | None = None
        for chunk in _chunks(dict.fromkeys(symbols), MAX_SYMBOLS_PER_REQUEST):
            try:
                payload = await self._post("/data/symbol-token", {"symbols": chunk})
            except Exception as exc:  # noqa: BLE001 - one bad batch must not stop the rest
                failure = LookupError(f"FYERS symbol-token request failed: {exc}")
                logger.warning(
                    "FYERS symbol-token batch of %d failed; continuing with other batches",
                    len(chunk),
                )
                continue
            valid = payload.get("validSymbol")
            if not isinstance(valid, dict) or not valid:
                failure = LookupError(
                    "FYERS symbol-token response did not contain validSymbol"
                )
                continue
            resolved.update({str(symbol): str(token) for symbol, token in valid.items()})
        if not resolved:
            raise failure or LookupError(
                "FYERS symbol-token response did not contain validSymbol"
            )
        return resolved

    async def quotes(self, symbols: Iterable[str]) -> dict[str, Decimal]:
        """Latest traded price per FYERS wire symbol.

        Used to classify the broad master list by live price. Symbols FYERS does
        not resolve (or has no quote for) are simply absent from the result.
        """
        prices: dict[str, Decimal] = {}
        for chunk in _chunks(dict.fromkeys(symbols), MAX_SYMBOLS_PER_REQUEST):
            try:
                payload = await self._get("/data/quotes", {"symbols": ",".join(chunk)})
            except Exception:  # noqa: BLE001 - partial quotes still classify the rest
                logger.warning(
                    "FYERS quote batch of %d failed; using the remaining quotes", len(chunk)
                )
                continue
            rows = payload.get("d")
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                name = row.get("n")
                values = row.get("v")
                if not isinstance(name, str) or not isinstance(values, dict):
                    continue
                last_price = values.get("lp")
                if last_price is None:
                    continue
                try:
                    price = Decimal(str(last_price))
                except ArithmeticError:
                    continue
                if price > 0:
                    prices[name.upper()] = price
        return prices


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def extract_hsm_key(access_token: str) -> str:
    """Read the ``hsm_key`` claim from the access-token JWT.

    The leading ``<app_id>:`` prefix is stripped when present.
    """
    token = access_token.split(":", 1)[1] if ":" in access_token else access_token
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError("FYERS access token is not a JWT")
    try:
        payload = json.loads(_b64url_decode(parts[1]))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("FYERS access token payload is not valid JSON") from exc
    hsm_key = payload.get("hsm_key") if isinstance(payload, dict) else None
    if not isinstance(hsm_key, str) or not hsm_key:
        raise ValueError("FYERS access token payload has no hsm_key claim")
    return hsm_key
