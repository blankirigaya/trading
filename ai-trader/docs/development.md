# Development

Run `make test`, `make lint`, and `make typecheck` before submitting changes. Prompts live in
`src/ai_trader/ai/prompts`, versioned by filename and referenced in journal metadata. Keep API
routes thin; business flows belong in `application/workflows`, and `application/container.py` is
the only composition root.

## Providers

`MARKET_PROVIDER` selects the market data provider (`fyers` default, `upstox` retained). Both are
confined to `integrations/`. Adding a provider means implementing feed assembly and a normalizer,
then registering it in `shadow_run.probe_configured_market`. Never let provider types cross into
`domain` or `application`.

## FYERS protocol coupling

`integrations/fyers/protocol.py` mirrors an undocumented upstream binary protocol. When you change
it, regenerate the fixtures and re-check the reference vectors:

```bash
uv run python tests/fixtures/build_fyers_fixtures.py
```

`src/ai_trader/integrations/fyers/README.md` records the framing, flow control, and known limits.

## Remote AI Node

This repository deploys only to the trading node. It is an HTTP client for the configured
OpenAI-compatible endpoint and must never install models, runtimes, CUDA, or GPU tooling.
Persisted provenance stores only `hostname[:port]`; keep `sanitize_server_identifier` the only path
from a configured URL to stored metadata.

## Health Reporting

`GET /system/status` serves cached health and never performs an inference request. Health changes
only on real traffic.

## Session Behaviour

Ingestion and review are separate tasks. Do not await inference inside the socket read path. When
adding anything to the socket loop, keep it decode-only.

## Integration Testing

Real acceptance requires live FYERS credentials and a reachable AI node. Without them, tests must
use simulated transports and must be described as wiring verification, never as a real run. Never
weaken a fail-closed path to make a test pass.
