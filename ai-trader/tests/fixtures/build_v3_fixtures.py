"""Generate serialized Upstox V3 protobuf fixtures for decoder/normalizer tests.

Run: uv run python tests/fixtures/build_v3_fixtures.py
Fixtures are committed as .bin files so tests never regenerate them.
"""

from pathlib import Path

from ai_trader.integrations.upstox.protobuf import MarketDataFeed_pb2 as pb

INSTRUMENT_KEY = "NSE_EQ|INE009A01021"
HERE = Path(__file__).parent


def market_info() -> bytes:
    response = pb.FeedResponse()
    response.type = pb.market_info
    response.currentTs = 1758000000000
    info = response.marketInfo
    info.segmentStatus["NSE"] = pb.NORMAL_OPEN
    info.segmentStatus["BSE"] = pb.CLOSING_END
    info.casMarketStatus["NSE_EQ"].status = "OPEN"
    info.casMarketStatus["NSE_EQ"].updatedTime = 1758000000000
    info.preOpenSessionStatus["NSE"].status = "COMPLETED"
    return response.SerializeToString()


def initial_snapshot() -> bytes:
    response = pb.FeedResponse()
    response.type = pb.initial_feed
    response.currentTs = 1758000001000
    feed = response.feeds[INSTRUMENT_KEY]
    feed.requestMode = pb.full_d5
    feed.ltpc.ltp = 1500.25
    feed.ltpc.cp = 1490.10
    feed.ltpc.ltt = 1758000000500
    feed.ltpc.ltq = 42
    feed.ltpc.iep.value = 1500.25

    full = feed.fullFeed.marketFF
    full.ltpc.ltp = 1500.25
    full.ltpc.cp = 1490.10
    full.atp = 1502.00
    full.vtt = 1250000
    quote = full.marketLevel.bidAskQuote.add()
    quote.bidP, quote.bidQ, quote.askP, quote.askQ = 1500.20, 120, 1500.30, 95
    for interval, o, h, low, c, vol in (
        ("1d", 1490.10, 1510.00, 1485.00, 1500.25, 1250000),
        ("5d", 1498.00, 1505.00, 1495.00, 1500.25, 240000),
    ):
        bar = full.marketOHLC.ohlc.add()
        bar.interval, bar.open, bar.high, bar.low, bar.close = interval, o, h, low, c
        bar.vol, bar.ts = vol, 1758000001000
    return response.SerializeToString()


def live_update() -> bytes:
    response = pb.FeedResponse()
    response.type = pb.live_feed
    response.currentTs = 1758000060000
    feed = response.feeds[INSTRUMENT_KEY]
    feed.requestMode = pb.full_d5
    full = feed.fullFeed.marketFF
    full.ltpc.ltp = 1501.80
    full.ltpc.cp = 1490.10
    full.ltpc.ltt = 1758000060000
    full.ltpc.ltq = 7
    full.atp = 1502.10
    full.vtt = 1260000
    quote = full.marketLevel.bidAskQuote.add()
    quote.bidP, quote.bidQ, quote.askP, quote.askQ = 1501.75, 60, 1501.85, 55
    bar = full.marketOHLC.ohlc.add()
    bar.interval = "1d"
    bar.open, bar.high, bar.low, bar.close = 1490.10, 1502.10, 1485.00, 1501.80
    bar.vol, bar.ts = 1260000, 1758000060000
    return response.SerializeToString()


def main() -> None:
    for name, payload in (
        ("v3_market_info.bin", market_info()),
        ("v3_initial_snapshot.bin", initial_snapshot()),
        ("v3_live_update.bin", live_update()),
    ):
        (HERE / name).write_bytes(payload)
        print(f"wrote {name} ({len(payload)} bytes)")


if __name__ == "__main__":
    main()
