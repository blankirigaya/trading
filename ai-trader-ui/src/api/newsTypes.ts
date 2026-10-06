/** News types mirroring the backend. Source material and AI analysis are separate. */

export type SourceType = 'OFFICIAL' | 'COMPANY' | 'NEWS_MEDIA' | 'AGGREGATOR'
export type TrustLevel = 'PRIMARY' | 'HIGH' | 'SECONDARY' | 'UNKNOWN'
export type Materiality = 'LOW' | 'MEDIUM' | 'HIGH' | 'VERY_HIGH' | 'UNCLEAR'
export type Direction = 'POSITIVE' | 'NEGATIVE' | 'MIXED' | 'NEUTRAL' | 'UNCLEAR'
export type Horizon =
  | 'MINUTES'
  | 'INTRADAY'
  | 'DAYS'
  | 'WEEKS'
  | 'LONG_TERM'
  | 'UNCLEAR'

export interface NewsSourceBlock {
  item_id: string
  source_name: string
  source_type: SourceType
  trust_level: TrustLevel
  is_official: boolean
  headline: string
  canonical_url: string
  url: string
  published_at: string
  received_at: string
  category: string
  symbols: string[]
  excerpt: string | null
  content_hash: string
  cluster_id: string | null
  related_source_count: number
}

export interface NewsAnalysisBlock {
  analysis_id: string
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'SKIPPED'
  event_type: string | null
  summary: string | null
  affected_symbols: string[]
  materiality: Materiality | null
  direction: Direction | null
  horizon: Horizon | null
  confidence: number | null
  requires_portfolio_review: boolean
  evidence: string[]
  uncertainties: string[]
  analysis_started_at: string | null
  analysis_completed_at: string | null
  portfolio_review_at: string | null
  failure_reason: string | null
  model: string | null
  latency_ms: number | null
}

export interface NewsRow {
  source: NewsSourceBlock
  ai_analysis: NewsAnalysisBlock | null
  review_triggered: boolean
}

export interface NewsSourceConfig {
  source_id: string
  name: string
  homepage: string
  source_type: SourceType
  trust_level: TrustLevel
  default_category: string
  enabled: boolean
  notes: string | null
}

/** Per-source ingestion health. A session that is not running reports STOPPED. */
export type ProviderStatus = 'LIVE' | 'DEGRADED' | 'OFFLINE' | 'STARTING' | 'STOPPED'

export interface NewsProviderStatus {
  source_id: string
  name?: string
  source_type?: SourceType
  trust_level?: TrustLevel
  enabled?: boolean
  poll_interval_seconds: number | null
  status: ProviderStatus
  last_item_at: string | null
  seconds_since_last_item: number | null
  last_error: string | null
  consecutive_failures: number
}

export interface NewsProvidersResponse {
  ingestion_running: boolean
  providers: NewsProviderStatus[]
  queue: { depth: number; capacity: number; max_concurrent_analyses: number }
  pipeline: {
    enqueued: number
    dropped_queue_full: number
    analysed: number
    analysis_failed: number
    reviews_requested: number
    reviews_suppressed_duplicate: number
  }
}

/** One measured news-to-trade chain. Null means the stage has not happened yet. */
export interface NewsLatencyRow {
  item_id: string
  analysis_id: string
  source_id: string
  symbols: string[]
  source_to_ingestion_ms: number | null
  ingestion_to_analysis_ms: number | null
  analysis_to_decision_ms: number | null
  news_to_trade_ms: number | null
  review_latency_ms: number | null
  published_at: string
  received_at: string
  analysis_started_at: string | null
  analysis_completed_at: string | null
  portfolio_review_started_at: string | null
  portfolio_decision_at: string | null
  paper_order_at: string | null
}

export interface NewsLatencyResponse {
  rows: NewsLatencyRow[]
  count: number
  news_to_trade_ms: {
    samples: number
    min_ms: number | null
    median_ms: number | null
    max_ms: number | null
  }
}
