"""FYERS historical OHLCV retrieval (market data only)."""

from datetime import UTC, datetime, timedelta

import httpx

from ai_trader.domain.market import Candle, Instrument
from ai_trader.integrations.fyers.auth import FyersAuth
from ai_trader.integrations.fyers.normalizer import normalize_ohlcv_rows

RESOLUTION_MINUTES = {"1": 1, "5": 5, "15": 15, "60": 60, "1D": 1440}


class FyersHistoricalError(RuntimeError):
    """Historical data could not be retrieved."""


async def fetch_ohlcv(
    auth: FyersAuth,
    symbol: str,
    instrument: Instrument,
    resolution: str = "1",
    range_from: datetime | None = None,
    range_to: datetime | None = None,
    days_back: int = 1,
) -> list[Candle]:
    """Fetch historical candles for one FYERS symbol, oldest first."""
    if resolution not in RESOLUTION_MINUTES:
        raise FyersHistoricalError(f"unsupported resolution: {resolution}")
    end = range_to or datetime.now(UTC)
    start = range_from or (end - timedelta(days=days_back))
    payload = {
        "symbol": symbol,
        "resolution": resolution,
        "date_format": "1",
        "range_from": start.strftime("%Y-%m-%d"),
        "range_to": end.strftime("%Y-%m-%d"),
        "cont_flag": "0",
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{auth.api_base_url}/data/ohlcv",
                headers={"Authorization": auth.authorization_header},
                json=payload,
            )
            response.raise_for_status()
        body = response.json()
    except httpx.HTTPError as exc:
        raise FyersHistoricalError("FYERS OHLCV request failed") from exc

    candles = body.get("candles") if isinstance(body, dict) else None
    if not isinstance(candles, list):
        raise FyersHistoricalError("FYERS OHLCV response did not contain candles")
    rows = [row for row in candles if isinstance(row, dict)]
    return normalize_ohlcv_rows(instrument, rows)
