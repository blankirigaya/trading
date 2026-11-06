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
    """Fetch historical candles for one FYERS symbol, oldest first.

    Uses the v3 ``GET /data/history`` endpoint. FYERS returns rows as
    ``[epoch, open, high, low, close, volume]`` arrays, so they are adapted to
    the dict shape ``normalize_ohlcv_rows`` expects.
    """
    if resolution not in RESOLUTION_MINUTES:
        raise FyersHistoricalError(f"unsupported resolution: {resolution}")
    end = range_to or datetime.now(UTC)
    start = range_from or (end - timedelta(days=days_back))
    params = {
        "symbol": symbol,
        "resolution": resolution,
        "date_format": "1",
        "range_from": start.strftime("%Y-%m-%d"),
        "range_to": end.strftime("%Y-%m-%d"),
        "cont_flag": "1",
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{auth.api_base_url}/data/history",
                headers={"Authorization": auth.authorization_header},
                params=params,
            )
            response.raise_for_status()
        body = response.json()
    except httpx.HTTPError as exc:
        raise FyersHistoricalError("FYERS history request failed") from exc

    if isinstance(body, dict) and body.get("s") not in (None, "ok"):
        raise FyersHistoricalError(f"FYERS history rejected: {body.get('message')}")

    candles = body.get("candles") if isinstance(body, dict) else None
    if not isinstance(candles, list):
        raise FyersHistoricalError("FYERS history response did not contain candles")
    rows: list[dict[str, object]] = []
    for row in candles:
        if isinstance(row, dict):
            rows.append(row)
        elif isinstance(row, list) and len(row) >= 5:
            rows.append(
                {
                    "epoch": row[0],
                    "open": row[1],
                    "high": row[2],
                    "low": row[3],
                    "close": row[4],
                    "volume": row[5] if len(row) > 5 else None,
                }
            )
    return normalize_ohlcv_rows(instrument, rows)
