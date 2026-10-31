# AI Trader

An AI-judged, NSE equity paper-trading system. FYERS supplies real market data; the AI makes the
trading decisions; `PaperBroker` simulates every execution. There is no trading strategy, no
indicator logic, and no live-money order capability.

## Terminology

| Term | Meaning |
| --- | --- |
| **Market data provider** | FYERS. Real NSE prices over the FYERS V3 Data WebSocket. |
| **Paper execution provider** | `PaperBroker` inside this application. Simulated money only. |
| `LIVE_SHADOW` / `FYERS_SHADOW` | Runtime mode: real prices, AI decisions, simulated execution. |

This is **not** FYERS-hosted paper trading. FYERS is never asked to execute an order.

## The Real Path

```text
REAL FYERS prices -> MarketState -> remote AI proposal -> Jev critique
  -> optional remote AI reconsideration -> final decision -> SafetyEnvelope
  -> PaperBroker -> SQL journal
```

Every decision persists `initial_ai_proposal`, `jev_critique`, and `final_ai_decision` separately,
plus whether reconsideration was requested and performed.

## Two-Machine Architecture

**Machine A — Trading Node** runs this repository: FYERS ingestion, portfolio state, decision
orchestration, Jev orchestration, PostgreSQL, the journal, and `PaperBroker`. It never installs,
loads, launches, or benchmarks an LLM and requires no GPU.

**Machine B — AI Node** independently runs any OpenAI-compatible inference server. This repository
only makes HTTP requests. Keep Machine B on a trusted LAN or private VPN.

```env
AI_PROVIDER=remote_openai_compatible
AI_BASE_URL=http://AI_MACHINE_IP:8080/v1
AI_MODEL=<server-model-id>
AI_API_KEY=
```

## Amazon Bedrock Provider

Set `AI_PROVIDER=bedrock` to use Amazon Bedrock Runtime instead of Machine B. Bedrock is an
alternate implementation of the existing `AIProvider` interface: prompts, schemas, reviews,
risk checks, paper execution, and the journal are unchanged.

```env
AI_PROVIDER=bedrock
BEDROCK_REGION=ap-south-1
BEDROCK_MODEL_ID=apac.amazon.nova-pro-v1:0
BEDROCK_MAX_INPUT_TOKENS=32000
BEDROCK_MAX_TOKENS=1024
BEDROCK_TEMPERATURE=0
BEDROCK_TIMEOUT_SECONDS=30
BEDROCK_MAX_RETRIES=2
```

Use the standard AWS credential chain, preferably an IAM role or temporary session
credentials. Do not put AWS access keys in Git. Bedrock model access, marketplace
prerequisites, payment method, and any use-case approval must be completed in AWS before
live inference can succeed; without them, `ai test` reports an actionable failure and no
trade is made.

Validate Bedrock without running the trading loop:

```bash
uv run python -m ai_trader ai test
```

## FYERS Configuration

```env
MARKET_PROVIDER=fyers
FYERS_CLIENT_ID=
FYERS_SECRET_KEY=
FYERS_REDIRECT_URI=
FYERS_ACCESS_TOKEN=
FYERS_FEED_MODE=symbolUpdate

SHADOW_INITIAL_CAPITAL_INR=100000
SHADOW_SYMBOLS=NSE:INFY-EQ,NSE:TCS-EQ,NSE:RELIANCE-EQ
```

Symbols use FYERS' own format (`NSE:INFY-EQ`) and are resolved to internal instrument identity.
Credentials are read from the environment only, are never logged, and are redacted from `repr`.

## Commands

```bash
uv sync
uv run alembic upgrade head
uv run pytest

uv run python -m ai_trader fyers test                 # FYERS data socket probe
uv run python -m ai_trader ai test                    # remote AI probe
uv run python -m ai_trader jev test                   # Jev probe

uv run python -m ai_trader shadow --provider fyers --once   # single AI review
uv run python -m ai_trader shadow --provider fyers         # continuous session

# Diagnostics only. A shadow session already ingests and analyses news on its own.
uv run python -m ai_trader news test                       # probe every news source
uv run python -m ai_trader news poll --once                # fetch and store new items
uv run python -m ai_trader news analyze --latest           # remote AI analysis of stored news
```

Each probe fails closed and reports only what actually happened. A `HOLD` or `STAY_IN_CASH`
result is a successful autonomous decision; no prompt is tuned to manufacture an order.

## Session Behaviour

Market ingestion runs on its own task, so prices keep flowing while the remote AI is thinking.
`PORTFOLIO_REVIEW_INTERVAL_SECONDS` controls only *when* the AI thinks, never *what* it decides.
After each fill, paper positions are marked to the latest real FYERS price, so unrealized P&L
reflects the live market.

## News Intelligence

News is **context, never an instruction**. The news package has no broker dependency and
contains no order call; `tests/integration/test_realtime_news.py` asserts this by scanning
the source.

Starting one shadow session runs both the real FYERS market stream and continuous news
ingestion. `news poll` and `news analyze` are diagnostics; neither is needed during
trading.

### Flow

```text
sources -> poll -> deduplicate -> AI news analyst -> review request
        -> Portfolio AI -> Jev -> final AI -> PaperBroker
```

The chain is unbroken: only a Portfolio AI decision can reach paper execution. There is
no path from a headline to a BUY or SELL, by design and by test.

### Ingestion

`RealtimeNewsService` runs one task per source on its own interval (`NSE_POLL_SECONDS`,
`BSE_POLL_SECONDS`, `FINANCIAL_NEWS_POLL_SECONDS`, `RBI_POLL_SECONDS`, `SEBI_POLL_SECONDS`).
A failing source backs off exponentially up to `NEWS_ERROR_BACKOFF_MAX_SECONDS` and is
reported `DEGRADED`, then `OFFLINE`, in the UI. It starts with the session and stops
cleanly with it.

`PollingFeed` and `StreamingFeed` satisfy the same `NewsFeed` contract, so a future
WebSocket or webhook NSE integration plugs in without touching the analyser, the review
path, or the workflow.

### Deduplication and corrections

Deduplication runs *before* the AI queue, keyed on source id, canonical URL, content hash,
headline normalisation, and document hash. Three outlets repeating one NSE filing are one
event, not three. A publisher correction is stored as a **new version** that supersedes
the original, so both remain auditable and only the change costs an AI call.

### Backpressure

The analysis queue is bounded, and a full queue makes the news feed **wait** rather than
discard a filing. `NEWS_AI_MAX_CONCURRENT_REQUESTS` serialises requests to the single
model server. Nothing in the news path shares a queue with FYERS ingestion, so news
backpressure can never stall prices.

### Review triggers

A portfolio review is requested when the news analyst sets `requires_portfolio_review`, or
when news names a symbol the paper book currently holds so an open position can be
re-examined. The second trigger is symbol overlap, not a headline keyword; what to do
about the news remains entirely the AI's judgement, including doing nothing.

### Separation of concerns

`NewsReviewBundle` renders two clearly labelled blocks to the Portfolio Manager and Jev:
`ORIGINAL SOURCE INFORMATION` (what publishers stated) and `AI NEWS INTERPRETATION` (a
model's judgement of it, with its own confidence that is explicitly not a trading
confidence).

### Latency

`published_at`, `received_at`, `analysis_started_at`, `analysis_completed_at`,
`portfolio_review_started_at`, `portfolio_decision_at`, and `paper_order_at` are recorded
as they happen, and derived as `source_to_ingestion_ms`, `ingestion_to_analysis_ms`,
`analysis_to_decision_ms`, and `news_to_trade_ms`. The Electron News page shows them; a
stage that has not happened reads `pending`, never `0 ms`.

### Verified against live sources

LiveMint (markets, companies), Economic Times, and Business Standard RSS each return
items. The NSE, RBI, SEBI, and BSE listing pages are JavaScript-rendered and return no
dated server-rendered anchors, so those providers report `FAILED items=0` with an
explicit reason and appear `DEGRADED`/`OFFLINE` in the UI. Their undocumented JSON APIs
are deliberately not used.

If no remote AI is configured, analysis fails closed with a recorded reason. It never
fabricates an interpretation.

## Execution Isolation

`FyersBroker` exists only to make the invariant enforceable: every method raises
`FyersTradingDisabled`. No FYERS module references an order endpoint.
`ShadowReviewWorkflow` accepts `PaperBroker` concretely, so injecting a live broker is a type
error. `tests/unit/test_fyers_no_execution.py` asserts all of this.

## Paper Broker Limitations

Deterministic simulator, not a backtester: immediate fills at an injected price plus configurable
slippage, placeholder fees, no depth, latency, partial fills, corporate actions, or short selling.
Allocations that cannot be converted to whole shares within tolerance are refused rather than
silently resized.

## Documentation

- `docs/architecture.md` — components and boundaries
- `docs/development.md` — workflow and remote AI node guidance
- `docs/decisions.md` — recorded decisions
- `src/ai_trader/integrations/fyers/README.md` — FYERS wire protocol notes
- `src/ai_trader/integrations/upstox/protobuf/README.md` — retained Upstox schema provenance
