/**
 * Types mirroring the FastAPI backend responses.
 *
 * These describe data the backend already produces. This UI never derives trading
 * intent from them: it only displays them.
 */

export type ProviderState = 'CONNECTED' | 'DEGRADED' | 'OFFLINE' | 'UNKNOWN'

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

export interface PortfolioState {
  cash: string
  reserved_cash: string
  positions: Position[]
  realized_pnl: string
  unrealized_pnl: string
}

export interface TradeProposal {
  proposal_id: string
  symbol: string | null
  exchange: string
  segment: string
  action: string
  side: string | null
  capital_allocation_inr: string | null
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
  decision_id: string
  ai_action: string | null
  thesis: string | null
}

export interface PerformanceSummary {
  total_pnl: string
  realized_pnl: string
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
