/**
 * Types mirroring the FastAPI backend responses.
 *
 * These describe data the backend already produces. This UI never derives trading
 * intent from them: it only displays them.
 */

export type ProviderState = 'CONNECTED' | 'DEGRADED' | 'OFFLINE' | 'UNKNOWN'

/** Which price bucket the AI reviews. Persisted on the backend. */
export type UniverseMode = 'cheap' | 'mixed' | 'expensive'

export type EventCategory =
  | 'MARKET'
  | 'AI'
  | 'JEV'
  | 'SAFETY'
  | 'PAPER'
  | 'SYSTEM'
  | 'ERROR'
  | 'JOURNAL'

export interface SystemStatus {
  mode: string
  environment: string
  trading_enabled: boolean
  market_provider: string
  market_connected: boolean
  execution_provider: string
  execution_broker: string
  live_order_execution: boolean
  fyers_market_data_only: boolean
  ai_provider: string
  ai_server: string | null
  ai_model: string
  ai_inference_location: string
  jev_enabled: boolean
  jev_model: string | null
  kill_switch_state: boolean
  session_running: boolean
  shadow_symbols: string[]
  /** Auto-classified review universe (defaults to cheap when absent). */
  universe_mode?: UniverseMode
  universe_size?: number
  universe_master_size?: number
  universe_selected?: string[]
  /** True when the operator lifted the fixed AI capital / exposure caps. */
  capital_cap_disabled?: boolean
  /** True when the operator lifted the max-open-positions cap. */
  position_count_cap_disabled?: boolean
  /** False when the operator killed the Tier-1 news desks. */
  tier1_news_enabled?: boolean
  /** False when the operator killed AI analysis of news items. */
  news_analysis_enabled?: boolean
  /** False when the operator killed Sonnet escalation of news reviews. */
  sonnet_escalation_enabled?: boolean
  /** Sonnet news reviews used today (IST). */
  sonnet_escalations_today?: number
  /** Daily cap for Sonnet-escalated news reviews. */
  ai_max_sonnet_escalations_per_day?: number
  max_ai_capital_inr?: string
  max_single_position_inr?: string
  max_gross_exposure_inr?: string
  max_open_positions?: number
  shadow_initial_capital_inr: string
  portfolio_review_interval_seconds: number
  database: string
  news_enabled: boolean
  ai_server_reachable: boolean | null
  last_ai_success_at: string | null
  last_ai_failure_at: string | null
  last_ai_error: string | null
}

export interface Position {
  symbol: string
  quantity: number
  average_entry_price: string
  realized_pnl: string
}

/** Active paper charge schedule, served so the UI can estimate exit costs with
 * the exact rates the broker books — never a hardcoded copy that can drift. */
export interface ChargeSchedule {
  enabled: boolean
  rates: {
    brokerage_pct: string
    brokerage_cap_inr: string
    stt_sell_pct: string
    exchange_pct: string
    sebi_pct: string
    stamp_duty_buy_pct: string
    gst_pct: string
  } | null
}

export interface PortfolioState {
  cash: string
  reserved_cash: string
  positions: Position[]
  realized_pnl: string
  unrealized_pnl: string
  /** Net worth from the backend: cash + blocked margin + unrealized P&L. */
  equity?: string
  /** Total brokerage + statutory charges booked against closed trades. */
  charges_paid?: string
  /** Realized P&L before charges (realized_pnl + charges_paid). */
  gross_realized_pnl?: string
}

export interface TradeProposal {
  proposal_id: string
  symbol: string | null
  exchange: string
  segment: string
  action: string
  side: string | null
  capital_allocation_inr: string | null
  quantity_shares: number | null
  desired_entry_price: string | null
  order_type: string
  invalidation_price: string | null
  target_price: string | null
  expected_holding_minutes: number | null
  confidence: number
  thesis: string
  evidence: string[]
  conditions_that_change_thesis: string[]
}

export interface PortfolioDecision {
  cash_percent: number
  allocations: { symbol: string; allocation_percent: number; thesis: string; confidence: number }[]
  proposals: TradeProposal[]
  market_summary: string
  decision_summary: string
}

export interface ProviderMetadata {
  provider: string
  model: string
  request_id: string | null
  prompt_name: string
  prompt_version: string
  latency_ms: number | null
  input_tokens: number | null
  output_tokens: number | null
  temperature: number | null
  seed: number | null
  schema_version: string
  provider_metadata: Record<string, unknown>
}

export interface CriticAssessment {
  score: number
  agrees: boolean
  disagreement_signals: string[]
  critique: string
  bounded_decisions: string[]
}

export interface FinalAIDecision {
  decision_id: string
  correlation_id: string
  approved: boolean
  decision_summary: string
  proposals: TradeProposal[]
}

export interface PaperOrder {
  broker_order_id: string
  status: string
  created_at: string
  request: {
    client_order_id: string
    decision_id: string
    correlation_id: string
    symbol: string
    side: string
    order_type: string
    quantity: number
    limit_price: string | null
    requested_allocation: string
    actual_order_value: string
    conversion_reason: string
  }
}

export interface DecisionRecord {
  decision_id: string
  correlation_id: string
  event_id: string
  timestamp: string
  status: string
  input_context_hash: string
  initial_ai_proposal: PortfolioDecision
  initial_provider_metadata: ProviderMetadata
  jev_critique: CriticAssessment | null
  jev_provider_metadata: ProviderMetadata | null
  final_ai_decision: FinalAIDecision | null
  portfolio_before: PortfolioState
  portfolio_after: PortfolioState | null
  orders_generated: PaperOrder[]
  market_snapshot_id: string | null
  market_timestamp: string | null
  instrument_key: string | null
  symbol: string | null
  reconsideration_requested: boolean
  reconsideration_performed: boolean
  safety_allowed: boolean | null
  safety_reason: string | null
  failure_reason: string | null
}

export interface MarketStatus {
  provider: string
  connected: boolean
  symbols: string[]
  feed_mode: string
  fyers_market_data_only: boolean
  feed_idle_seconds: number | null
  socket_reconnects: number | null
  /** Age of the newest snapshot the API serves; null before the first tick. */
  snapshot_age_seconds: number | null
  /** True during the NSE session, when stale prices are a fault rather than closure. */
  market_open: boolean
}

export interface MarketSnapshot {
  snapshot_id: string
  instrument: {
    instrument_key: string
    symbol: string
    exchange: string
    segment: string
  }
  timestamp: string
  last_price: string
  previous_close: string | null
  bid: string | null
  ask: string | null
  recent_ticks: {
    symbol: string
    last_price: string
    exchange_timestamp: string
    received_timestamp: string
  }[]
  current_1m_candle: Candle | null
  recent_1m_candles: Candle[]
}

export interface Candle {
  start_timestamp: string
  end_timestamp: string
  open: string
  high: string
  low: string
  close: string
  volume: number | null
}

export interface TradeRow {
  trade_id: string
  execution_provider: string
  is_paper: boolean
  time: string
  symbol: string
  side: string
  quantity: number
  entry_price: string
  requested_allocation: string
  actual_order_value: string
  status: string
  /** Charges paid on this fill; P&L is net of these. */
  fees: string
  /** Entry-side charges released by this closing fill (0 for buys). */
  entry_fees: string
  charge_breakdown: Record<string, string>
  decision_id: string
  ai_action: string | null
  thesis: string | null
}

export interface ClosedTradeRow {
  closed_trade_id: string
  shard: number
  symbol: string
  quantity: number
  entry_price: string
  exit_price: string
  realized_pnl: string
  /** Charges for the round trip; realized_pnl is already net of them. */
  charges: string
  /** Realized P&L before charges. */
  gross_pnl: string
  opened_at: string | null
  closed_at: string
  entry_decision_id: string | null
  exit_decision_id: string
}

export interface TradeHistoryResponse {
  closed_trades: ClosedTradeRow[]
  total_realized_pnl: string
  total_charges: string
  gross_realized_pnl: string
  wins: number
  losses: number
}

export interface AiUsageBucket {
  calls: number
  input_tokens: number
  output_tokens: number
  estimated_cost_usd: number | null
}

export interface AiUsageSummary {
  calls: number
  input_tokens: number
  output_tokens: number
  estimated_cost_usd: number | null
  cost_configured: boolean
  by_model: Record<string, AiUsageBucket>
  last_24h: AiUsageBucket
  sources: Record<string, AiUsageBucket>
}

export interface BotPosition {
  symbol: string
  quantity: number
  average_entry_price: string
  ltp: string
  value: string
  unrealized_pnl: string
}

export interface BotDecision {
  decision_id: string
  symbol: string | null
  status: string
  timestamp: string
  ai_action: string | null
}

export interface TradingBot {
  index: number
  name: string
  symbols: string[]
  cash: string
  positions: BotPosition[]
  reviews: number
  ai_model: string
  recent_decisions: BotDecision[]
}

export interface PerformanceSummary {
  total_pnl: string
  realized_pnl: string
  gross_realized_pnl: string
  charges_paid: string
  unrealized_pnl: string
  cash: string
  open_positions: number
  total_decisions: number
  executed: number
  failed: number
  denied_by_safety: number
  actions: Record<string, number>
  jev_approvals: number
  jev_challenges: number
  ai_changed_after_jev: number
  ai_retained_after_jev: number
  avg_ai_latency_ms: number | null
  avg_jev_latency_ms: number | null
  outcome_measurements: unknown[]
  ai_usage: AiUsageSummary | null
}

export interface TimelineEvent {
  seq?: number
  timestamp: string
  category: EventCategory
  kind: string
  message: string
  decision_id: string | null
  correlation_id: string | null
  symbol: string | null
  data: Record<string, unknown>
}
