# Architecture

## FYERS_SHADOW path

```mermaid
flowchart TB
  subgraph MA["Machine A - Trading Node"]
    WS[FYERS V3 data socket] --> PB[binary protocol decode]
    PB --> NM[normalizer]
    NM --> MS[MarketState]
    MS --> PBR[PaperBroker mark-to-market]
    PBR --> WF[ShadowReviewWorkflow]
    WF --> AI[remote AI proposal]
    AI --> JV[Jev critique]
    JV -->|challenge| AI
    AI --> SE[SafetyEnvelope]
    SE --> XS[ExecutionService]
    XS --> PBO[PaperBroker only]
    WF --> DJ[SqlDecisionJournal]
    DJ --> PG[(PostgreSQL)]
  end

  subgraph MB["Configured inference service"]
    MSRV[OpenAI-compatible server or Amazon Bedrock]
  end

  MSRV -.->|structured decision| AI
  AI -.->|inference request| MSRV
  JV -.->|HTTPS| JE[TypeSafe AI Jev]
  WF -.-> FY[(FYERS market data)]
  PBO -.x FY
```

The crossed edge is the point: FYERS provides information only. Nothing flows back.

## Provider Separation

| Layer | Knows about |
| --- | --- |
| `domain` | `Instrument`, `MarketTick`, `Candle`, `MarketState`, decisions. No provider types. |
| `application` | The `MarketState` contract and `PaperBroker`. No FYERS or Upstox types. |
| `ai/providers.py` | Selects the configured `AIProvider`; workflows never instantiate a backend. |
| `integrations/fyers` | FYERS wire format and protocol constants. Contained here. |

FYERS objects never leave `integrations/fyers`. The Upstox integration is retained but is not part
of the active FYERS_SHADOW path.

## Boundaries

- **Market data only.** `FyersBroker` raises `FyersTradingDisabled` on every method. No FYERS
  module references an order endpoint.
- **Judgment separation.** The AI owns market judgment. `SafetyEnvelope` enforces only configured
  mechanical limits.
- **Execution isolation.** `ShadowReviewWorkflow` takes `PaperBroker` concretely.
- **Idempotency.** `prepare()` is side-effect free; `submit()` is idempotent by decision ID.
- **Three-stage provenance.** Initial proposal, Jev critique, and final decision stay separate,
  with explicit reconsideration flags.
- **Repository translation.** Repositories convert between SQLAlchemy rows and domain models.
- **Non-blocking ingestion.** Socket decoding never awaits a reviewer.

## Fail-Closed Policy

Missing credentials, refused connections, malformed frames, remote AI or Jev failure, schema
violations, whole-share conversion refusals, and paper broker rejections all journal a `FAILED`
decision with a `failure_reason` and place no order. A broker-level refusal such as selling an
unheld position is journalled, not raised.

## Time and Money

Timezone-aware UTC throughout; `Asia/Kolkata` is presentation only. `Decimal` for all monetary
values; integer NSE equity quantities.

## Protocol Coupling

`integrations/fyers/protocol.py` is coupled to an undocumented upstream binary protocol. Keep its
constants and fixtures together, and re-verify them when FYERS changes the feed.
