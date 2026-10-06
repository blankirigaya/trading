import gzip
import json
from collections.abc import Iterable

import httpx

from ai_trader.domain.market import Instrument


class InstrumentResolutionError(LookupError):
    pass


async def fetch_instruments() -> list[dict[str, object]]:
    """Fetch Upstox's published complete instrument master; no broker API/order access."""
    url = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(url)
        response.raise_for_status()
    decoded = json.loads(gzip.decompress(response.content))
    if not isinstance(decoded, list):
        raise InstrumentResolutionError("Upstox instrument master has invalid shape")
    return [item for item in decoded if isinstance(item, dict)]


def resolve_instrument(rows: Iterable[dict[str, object]], symbol: str, exchange: str) -> Instrument:
    for row in rows:
        if row.get("trading_symbol") == symbol and row.get("exchange") == exchange:
            key = row.get("instrument_key")
            if isinstance(key, str):
                return Instrument(instrument_key=key, symbol=symbol, exchange=exchange)
    raise InstrumentResolutionError(f"No {exchange} instrument found for {symbol}")
