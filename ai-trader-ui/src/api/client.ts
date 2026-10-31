/**
 * REST client for the FastAPI backend.
 *
 * The backend is the only source of truth. This client performs read calls plus a
 * small set of explicitly safe control calls. It never contacts FYERS, Jev, or the
 * remote AI model directly, and it holds no credentials.
 */

import type {
  DecisionRecord,
  MarketSnapshot,
  MarketStatus,
  PerformanceSummary,
  PortfolioState,
  SystemStatus,
  TimelineEvent,
  TradeHistoryResponse,
  TradeRow,
  TradingBot,
  UniverseMode,
  Candle,
} from './types'
import type {
  NewsLatencyResponse,
  NewsProvidersResponse,
  NewsRow,
  NewsSourceConfig,
} from './newsTypes'

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://127.0.0.1:8000'

export class BackendOfflineError extends Error {
  constructor(message = 'Backend unreachable') {
    super(message)
    this.name = 'BackendOfflineError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch (cause) {
    throw new BackendOfflineError(cause instanceof Error ? cause.message : 'network error')
  }
  if (!response.ok) {
    throw new Error(`Backend responded ${response.status} for ${path}`)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

const post = <T>(path: string) => request<T>(path, { method: 'POST' })

const postWith = <T>(path: string, params: Record<string, string>) => {
  const query = new URLSearchParams(params).toString()
  return request<T>(`${path}?${query}`, { method: 'POST' })
}

export const api = {
  baseUrl: API_BASE,

  systemStatus: () => request<SystemStatus>('/system/status'),
  marketStatus: () => request<MarketStatus>('/market/status'),
  marketSnapshots: () => request<MarketSnapshot[]>('/market/snapshots'),
  marketCandles: (symbol: string) => request<Candle[]>(`/market/candles?symbol=${encodeURIComponent(symbol)}`),

  portfolio: () => request<PortfolioState>('/portfolio'),
  positions: () => request<PortfolioState['positions']>('/positions'),

  decisions: () => request<DecisionRecord[]>('/decisions'),
  decision: (id: string) => request<DecisionRecord>(`/decisions/${encodeURIComponent(id)}`),

  trades: () => request<TradeRow[]>('/trades'),

  tradeHistory: () => request<TradeHistoryResponse>('/trades/history'),
  trade: (id: string) => request<TradeRow>(`/trades/${encodeURIComponent(id)}`),

  events: (limit = 200) => request<TimelineEvent[]>(`/events/recent?limit=${limit}`),
  performance: () => request<PerformanceSummary>('/performance'),

  // One entry per AI reviewer shard: its symbols, paper book, and recent decisions.
  bots: () => request<TradingBot[]>('/bots'),

  // News is read-only here. The UI never polls or analyses news itself.
  news: (limit = 100) => request<NewsRow[]>(`/news?limit=${limit}`),
  newsSources: () => request<NewsSourceConfig[]>('/news/sources'),
  newsForSymbol: (symbol: string) =>
    request<NewsRow[]>(`/news/symbol/${encodeURIComponent(symbol.toUpperCase())}`),
  newsProviders: () => request<NewsProvidersResponse>('/news/providers'),
  newsLatency: (limit = 25) => request<NewsLatencyResponse>(`/news/latency?limit=${limit}`),

  // Safe controls only. There is deliberately no buy/sell/order endpoint.
  requestReview: () => post<{ requested: boolean; decision_id: string | null }>('/control/review'),
  startSession: () => post<{ running: boolean }>('/control/start'),
  stopSession: () => post<{ running: boolean }>('/control/stop'),
  setKillSwitch: (enabled: boolean) =>
    postWith<{ kill_switch_state: boolean; live_order_execution: boolean }>('/control/kill-switch', {
      enabled: String(enabled),
    }),
  // Persisted universe switch; the backend applies it at the next review cycle.
  setUniverseMode: (mode: UniverseMode) =>
    postWith<{ universe_mode: UniverseMode; applies: string }>('/control/universe', {
      mode,
    }),
  // Persisted override of the fixed AI money caps; applied immediately.
  setCapitalCap: (disabled: boolean) =>
    postWith<{ capital_cap_disabled: boolean; live_order_execution: boolean }>(
      '/control/capital-cap',
      { disabled: String(disabled) },
    ),
  // Persisted override of the max-open-positions cap; applied immediately.
  setPositionCap: (disabled: boolean) =>
    postWith<{ position_count_cap_disabled: boolean; live_order_execution: boolean }>(
      '/control/position-cap',
      { disabled: String(disabled) },
    ),
  // News kill switches; applied immediately, no restart.
  setTier1News: (enabled: boolean) =>
    postWith<{ tier1_news_enabled: boolean; live_order_execution: boolean }>(
      '/control/tier1-news',
      { enabled: String(enabled) },
    ),
  setNewsAnalysis: (enabled: boolean) =>
    postWith<{ news_analysis_enabled: boolean; live_order_execution: boolean }>(
      '/control/news-analysis',
      { enabled: String(enabled) },
    ),
  setSonnetEscalation: (enabled: boolean) =>
    postWith<{ sonnet_escalation_enabled: boolean; live_order_execution: boolean }>(
      '/control/sonnet-escalation',
      { enabled: String(enabled) },
    ),
  resetPaper: () => post<{ status: string; execution_provider: string }>('/paper/reset'),
}
