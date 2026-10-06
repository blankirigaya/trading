"""Decoding boundary for Upstox Market Data Feed V3 binary frames.

The generated protobuf objects are confined to this module and the Upstox normalizer.
Domain and application code receives plain typed models only.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ai_trader.integrations.upstox.protobuf import MarketDataFeed_pb2 as _generated

# Protobuf-generated modules expose message classes and enum values only at runtime,
# so static analyzers cannot see them. The boundary is deliberately typed as Any and
# every value is immediately converted into explicit typed structures below.
pb: Any = _generated

SCHEMA_VERSION = "market-data-feed-v3"


class V3DecodeError(ValueError):
    """The binary frame is not a decodable V3 FeedResponse."""


class FeedType(StrEnum):
    INITIAL_FEED = "initial_feed"
    LIVE_FEED = "live_feed"
    MARKET_INFO = "market_info"


_FEED_TYPES: dict[int, FeedType] = {
    pb.initial_feed: FeedType.INITIAL_FEED,
    pb.live_feed: FeedType.LIVE_FEED,
    pb.market_info: FeedType.MARKET_INFO,
}

_MARKET_STATUS: dict[int, str] = {
    pb.PRE_OPEN_START: "PRE_OPEN_START",
    pb.PRE_OPEN_END: "PRE_OPEN_END",
    pb.NORMAL_OPEN: "NORMAL_OPEN",
    pb.NORMAL_CLOSE: "NORMAL_CLOSE",
    pb.CLOSING_START: "CLOSING_START",
    pb.CLOSING_END: "CLOSING_END",
}


@dataclass(frozen=True)
class DecodedFeed:
    """Provider-neutral view of one V3 frame."""

    feed_type: FeedType
    current_timestamp_ms: int
    instrument_keys: tuple[str, ...]
    snapshot: dict[str, Any]
    market_status: dict[str, str] = field(default_factory=dict)


def decode_v3_frame(payload: bytes) -> DecodedFeed:
    response = pb.FeedResponse()
    try:
        response.ParseFromString(payload)
    except Exception as exc:
        raise V3DecodeError("Unable to decode Upstox V3 protobuf frame") from exc

    feed_type = _FEED_TYPES.get(response.type)
    if feed_type is None:
        raise V3DecodeError(f"Unknown V3 feed type: {response.type}")

    status = {
        segment: _MARKET_STATUS.get(value, str(value))
        for segment, value in response.marketInfo.segmentStatus.items()
    }

    snapshot: dict[str, Any] = {"feeds": {}, "market_status": status}
    for instrument_key, feed in response.feeds.items():
        entry: dict[str, Any] = {"request_mode": pb.RequestMode.Name(feed.requestMode)}
        which = feed.WhichOneof("FeedUnion")
        entry["feed_union"] = which
        if feed.HasField("ltpc"):
            entry["ltpc"] = {
                "ltp": feed.ltpc.ltp,
                "cp": feed.ltpc.cp,
                "ltt": feed.ltpc.ltt,
                "ltq": feed.ltpc.ltq,
                "iep": feed.ltpc.iep.value,
            }
        if feed.HasField("fullFeed"):
            full = feed.fullFeed
            full_which = full.WhichOneof("FullFeedUnion")
            entry["full_feed_union"] = full_which
            if full.HasField("marketFF"):
                entry["market_ff"] = {
                    "ltpc": {"ltp": full.marketFF.ltpc.ltp, "cp": full.marketFF.ltpc.cp},
                    "atp": full.marketFF.atp,
                    "vtt": full.marketFF.vtt,
                    "ohlc": [
                        {
                            "interval": o.interval,
                            "open": o.open,
                            "high": o.high,
                            "low": o.low,
                            "close": o.close,
                            "vol": o.vol,
                            "ts": o.ts,
                        }
                        for o in full.marketFF.marketOHLC.ohlc
                    ],
                    "bid_ask": [
                        {"bidP": q.bidP, "bidQ": q.bidQ, "askP": q.askP, "askQ": q.askQ}
                        for q in full.marketFF.marketLevel.bidAskQuote
                    ],
                }
            elif full.HasField("indexFF"):
                entry["index_ff"] = {"ltpc": {"ltp": full.indexFF.ltpc.ltp}}
        if feed.HasField("firstLevelWithGreeks"):
            entry["first_level"] = {
                "ltpc": {"ltp": feed.firstLevelWithGreeks.ltpc.ltp},
                "bidP": feed.firstLevelWithGreeks.firstDepth.bidP,
                "askP": feed.firstLevelWithGreeks.firstDepth.askP,
            }
        snapshot["feeds"][instrument_key] = entry

    return DecodedFeed(
        feed_type=feed_type,
        current_timestamp_ms=response.currentTs,
        instrument_keys=tuple(response.feeds.keys()),
        snapshot=snapshot,
        market_status=status,
    )
