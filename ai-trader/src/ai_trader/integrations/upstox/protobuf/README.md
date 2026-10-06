# Upstox Market Data Feed V3 protobuf schema

This directory contains the **official, unmodified** Upstox Market Data Feed V3 protobuf schema and
its generated Python module. It is used only inside `integrations/upstox`; domain and application
layers never import these objects.

## Provenance

| Item | Value |
| --- | --- |
| Schema source | `https://assets.upstox.com/feed/market-data-feed/v3/MarketDataFeed.proto` |
| Retrieved (UTC) | 2026-10-01T17:01:55Z |
| Upstream `Last-Modified` | Fri, 04 Sep 2026 15:24:42 GMT |
| SHA-256 | `3e1c939dce2c83a3fef91405c5ca268dfa089aae973319f56685f12a5afcf612` |
| Protobuf package | `com.upstox.marketdatafeederv3udapi.rpc.proto` |
| Generation command | `uv run python -m grpc_tools.protoc -I"src/ai_trader/integrations/upstox/protobuf" --python_out="src/ai_trader/integrations/upstox/protobuf" "src/ai_trader/integrations/upstox/protobuf/MarketDataFeed.proto"` |
| Compiler | `grpcio-tools 1.84.0` (`grpc_tools.protoc`, protoc 31.x equivalent) |
| Runtime | `protobuf 7.36.2` |

`MarketDataFeed_pb2.py` is committed so the trading node does **not** require `protoc` at runtime.
`grpcio-tools` is a dev-only dependency used solely for regeneration.

## Regenerating

```bash
uv sync
uv run python -m grpc_tools.protoc -I"src/ai_trader/integrations/upstox/protobuf" --python_out="src/ai_trader/integrations/upstox/protobuf" "src/ai_trader/integrations/upstox/protobuf/MarketDataFeed.proto"
```

Re-download the `.proto` from the schema source above and record the new retrieval date and SHA-256
whenever the upstream feed schema changes.

## Schema notes

`FeedResponse` carries an enum `Type` of `initial_feed`, `live_feed`, or `market_info`, a
`feeds` map keyed by instrument key, `currentTs` in milliseconds, and a `MarketInfo` block holding
per-segment `MarketStatus` plus CAS and pre-open status. This is Feed V3 only; the discontinued
older feed is not implemented.
