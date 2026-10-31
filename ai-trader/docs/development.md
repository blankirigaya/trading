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

This repository deploys only to the trading node. With the default provider, it is an HTTP
client for the configured OpenAI-compatible endpoint and must never install models,
runtimes, CUDA, or GPU tooling. Persisted provenance stores only `hostname[:port]`; keep
`sanitize_server_identifier` the only path from a configured URL to stored metadata.

## Amazon Bedrock

`AI_PROVIDER=bedrock` selects `BedrockAIProvider`, which calls Bedrock Runtime's Converse
API with forced tool use. The response tool schema is the unmodified Pydantic schema for
`PortfolioDecision`, `CriticAssessment`, or `NewsAnalysisResponse`; the tool result is then
validated with the same local Pydantic model. AWS credentials come only from the standard
boto3 chain. Prefer an IAM role or temporary session credentials and never commit them.

Required setup:

```env
AI_PROVIDER=bedrock
BEDROCK_REGION=ap-south-1
BEDROCK_MODEL_ID=apac.amazon.nova-pro-v1:0
```

The default model is the APAC Nova Pro inference profile because it supports Converse tool
use and keeps Mumbai-originated requests within APAC. Confirm the exact model, endpoint,
marketplace prerequisites, billing, and account entitlement in AWS before production use.

Bedrock behavior is intentionally boring for the rest of the system:

- Transport failures raise `BedrockAIUnavailable`, a subclass of `RemoteAIUnavailable`, so
  all existing fail-closed workflow paths are reused.
- Only retryable AWS/network errors are retried, with bounded exponential backoff and
  jitter. Authentication, permission, model-access, validation, timeout-budget, and
  malformed-response failures stop immediately.
- Malformed model output is validated by Pydantic and retried by the existing
  schema-retry wrapper when retries remain.
- Input prompts are truncated to the approximate `BEDROCK_MAX_INPUT_TOKENS` budget and
  cannot block market ingestion because the shared single-inference semaphore is retained.
- Token usage, latency, model, region, and calculated cost (only when both token rates are
  configured) are recorded in provider metadata. Prompts and credentials are never logged.

Use `uv run python -m ai_trader ai test` for a live connectivity check. Ordinary unit tests
must use the injected fake Bedrock client and must not require AWS credentials. Do not run
live Bedrock tests casually because they can incur AWS charges.

## Health Reporting

`GET /system/status` serves cached health and never performs an inference request. Health changes
only on real traffic.

## Session Behaviour

Ingestion and review are separate tasks. Do not await inference inside the socket read path. When
adding anything to the socket loop, keep it decode-only.

## Bedrock troubleshooting

- `BEDROCK_MODEL_ID must be configured`: the setting is blank. Set an entitled,
  Converse-compatible model or inference-profile ID.
- Missing AWS credentials: use `AWS_PROFILE`, an IAM role, or temporary `AWS_ACCESS_KEY_ID`,
  `AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN` values. Do not commit these values.
- Missing region: set `BEDROCK_REGION` or rely on `AWS_REGION`/shared AWS configuration.
- Access denied or model unavailable: verify `bedrock:InvokeModel` permission for the exact
  model or inference-profile ARN, marketplace prerequisites, payment method, and any
  provider use-case approval. The provider reports the safe error category without echoing
  AWS identifiers or secrets.
- Throttling or timeouts: Bedrock retries only bounded transient errors. Repeated failures
  leave the trading workflow failed-closed; reduce call frequency, request a quota increase,
  or investigate the model/region before rerunning.
- Malformed output: Pydantic rejects the response and the normal schema-retry policy applies.
  Do not treat a refused or truncated model response as a buy, sell, hold, or news signal.

## Integration Testing

Real acceptance requires live FYERS credentials and a reachable AI node. Without them, tests must
use simulated transports and must be described as wiring verification, never as a real run. Never
weaken a fail-closed path to make a test pass.
