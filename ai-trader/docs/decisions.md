# Decisions

- FYERS is the market data provider. `PaperBroker` is the paper execution provider. The runtime
  mode remains `LIVE_SHADOW`. This is not FYERS-hosted paper trading.
- FYERS is never asked to execute. `FyersBroker` exists solely so the invariant is enforced and
  testable: every method raises `FyersTradingDisabled`.
- The Upstox integration is retained but removed from the active runtime path. `MARKET_PROVIDER`
  selects between them.
- The FYERS V3 data socket is implemented directly as a binary protocol rather than through the
  vendor SDK. FYERS publishes no packet specification, so the field ordering follows the reference
  `fyers_apiv3` SDK's `map.json` and is verified against known vectors offline. This is coupled
  upstream code and is not yet validated against a live feed.
- The client honours the server's `ack_count` flow control by echoing the latest message number.
  Without it the server stops emitting updates.
- The AI owns all market judgment, including entries and exits. There is no automatic exit on a
  technical threshold and no formula-based position sizing; the execution layer only converts whole
  shares mechanically.
- Review cadence controls only *when* the AI thinks, never *what* it decides.
- A `HOLD` or `STAY_IN_CASH` result is a successful decision. No prompt is tuned to manufacture an
  order.
- Paper positions are marked to real FYERS prices after every review, so unrealized P&L reflects
  the live market even though fills are simulated.
- Remote AI or Jev unavailability, malformed output, and network errors fail closed: the decision
  is journalled as `FAILED` with a reason and no order is placed. There is no mock fallback in the
  acceptance path.
- The trading node is an HTTP client for the AI node and never installs, runs, or benchmarks a
  model. Persisted AI provenance is restricted to `hostname[:port]`.
- Migrations declare schemas explicitly rather than reflecting ORM metadata.
- AI schema failures are retried up to `AI_SCHEMA_MAX_RETRIES`; network failures are not retried as
  schema faults.
- Allocations that cannot be converted to whole shares within tolerance are refused and returned
  to the AI rather than silently resized.
- Paper broker rejections, such as selling an unheld position, are journalled as failures instead
  of raising out of the workflow.
- Private model reasoning is never requested or persisted. Only thesis, evidence, summary,
  conditions-that-change-thesis, and confidence are stored.
