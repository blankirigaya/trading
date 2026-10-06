from decimal import Decimal
from uuid import uuid4

from ai_trader.domain.market import Candle, Instrument, MarketState, MarketTick, RawMarketEvent
from ai_trader.integrations.upstox.protobuf.decoder import DecodedFeed


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def normalize_v3_feed(
    raw: RawMarketEvent, instrument: Instrument, decoded: DecodedFeed
) -> MarketState:
    """Map one decoded V3 frame into the internal MarketState. No trading judgment here."""
    feeds = decoded.snapshot.get("feeds", {})
    if not isinstance(feeds, dict) or instrument.instrument_key not in feeds:
        raise ValueError("V3 payload has no data for the requested instrument")
    entry = feeds[instrument.instrument_key]
    if not isinstance(entry, dict):
        raise ValueError("V3 instrument entry has an invalid shape")

    ltpc: dict[str, object] = {}
    market_ff: dict[str, object] | None = None
    first_level: dict[str, object] | None = None
    for key in ("ltpc", "market_ff", "index_ff"):
        value = entry.get(key)
        if isinstance(value, dict) and isinstance(value.get("ltpc"), dict):
            ltpc = value["ltpc"]
            if key == "market_ff":
                market_ff = value
            break
    first_level = entry.get("first_level") if isinstance(entry.get("first_level"), dict) else None
    if not ltpc and first_level and isinstance(first_level.get("ltpc"), dict):
        ltpc = first_level["ltpc"]
    if "ltp" not in ltpc:
        raise ValueError("V3 instrument entry has no last traded price")

    last_price = _decimal(ltpc.get("ltp"))
    if last_price is None or last_price <= 0:
        raise ValueError("V3 last traded price is not usable")

    exchange_timestamp = raw.exchange_timestamp or raw.received_timestamp
    if decoded.current_timestamp_ms:
        from datetime import UTC, datetime

        exchange_timestamp = datetime.fromtimestamp(decoded.current_timestamp_ms / 1000, tz=UTC)

    bid: Decimal | None = None
    ask: Decimal | None = None
    quotes = market_ff.get("bid_ask") if market_ff else None
    if isinstance(quotes, list) and quotes and isinstance(quotes[0], dict):
        bid = _decimal(quotes[0].get("bidP"))
        ask = _decimal(quotes[0].get("askP"))
    if bid is None and first_level:
        bid = _decimal(first_level.get("bidP"))
        ask = _decimal(first_level.get("askP"))

    tick = MarketTick(
        symbol=instrument.symbol,
        exchange=instrument.exchange,
        segment=instrument.segment,
        last_price=last_price,
        exchange_timestamp=exchange_timestamp,
        received_timestamp=raw.received_timestamp,
        processed_timestamp=raw.received_timestamp,
        raw_payload=decoded.snapshot,
    )

    candles: list[Candle] = []
    if market_ff:
        ohlc_rows = market_ff.get("ohlc")
        for ohlc in ohlc_rows if isinstance(ohlc_rows, list) else []:
            if not isinstance(ohlc, dict) or not ohlc.get("ts"):
                continue
            ts = ohlc["ts"]
            from datetime import UTC, datetime

            end = datetime.fromtimestamp(int(ts) / 1000, tz=UTC)
            candles.append(
                Candle(
                    start_timestamp=end,
                    end_timestamp=end,
                    open=_decimal(ohlc.get("open")) or last_price,
                    high=_decimal(ohlc.get("high")) or last_price,
                    low=_decimal(ohlc.get("low")) or last_price,
                    close=_decimal(ohlc.get("close")) or last_price,
                    volume=int(ohlc.get("vol") or 0),
                )
            )

    return MarketState(
        snapshot_id=str(uuid4()),
        instrument=instrument,
        timestamp=exchange_timestamp,
        last_price=last_price,
        previous_close=_decimal(ltpc.get("cp")),
        recent_ticks=[tick],
        current_1m_candle=None,
        recent_1m_candles=candles,
        bid=bid,
        ask=ask,
    )
