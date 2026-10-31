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
- The repository contained no local model loader: `ai/local_model.py` was already only a
  compatibility alias for the remote OpenAI-compatible provider. Bedrock is therefore an
  additional `AIProvider` implementation, not a replacement for local weights.
- `AI_PROVIDER=remote_openai_compatible` remains the default. `AI_PROVIDER=bedrock` selects
  Bedrock Runtime explicitly; there is no silent fallback between AI backends.
- Migrations declare schemas explicitly rather than reflecting ORM metadata.
- AI schema failures are retried up to `AI_SCHEMA_MAX_RETRIES`; network failures are not retried as
  schema faults.
- Allocations that cannot be converted to whole shares within tolerance are refused and returned
  to the AI rather than silently resized.
- Paper broker rejections, such as selling an unheld position, are journalled as failures instead
  of raising out of the workflow.
- Private model reasoning is never requested or persisted. Only thesis, evidence, summary,
  conditions-that-change-thesis, and confidence are stored.
- Reviewers are sharded (`AI_REVIEW_SHARDS`, default 1 preserves the legacy single reviewer).
  Each shard owns a disjoint slice of the watchlist and its own paper book (capital split
  evenly), so parallel reviewers never contend on money. Shards share the feed, journal,
  news pipeline, and kill switch. The UI aggregates all shard books into one combined view.
- A daily Bedrock spend cap (`AI_MAX_DAILY_SPEND_USD`, unset by default) pauses cadence and
  news reviews for the rest of the IST day once estimated spend crosses it. Positions stay
  open and the intraday square-off still runs. Manual reviews bypass the cap.
- Manager prompt v10 adds exit-economics guidance (suggestion, not a rule): prefer reducing/closing a profitable position whose momentum is fading, but avoid churning for a few rupees � act when the gain at risk clearly exceeds ~100 INR plus round-trip costs (paper book charges no fee; live would pay brokerage/STT/charges). Prompt changes ship as new versions (v10 file; container/fyers_session/shadow_run bumped v9?v10).
- Bots page recent-decisions lists trade actions only (OPEN/INCREASE/REDUCE/CLOSE on the shard's symbols). HOLD/STAY_IN_CASH reviews are journaled under the shard's primary tick and flooded every bot's list with the same repeated symbol, so the API filters them out; the UI empty-state covers bots with no trades yet.
- Supervisor no longer counts startup time as a crash signal (startup alone takes ~20s) and adopts an already-listening backend instead of killing it, so relaunching the watchdog never wipes live paper books. Crash-loop guard now keys off listener uptime (<2 min served, 5 in a row).
- Feed idle watchdog: a connected-but-silent FYERS socket trips no reconnect, so the session now forces one when no datafeed frame arrives for FEED_IDLE_RECONNECT_SECONDS (90s) during NSE hours (09:15-15:30 IST, Mon-Fri), at most one prod per FEED_RECONNECT_COOLDOWN_SECONDS (300s). Overnight/weekend silence never triggers. /market/status exposes feed_idle_seconds and socket_reconnects so stalls are visible without guessing. Deploys on next backend restart (in working tree; not restarted while positions are open).
- Supervisor hardened after it murdered two healthy backends: a single WMI listener miss (common during network flaps) used to trigger an immediate kill+restart. Now restarts require three consecutive misses ~5s apart, only one watchdog runs at a time (PID lock file with heartbeat; CIM data proved too stale to trust), and the dead backend's log is preserved as backend.log.prev before overwrite.
- DB I/O moved off the event loop + bounded journal reads: sync SQLAlchemy ran inline in async handlers, so every UI poll materialized the whole journal (1567 rows, 7.8MB on /decisions) inside the loop; health checks took 2.6s on a healthy backend. SqlDecisionJournal.save/get/list_recent now run in asyncio.to_thread, and list_recent takes an optional limit (/bots 200, /trades 500, /decisions ?limit= default 300; /performance and spend-cap keep full scans). Parks until next backend restart (positions open).
- Watchlist 31 -> 50 NSE symbols (rest of Nifty 50 large caps); shards rebalance to 17/17/16 round-robin automatically. Deterministic screener (screening/screener.py) ranks each shard board per review on gap/volume-surge/range-position/range-expansion with fixed weights; top 5 starred in the prompt board block, AI may overrule with written reason. Manager prompt v11 replaces the cheap-stock bias with expected-INR-gain ranking (price only a tiebreaker).
- Dev workbench D:/trading-dev (dark): full copy, own .venv/node_modules/fresh DB, backend :8001 + UI :5174, CORS+CSP opened for :5174, ensure_journal_tables() so fresh DBs self-create journal tables, parameterized supervisor script (manual boots only, never launched). Verified end-to-end (v11 reviews journaled, 3 positions opened, UI live against :8001), then shut down.
- Permanent paper-trade ledger (promoted from dev to live): paper_fills (append-only, every fill with decision + cash-after), paper_positions + paper_accounts (open book, restored on attach so restarts resume instead of flattening), paper_closed_trades (each SELL books its realized rupee P&L with entry/exit linkage). PaperBroker persists on every fill via to_thread; /paper/reset clears the open book but keeps history; GET /trades/history exposes closed trades + totals. Trades page shows a permanent Closed Trades card.
- Capital-cap override: a persisted `capital_cap_disabled` control (System page toggle, POST /control/capital-cap) lifts MAX_AI_CAPITAL_INR, MAX_SINGLE_POSITION_INR, and MAX_GROSS_EXPOSURE_INR for new buys only; paper cash and MAX_OPEN_POSITIONS remain the limits. The override applies immediately to every shard envelope and re-renders the prompt limits, survives restarts (runtime_settings), and never blocks an exit even after caps are restored. Prompt v11 now tells the AI to size by its own conviction instead of always buying the maximum affordable quantity.
- Review cadence 60s to 20s (PORTFOLIO_REVIEW_INTERVAL_SECONDS default plus live .env): roughly 3x AI reviews and Bedrock spend; MAX_ORDERS_PER_MINUTE still bounds bursts and the daily AI spend cap still pauses reviews when set. Applies on backend restart because the session interval is fixed at build. Reverted to 60s same day per operator: 20s tripled inference cost without evidence it improves decisiveness.
- Position-count override: a persisted `position_count_cap_disabled` control (System page toggle, POST /control/position-cap, status field plus max_open_positions) lifts MAX_OPEN_POSITIONS for new symbols only; adding to a held symbol was never gated. Paper cash remains the real limit, applies immediately per shard, survives restarts, never blocks exits.
- Cadence model split (Bedrock): routine shard reviews use `BEDROCK_CADENCE_MODEL_ID` when set (`build_ai_provider(..., cadence=True)`), news analysis keeps the main model, spend accounting uses per-role rates with fallback to the main rates. Live: Nova Micro via `apac.amazon.nova-micro-v1:0` (direct model ID rejected in ap-south-1, needs the APAC inference profile; verified invocable on this AWS key) at $0.035/$0.14 per MTok vs Sonnet $3/$15. Roughly 100x cheaper per review; watch schema-adherence and decision quality in paper before trusting it.
- Master universe 224 to 274 to 320: two batches of popular sub-200 names (96 total, all verified against FYERS symbol-token; first-choice misses replaced with verified alternates). Board size UNIVERSE_SIZE 50 to 100 so the cheap board holds ~100 low-cost names per cycle; prompts stay one-line-per-symbol so input stays inside the 32k budget, shards split ~33 each. Applies on backend restart; Dashboard/System counts render dynamically.
- Tier-1 news desks: CNBC-TV18 Markets, NDTV Profit (feedburner) and Business Today wired as SECONDARY sources (verified live; Moneycontrol excluded — hard HTTP 403 bot-block, no UA spoofing). Each has a persisted kill switch, no restart needed: POST /control/tier1-news (ingestion), POST /control/news-analysis (Sonnet analysis gate; off = headlines ingest but nothing is analysed or triggers reviews), POST /control/sonnet-escalation (NEWS reviews on Sonnet vs cadence model, bounded by AI_MAX_SONNET_ESCALATIONS_PER_DAY=20, IST-day counter). Dual Sonnet workflow per shard shares book/safety/execution/journal; journal provider metadata shows which model ran each review.
