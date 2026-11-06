import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { App, createQueryClient } from '@/App'
import { api } from '@/api/client'
import type {
  NewsLatencyResponse,
  NewsProvidersResponse,
  NewsRow,
  NewsSourceConfig,
} from '@/api/newsTypes'
import { useLiveEvents } from '@/store/liveEvents'

const newsSourceConfigs: NewsSourceConfig[] = [
  {
    source_id: 'nse-corporate',
    name: 'NSE Corporate Filings',
    homepage: 'https://www.nseindia.com/companies-listing/corporate-filings-announcements',
    source_type: 'OFFICIAL',
    trust_level: 'PRIMARY',
    default_category: 'CORPORATE_FILING',
    enabled: true,
    notes: 'Public filings listing page.',
  },
  {
    source_id: 'livemint-markets',
    name: 'LiveMint Markets',
    homepage: 'https://www.livemint.com/rss/markets.xml',
    source_type: 'NEWS_MEDIA',
    trust_level: 'SECONDARY',
    default_category: 'MARKET_NEWS',
    enabled: true,
    notes: null,
  },
]

const officialItem: NewsRow = {
  source: {
    item_id: 'official-1',
    source_name: 'NSE Corporate Filings',
    source_type: 'OFFICIAL',
    trust_level: 'PRIMARY',
    is_official: true,
    headline: 'Infosys approves buyback of 5 crore shares',
    canonical_url: 'https://www.nseindia.com/api-corporate-announcements?index=equities',
    url: 'https://www.nseindia.com/api-corporate-announcements?index=equities',
    published_at: '2026-10-01T09:00:00+00:00',
    received_at: '2026-10-01T09:00:05+00:00',
    category: 'CORPORATE_FILING',
    symbols: ['INFY'],
    excerpt: 'Board approved the buyback subject to shareholder approval.',
    content_hash: 'a'.repeat(64),
    cluster_id: 'cluster-official-1',
    related_source_count: 0,
  },
  ai_analysis: null,
  review_triggered: false,
}

const mediaItem: NewsRow = {
  source: {
    item_id: 'media-1',
    source_name: 'LiveMint Markets',
    source_type: 'NEWS_MEDIA',
    trust_level: 'SECONDARY',
    is_official: false,
    headline: 'Bank Nifty slips as IT stocks drag',
    canonical_url: 'https://www.livemint.com/market/markets/1/story.example',
    url: 'https://www.livemint.com/market/markets/1/story.example',
    published_at: '2026-10-01T11:30:00+00:00',
    received_at: '2026-10-01T11:30:02+00:00',
    category: 'MARKET_NEWS',
    symbols: [],
    excerpt: null,
    content_hash: 'b'.repeat(64),
    cluster_id: 'cluster-media-1',
    related_source_count: 1,
  },
  ai_analysis: {
    analysis_id: 'an-media-1',
    status: 'COMPLETED',
    event_type: 'MARKET_MOVE',
    summary: 'Index weakness attributed to a decline in information technology shares.',
    affected_symbols: ['INFY'],
    materiality: 'MEDIUM',
    direction: 'NEGATIVE',
    horizon: 'INTRADAY',
    confidence: 0.62,
    requires_portfolio_review: true,
    evidence: ['Headline attributes the move to IT shares.'],
    uncertainties: ['No constituent-level data was provided.'],
    analysis_started_at: '2026-10-01T11:31:00+00:00',
    analysis_completed_at: '2026-10-01T11:31:03+00:00',
    portfolio_review_at: '2026-10-01T11:31:04+00:00',
    failure_reason: null,
    model: 'server-news-model',
    latency_ms: 3200,
  },
  review_triggered: true,
}

const newsRows: NewsRow[] = [mediaItem, officialItem]

const providerResponse: NewsProvidersResponse = {
  ingestion_running: true,
  providers: [
    {
      source_id: 'nse-corporate',
      name: 'NSE India',
      source_type: 'OFFICIAL',
      trust_level: 'PRIMARY',
      enabled: true,
      poll_interval_seconds: 5,
      status: 'LIVE',
      last_item_at: '2026-10-01T09:00:05+00:00',
      seconds_since_last_item: 3,
      last_error: null,
      consecutive_failures: 0,
    },
    {
      source_id: 'rbi-press-releases',
      name: 'Reserve Bank of India',
      source_type: 'OFFICIAL',
      trust_level: 'PRIMARY',
      enabled: true,
      poll_interval_seconds: 30,
      status: 'OFFLINE',
      last_item_at: null,
      seconds_since_last_item: null,
      last_error: 'listing page returned no dated announcement anchors',
      consecutive_failures: 6,
    },
  ],
  queue: { depth: 2, capacity: 200, max_concurrent_analyses: 1 },
  pipeline: {
    enqueued: 12,
    dropped_queue_full: 0,
    analysed: 10,
    analysis_failed: 0,
    reviews_requested: 1,
    reviews_suppressed_duplicate: 0,
  },
}

const latencyResponse: NewsLatencyResponse = {
  rows: [
    {
      item_id: 'media-1',
      analysis_id: 'an-media-1',
      source_id: 'livemint-markets',
      symbols: ['INFY'],
      source_to_ingestion_ms: 1800,
      ingestion_to_analysis_ms: 950,
      analysis_to_decision_ms: 15_200,
      news_to_trade_ms: 21_400,
      review_latency_ms: 11_000,
      published_at: '2026-10-01T11:30:00+00:00',
      received_at: '2026-10-01T11:30:02+00:00',
      analysis_started_at: '2026-10-01T11:31:00+00:00',
      analysis_completed_at: '2026-10-01T11:31:03+00:00',
      portfolio_review_started_at: '2026-10-01T11:31:04+00:00',
      portfolio_decision_at: '2026-10-01T11:31:17+00:00',
      paper_order_at: '2026-10-01T11:31:18+00:00',
    },
    {
      item_id: 'official-1',
      analysis_id: 'an-official-1',
      source_id: 'nse-corporate',
      symbols: ['INFY'],
      source_to_ingestion_ms: 900,
      ingestion_to_analysis_ms: 1100,
      // No decision or order yet: must read "pending", never 0 ms.
      analysis_to_decision_ms: null,
      news_to_trade_ms: null,
      review_latency_ms: null,
      published_at: '2026-10-01T09:00:00+00:00',
      received_at: '2026-10-01T09:00:01+00:00',
      analysis_started_at: '2026-10-01T09:00:02+00:00',
      analysis_completed_at: '2026-10-01T09:00:06+00:00',
      portfolio_review_started_at: null,
      portfolio_decision_at: null,
      paper_order_at: null,
    },
  ],
  count: 2,
  news_to_trade_ms: { samples: 1, min_ms: 21_400, median_ms: 21_400, max_ms: 21_400 },
}

function newsEvent(overrides: Record<string, unknown> = {}) {
  return {
    timestamp: '2026-10-01T11:31:00+00:00',
    category: 'MARKET' as const,
    kind: 'news_received',
    message: 'NSE India: Infosys board approves buyback',
    decision_id: null,
    correlation_id: null,
    symbol: 'INFY' as string | null,
    data: {},
    seq: 1,
    ...overrides,
  }
}

const statusPayload = {
  mode: 'LIVE_SHADOW',
  environment: 'development',
  trading_enabled: false,
  market_provider: 'fyers',
  market_connected: true,
  execution_provider: 'PaperBroker',
  execution_broker: 'paper',
  live_order_execution: false,
  fyers_market_data_only: true,
  ai_provider: 'remote_openai_compatible',
  ai_server: '192.168.1.25:8080',
  ai_model: 'server-model-id',
  ai_inference_location: 'remote',
  jev_enabled: true,
  jev_model: 'jev-latest',
  kill_switch_state: false,
  session_running: false,
  shadow_symbols: ['NSE:INFY-EQ'],
  shadow_initial_capital_inr: '100000',
  portfolio_review_interval_seconds: 60,
  database: 'connected',
  news_enabled: true,
  ai_server_reachable: true,
  last_ai_success_at: null,
  last_ai_failure_at: null,
  last_ai_error: null,
}

function stubBackend(overrides: Record<string, unknown> = {}, performanceBody: unknown = {}, botsBody: unknown = []) {
  const calls: string[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    calls.push(url)
    const json = (body: unknown) =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    if (url.includes('/charges/schedule'))
      return json({
        enabled: true,
        rates: {
          brokerage_pct: '0.0003',
          brokerage_cap_inr: '20',
          stt_sell_pct: '0.00025',
          exchange_pct: '0.0000297',
          sebi_pct: '0.000001',
          stamp_duty_buy_pct: '0.00003',
          gst_pct: '0.18',
        },
      })
    if (url.includes('/system/status')) return json({ ...statusPayload, ...overrides })
    if (url.includes('/events/recent')) return json([])
    if (url.includes('/portfolio')) return json({ ...overrides })
    if (url.includes('/market/status')) return json({ symbols: ['NSE:INFY-EQ'] })
    if (url.includes('/market/snapshots')) return json([])
    if (url.includes('/news/sources')) return json(newsSourceConfigs)
    if (url.includes('/news/providers')) return json(providerResponse)
    if (url.includes('/news/latency')) return json(latencyResponse)
    if (url.includes('/news')) return json(newsRows)
    if (url.includes('/decisions')) return json([])
    if (url.includes('/trades/history'))
      return json({
        closed_trades: [
          {
            closed_trade_id: 'CLOSE-FILL-PAPER-000002',
            shard: 0,
            symbol: 'INFY',
            quantity: 10,
            entry_price: '100.0000',
            exit_price: '112.0000',
            realized_pnl: '118.8800',
            charges: '1.1200',
            gross_pnl: '120.0000',
            opened_at: null,
            closed_at: '2026-10-28T05:30:00+00:00',
            entry_decision_id: 'd1',
            exit_decision_id: 'd2',
          },
        ],
        total_realized_pnl: '118.8800',
        total_charges: '1.1200',
        gross_realized_pnl: '120.0000',
        wins: 1,
        losses: 0,
      })
    if (url.includes('/trades')) return json([])
    if (url.includes('/performance')) return json(performanceBody)
    if (url.endsWith('/bots')) return json(botsBody)
    if (url.includes('/positions')) return json([])
    if (url.includes('/control/review')) {
      return json({ requested: true, decision_id: 'manual-review-1' })
    }
    if (url.includes('/control/kill-switch'))
      return json({ kill_switch_state: true, live_order_execution: false })
    if (url.includes('/control/universe')) {
      const mode = new URL(url, 'http://127.0.0.1').searchParams.get('mode') ?? 'cheap'
      return json({ universe_mode: mode, applies: 'next_review' })
    }
    if (url.includes('/control/capital-cap')) {
      const disabled =
        new URL(url, 'http://127.0.0.1').searchParams.get('disabled') === 'true'
      return json({ capital_cap_disabled: disabled, live_order_execution: false })
    }
    if (url.includes('/control/position-cap')) {
      const disabled =
        new URL(url, 'http://127.0.0.1').searchParams.get('disabled') === 'true'
      return json({ position_count_cap_disabled: disabled, live_order_execution: false })
    }
    if (url.includes('/control/tier1-news')) {
      const enabled =
        new URL(url, 'http://127.0.0.1').searchParams.get('enabled') !== 'false'
      return json({ tier1_news_enabled: enabled, live_order_execution: false })
    }
    if (url.includes('/control/news-analysis')) {
      const enabled =
        new URL(url, 'http://127.0.0.1').searchParams.get('enabled') !== 'false'
      return json({ news_analysis_enabled: enabled, live_order_execution: false })
    }
    if (url.includes('/control/sonnet-escalation')) {
      const enabled =
        new URL(url, 'http://127.0.0.1').searchParams.get('enabled') !== 'false'
      return json({ sonnet_escalation_enabled: enabled, live_order_execution: false })
    }
    if (url.includes('/paper/reset')) return json({ status: 'reset' })
    return json({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return { calls, fetchMock }
}

function renderApp(path = '/') {
  // A fresh client per render keeps tests isolated from the module-level default.
  const client = createQueryClient()
  client.setDefaultOptions({ queries: { retry: false } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('App shell', () => {
  beforeEach(() => {
    useLiveEvents.getState().reset()
    vi.stubGlobal(
      'WebSocket',
      class {
        onopen = null
        onclose = null
        onerror = null
        onmessage = null
        close() {}
      },
    )
  })

  it('shows the paper banner and status header', async () => {
    stubBackend()
    renderApp('/')
    expect(screen.getAllByText(/Paper Mode/i).length).toBeGreaterThan(0)
    expect(screen.getByText('AI TRADER')).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByTestId('live-order-execution')).toHaveTextContent('FALSE'),
    )
  })

  it('switches the AI universe from the dashboard after confirmation', async () => {
    const { fetchMock } = stubBackend({ universe_mode: 'cheap', universe_master_size: 180 })
    renderApp('/')
    expect(await screen.findByTestId('universe-badge')).toHaveTextContent('CHEAP')

    await userEvent.click(screen.getByTestId('universe-expensive'))
    expect(screen.getByTestId('universe-dialog')).toBeInTheDocument()
    await userEvent.click(screen.getByTestId('confirm-button'))

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          (call) =>
            String(call[0]).includes('/control/universe') &&
            String(call[0]).includes('mode=expensive'),
        ),
      ).toBe(true),
    )
  })

  it('requests an AI review from the dashboard after confirmation', async () => {
    const { fetchMock } = stubBackend()
    renderApp('/')
    await userEvent.click(await screen.findByTestId('request-review'))
    expect(screen.getByTestId('confirm-review')).toBeInTheDocument()
    await userEvent.click(screen.getByTestId('confirm-button'))

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((call) => String(call[0]).includes('/control/review')),
      ).toBe(true),
    )
    await waitFor(() => expect(screen.getByTestId('review-requested')).toBeInTheDocument())
  })

  it('scans for entries only without touching exits', async () => {
    const { fetchMock } = stubBackend()
    renderApp('/')
    await userEvent.click(await screen.findByTestId('scan-entries'))
    expect(screen.getByTestId('confirm-review')).toBeInTheDocument()
    await userEvent.click(screen.getByTestId('confirm-button'))

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((call) => String(call[0]).includes('scope=entries')),
      ).toBe(true),
    )
    await waitFor(() => expect(screen.getByTestId('review-requested')).toBeInTheDocument())
  })

  it('shows BACKEND OFFLINE without crashing when the API is unreachable', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('connection refused')
      }),
    )
    renderApp('/')
    // The shell renders immediately regardless of backend state.
    expect(screen.getByText('AI TRADER')).toBeInTheDocument()
    expect(screen.getAllByText(/Paper Mode/i).length).toBeGreaterThan(0)
    // Provider chips report OFFLINE straight away.
    expect(screen.getByTestId('mode-label')).toHaveTextContent('BACKEND OFFLINE')
    await waitFor(() => expect(screen.getByTestId('backend-offline')).toBeInTheDocument(), {
      timeout: 10_000,
    })
  })

  it('renders dashboard portfolio cards from backend data', async () => {
    const { calls } = stubBackend({
      cash: '100000',
      reserved_cash: '0',
      positions: [],
      realized_pnl: '0',
      unrealized_pnl: '0',
    })
    renderApp('/')
    await waitFor(() => expect(screen.getByTestId('stat-cash')).toBeInTheDocument())
    expect(calls.some((url) => url.includes('/portfolio'))).toBe(true)
    await waitFor(() => expect(screen.getByTestId('stat-cash')).toHaveTextContent('1,00,000'))
  })

  it('navigates to the decisions page', async () => {
    stubBackend()
    renderApp('/')
    const link = await screen.findByText('Decisions')
    await userEvent.click(link)
    await waitFor(() => expect(screen.getByText(/AI decision journal/i)).toBeInTheDocument())
  })

  it('navigates to the charts page from the sidebar', async () => {
    stubBackend()
    renderApp('/')
    const link = await screen.findByText('Charts')
    await userEvent.click(link)
    await waitFor(() => expect(screen.getByText('Trade charts')).toBeInTheDocument())
  })

  it('renders the trade charts workspace with real backend data only', async () => {
    stubBackend()
    renderApp('/charts')
    await waitFor(() => expect(screen.getByText('Symbol & timeframe')).toBeInTheDocument())
    // No candles, no fills, no invented history — honest empty states instead.
    expect(screen.getByText('Trade history')).toBeInTheDocument()
  })
})

/**
 * The News page must keep publisher facts and AI interpretation visually separate,
 * and must never offer a trading action derived from news.
 */
function rowAt(rows: HTMLElement[], index: number): HTMLElement {
  const row = rows[index]
  if (!row) throw new Error(`expected a news row at index ${index}, found ${rows.length}`)
  return row
}

describe('News intelligence', () => {
  beforeEach(() => {
    useLiveEvents.getState().reset()
    vi.stubGlobal(
      'WebSocket',
      class {
        onopen = null
        onclose = null
        onerror = null
        onmessage = null
        close() {}
      },
    )
  })

  it('separates official from secondary sources in the feed', async () => {
    stubBackend()
    renderApp('/news')
    const rows = await screen.findAllByTestId('news-row')
    expect(rows).toHaveLength(2)
    expect(screen.getAllByText('OFFICIAL')).not.toHaveLength(0)
    expect(screen.getAllByText('SECONDARY')).not.toHaveLength(0)
  })

  it('filters to official sources only', async () => {
    stubBackend()
    renderApp('/news')
    await screen.findAllByTestId('news-row')
    await userEvent.click(screen.getByRole('button', { name: 'OFFICIAL' }))
    await waitFor(() => expect(screen.getAllByTestId('news-row')).toHaveLength(1))
    expect(screen.getByText(/Infosys approves buyback/i)).toBeInTheDocument()
    expect(screen.queryByText(/Bank Nifty slips/i)).not.toBeInTheDocument()
  })

  it('shows source material and AI analysis as separate blocks in the detail drawer', async () => {
    stubBackend()
    renderApp('/news')
    const rows = await screen.findAllByTestId('news-row')
    await userEvent.click(rowAt(rows, 0))
    const drawer = await screen.findByTestId('news-drawer')
    expect(within(drawer).getByTestId('source-material')).toBeInTheDocument()
    expect(within(drawer).getByTestId('ai-analysis')).toBeInTheDocument()
    // The AI block names its model and latency; the source block does not.
    expect(within(drawer).getByText('server-news-model')).toBeInTheDocument()
    expect(within(drawer).getByText('3.20 s')).toBeInTheDocument()
    expect(within(drawer).getByText('LiveMint Markets')).toBeInTheDocument()
  })

  it('labels unanalysed news instead of inventing an interpretation', async () => {
    stubBackend()
    renderApp('/news')
    const rows = await screen.findAllByTestId('news-row')
    await userEvent.click(rowAt(rows, 1))
    const drawer = await screen.findByTestId('news-drawer')
    expect(within(drawer).getByText('NOT ANALYSED')).toBeInTheDocument()
    expect(within(drawer).queryByTestId('thesis')).not.toBeInTheDocument()
  })

  it('marks a news-triggered portfolio review without proposing an order', async () => {
    stubBackend()
    const { container } = renderApp('/news')
    const rows = await screen.findAllByTestId('news-row')
    await userEvent.click(rowAt(rows, 0))
    await screen.findByTestId('news-drawer')
    expect(screen.getByText('PORTFOLIO REVIEW TRIGGERED')).toBeInTheDocument()
    const text = (container.textContent ?? '').toLowerCase()
    expect(text).not.toMatch(/\bplace order\b/)
    expect(text).not.toMatch(/\bbuy\b/)
    expect(text).not.toMatch(/\bsell\b/)
  })

  it('opens the original publisher link externally and never in-app', async () => {
    stubBackend()
    renderApp('/news')
    const rows = await screen.findAllByTestId('news-row')
    await userEvent.click(rowAt(rows, 1))
    const link = await screen.findByTestId('source-link')
    expect(link).toHaveAttribute('href', officialItem.source.canonical_url)
    expect(link).toHaveAttribute('target', '_blank')
    expect(link.getAttribute('rel')).toContain('noopener')
  })

  it('never sends news to a provider or trading endpoint from the renderer', async () => {
    const { fetchMock } = stubBackend()
    renderApp('/news')
    await screen.findAllByTestId('news-row')
    for (const call of fetchMock.mock.calls) {
      const url = String(call[0])
      expect(url).not.toMatch(/place_order|placeOrder|\/orders|kill-switch|paper\/reset/)
    }
  })

  it('shows the live news panel on the dashboard', async () => {
    stubBackend()
    renderApp('/')
    // No news yet: the panel renders a waiting state rather than nothing.
    expect(await screen.findByText('Live news')).toBeInTheDocument()
    useLiveEvents.getState().append(newsEvent({ seq: 1 }))
    expect(await screen.findByTestId('live-news-item')).toHaveTextContent(
      'Infosys board approves buyback',
    )
  })

  it('shows the AI token and cost counter on the dashboard', async () => {
    stubBackend({}, {
      ai_usage: {
        calls: 12,
        input_tokens: 1500000,
        output_tokens: 60000,
        estimated_cost_usd: 0.0042,
        cost_configured: true,
        by_model: {},
        last_24h: { calls: 3, input_tokens: 9000, output_tokens: 900, estimated_cost_usd: null },
        sources: {
          reviews: { calls: 8, input_tokens: 1000000, output_tokens: 40000, estimated_cost_usd: null },
          reconsiderations: { calls: 3, input_tokens: 400000, output_tokens: 15000, estimated_cost_usd: null },
          news: { calls: 1, input_tokens: 100000, output_tokens: 5000, estimated_cost_usd: null },
        },
      },
    })
    renderApp('/')
    expect(await screen.findByTestId('stat-ai-calls')).toHaveTextContent('12')
    expect(await screen.findByTestId('stat-ai-input')).toHaveTextContent('1.5M')
    expect(await screen.findByTestId('stat-ai-total')).toHaveTextContent('1.6M')
    expect(await screen.findByTestId('stat-ai-cost')).toHaveTextContent('$0.0042')
  })

  it('shows a rates hint instead of a cost when rates are not configured', async () => {
    stubBackend({}, {
      ai_usage: {
        calls: 2,
        input_tokens: 3000,
        output_tokens: 300,
        estimated_cost_usd: null,
        cost_configured: false,
        by_model: {},
        last_24h: { calls: 2, input_tokens: 3000, output_tokens: 300, estimated_cost_usd: null },
        sources: {},
      },
    })
    renderApp('/')
    expect(await screen.findByTestId('stat-ai-cost')).toHaveTextContent('set rates')
  })

  it('lists each bot with its symbols, book, and recent decisions', async () => {
    stubBackend({}, {}, [
      {
        index: 0,
        name: 'Bot 1',
        symbols: ['INFY', 'TCS'],
        cash: '10000',
        positions: [
          { symbol: 'INFY', quantity: 10, average_entry_price: '1150.00', ltp: '1160.00', value: '11600.00', unrealized_pnl: '100.00' },
        ],
        reviews: 4,
        ai_model: 'test-model',
        recent_decisions: [
          { decision_id: 'abc12345', symbol: 'INFY', status: 'EXECUTED', timestamp: '2026-10-22T06:46:15+00:00', ai_action: 'OPEN_POSITION' },
        ],
      },
      {
        index: 1,
        name: 'Bot 2',
        symbols: ['RELIANCE'],
        cash: '10000',
        positions: [],
        reviews: 2,
        ai_model: 'test-model',
        recent_decisions: [],
      },
    ])
    renderApp('/bots')
    expect(await screen.findByText('Bot 1')).toBeInTheDocument()
    expect(screen.getByText('Bot 2')).toBeInTheDocument()
    expect(screen.getByText(/INFY, TCS/)).toBeInTheDocument()
    expect(screen.getByTestId('bot-reviews-0')).toHaveTextContent('4')
    expect(screen.getByText('abc12345'.slice(0, 8))).toBeInTheDocument()
  })

  it('explains an empty bots page when no session is attached', async () => {
    stubBackend()
    renderApp('/bots')
    expect(await screen.findByText(/No reviewer bots are attached/)).toBeInTheDocument()
  })
})

/**
 * Continuous ingestion is observed, never driven, from the UI. These tests confirm the
 * live transcript, provider health, and latency arrive over the event stream and REST
 * with no manual refresh and no trading control.
 */
describe('Continuous news ingestion', () => {
  beforeEach(() => {
    useLiveEvents.getState().reset()
    vi.stubGlobal(
      'WebSocket',
      class {
        onopen = null
        onclose = null
        onerror = null
        onmessage = null
        close() {}
      },
    )
  })

  it('renders news events pushed over the websocket without a refresh', async () => {
    stubBackend()
    renderApp('/news')
    await screen.findAllByTestId('news-row')
    expect(screen.queryByTestId('live-news-item')).not.toBeInTheDocument()

    // Events arrive after mount, exactly as the backend emits them.
    useLiveEvents.getState().append(newsEvent({ seq: 1 }))
    useLiveEvents.getState().append(
      newsEvent({
        seq: 2,
        category: 'AI' as const,
        kind: 'news_analysis_completed',
        message: 'High / Negative',
      }),
    )
    useLiveEvents.getState().append(
      newsEvent({
        seq: 3,
        category: 'SYSTEM' as const,
        kind: 'news_review_requested',
        message: 'Immediate portfolio review requested (NEWS_AI_FLAGGED) for INFY',
      }),
    )

    const items = await screen.findAllByTestId('live-news-item')
    expect(items).toHaveLength(3)
    // Newest first, so the transcript reads top-down as it happened.
    expect(items[0]).toHaveTextContent('Immediate portfolio review requested')
    expect(items[1]).toHaveTextContent('High / Negative')
    expect(items[2]).toHaveTextContent('Infosys board approves buyback')
  })

  it('labels each live news stage', async () => {
    stubBackend()
    renderApp('/news')
    await screen.findAllByTestId('news-row')
    useLiveEvents.getState().append(
      newsEvent({ seq: 1, kind: 'news_review_started', message: 'Portfolio review started' }),
    )
    const item = await screen.findByTestId('live-news-item')
    expect(item).toHaveTextContent('TRADING AI')
  })

  it('reports per-source liveness and last item age', async () => {
    stubBackend()
    renderApp('/news')
    const strip = await screen.findByTestId('provider-strip')
    expect(within(strip).getByText('LIVE')).toBeInTheDocument()
    expect(within(strip).getByText('OFFLINE')).toBeInTheDocument()
    expect(within(strip).getByText('last item 3s ago')).toBeInTheDocument()
    // A failed official feed surfaces the reason instead of silently looking healthy.
    expect(within(strip).getByText(/no dated announcement anchors/i)).toBeInTheDocument()
  })

  it('reports the analysis queue and concurrency bound', async () => {
    stubBackend()
    renderApp('/news')
    const strip = await screen.findByTestId('provider-strip')
    expect(strip.parentElement).toHaveTextContent('Analysis queue 2/200')
    expect(strip.parentElement).toHaveTextContent('max concurrent 1')
  })

  it('shows measured latency and marks incomplete stages as pending', async () => {
    stubBackend()
    renderApp('/news')
    const rows = await screen.findAllByTestId('latency-row')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('21.40 s')
    // The untraded item must read pending for every stage it has not reached,
    // never a misleading 0 ms.
    const cells = within(rowAt(rows, 1)).getAllByText('pending')
    expect(cells).toHaveLength(2)
  })

  it('never exposes a trading control from live news', async () => {
    stubBackend()
    const { container } = renderApp('/news')
    await screen.findAllByTestId('news-row')
    useLiveEvents.getState().append(
      newsEvent({
        seq: 1,
        category: 'SYSTEM' as const,
        kind: 'news_review_requested',
        message: 'Immediate portfolio review requested for INFY',
      }),
    )
    await screen.findByTestId('live-news-item')
    const text = (container.textContent ?? '').toLowerCase()
    expect(text).not.toMatch(/\bplace order\b/)
    expect(text).not.toMatch(/\bexecute\b/)
  })
})

describe('Safe controls', () => {
  beforeEach(() => {
    useLiveEvents.getState().reset()
    vi.stubGlobal(
      'WebSocket',
      class {
        onopen = null
        onclose = null
        onerror = null
        onmessage = null
        close() {}
      },
    )
  })

  it('requires confirmation before engaging the kill switch', async () => {
    const { fetchMock } = stubBackend()
    renderApp('/system')
    const trigger = await screen.findByTestId('control-kill-switch')
    await userEvent.click(trigger)
    expect(screen.getByTestId('kill-switch-dialog')).toBeInTheDocument()

    // Cancelling must not call the backend.
    const before = fetchMock.mock.calls.filter((call) => String(call[0]).includes('kill-switch')).length
    await userEvent.click(screen.getByText('Cancel'))
    expect(screen.queryByTestId('kill-switch-dialog')).not.toBeInTheDocument()
    const after = fetchMock.mock.calls.filter((call) => String(call[0]).includes('kill-switch')).length
    expect(after).toBe(before)
  })

  it('calls the backend only after explicit confirmation', async () => {
    const { fetchMock } = stubBackend()
    renderApp('/system')
    await userEvent.click(await screen.findByTestId('control-kill-switch'))
    await userEvent.click(screen.getByTestId('confirm-button'))
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((call) => String(call[0]).includes('/control/kill-switch')),
      ).toBe(true),
    )
  })

  it('requires confirmation before resetting the paper account', async () => {
    stubBackend()
    renderApp('/system')
    await userEvent.click(await screen.findByTestId('control-reset'))
    expect(screen.getByTestId('reset-dialog')).toBeInTheDocument()
  })

  it('toggles the capital cap only after confirmation', async () => {
    const { fetchMock } = stubBackend()
    renderApp('/system')
    await userEvent.click(await screen.findByTestId('control-capital-cap'))
    expect(screen.getByTestId('capital-cap-dialog')).toBeInTheDocument()
    await userEvent.click(screen.getByTestId('confirm-button'))
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((call) =>
          String(call[0]).includes('/control/capital-cap?disabled=true'),
        ),
      ).toBe(true),
    )
  })

  it('shows when the capital cap is disabled', async () => {
    stubBackend({ capital_cap_disabled: true })
    renderApp('/system')
    expect(await screen.findByTestId('system-capital-cap')).toHaveTextContent('DISABLED')
    expect(screen.getByTestId('control-capital-cap')).toHaveTextContent('Restore capital cap')
  })

  it('toggles the position cap only after confirmation', async () => {
    const { fetchMock } = stubBackend()
    renderApp('/system')
    await userEvent.click(await screen.findByTestId('control-position-cap'))
    expect(screen.getByTestId('position-cap-dialog')).toBeInTheDocument()
    await userEvent.click(screen.getByTestId('confirm-button'))
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((call) =>
          String(call[0]).includes('/control/position-cap?disabled=true'),
        ),
      ).toBe(true),
    )
  })

  it('shows when the position cap is disabled', async () => {
    stubBackend({ position_count_cap_disabled: true })
    renderApp('/system')
    expect(await screen.findByTestId('system-position-cap')).toHaveTextContent('DISABLED')
    expect(screen.getByTestId('control-position-cap')).toHaveTextContent('Restore position cap')
  })

  it('toggles Tier-1 news only after confirmation', async () => {
    const { fetchMock } = stubBackend()
    renderApp('/system')
    await userEvent.click(await screen.findByTestId('control-tier1-news'))
    expect(screen.getByTestId('tier1-news-dialog')).toBeInTheDocument()
    await userEvent.click(screen.getByTestId('confirm-button'))
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((call) =>
          String(call[0]).includes('/control/tier1-news?enabled=false'),
        ),
      ).toBe(true),
    )
  })

  it('shows when Tier-1 news is off', async () => {
    stubBackend({ tier1_news_enabled: false })
    renderApp('/system')
    expect(await screen.findByTestId('system-tier1-news')).toHaveTextContent('OFF')
    expect(screen.getByTestId('control-tier1-news')).toHaveTextContent('Turn on Tier-1 news')
  })

  it('toggles news analysis only after confirmation', async () => {
    const { fetchMock } = stubBackend()
    renderApp('/system')
    await userEvent.click(await screen.findByTestId('control-news-analysis'))
    expect(screen.getByTestId('news-analysis-dialog')).toBeInTheDocument()
    await userEvent.click(screen.getByTestId('confirm-button'))
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((call) =>
          String(call[0]).includes('/control/news-analysis?enabled=false'),
        ),
      ).toBe(true),
    )
  })

  it('shows when news analysis is off', async () => {
    stubBackend({ news_analysis_enabled: false })
    renderApp('/system')
    expect(await screen.findByTestId('system-news-analysis')).toHaveTextContent('OFF')
    expect(screen.getByTestId('control-news-analysis')).toHaveTextContent('Turn on news analysis')
  })

  it('toggles Sonnet escalation only after confirmation', async () => {
    const { fetchMock } = stubBackend()
    renderApp('/system')
    await userEvent.click(await screen.findByTestId('control-sonnet-escalation'))
    expect(screen.getByTestId('sonnet-escalation-dialog')).toBeInTheDocument()
    await userEvent.click(screen.getByTestId('confirm-button'))
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((call) =>
          String(call[0]).includes('/control/sonnet-escalation?enabled=false'),
        ),
      ).toBe(true),
    )
  })

  it('shows when Sonnet escalation is off', async () => {
    stubBackend({ sonnet_escalation_enabled: false })
    renderApp('/system')
    expect(await screen.findByTestId('system-sonnet-escalation')).toHaveTextContent('OFF')
    expect(screen.getByTestId('control-sonnet-escalation')).toHaveTextContent('Turn on Sonnet escalation')
  })

  it('exposes no buy or sell control anywhere on the system page', async () => {
    stubBackend()
    const { container } = renderApp('/system')
    await screen.findByTestId('control-kill-switch')
    const text = (container.textContent ?? '').toLowerCase()
    expect(text).not.toMatch(/\bplace order\b/)
    expect(text).not.toMatch(/\bbuy\b(?!.*decision)/)
  })

  it('reports live broker execution as disabled', async () => {
    stubBackend()
    renderApp('/system')
    await waitFor(() =>
      expect(screen.getByTestId('system-live-execution')).toHaveTextContent('DISABLED'),
    )
  })

  it('shows the permanent closed-trade record with realized P&L', async () => {
    stubBackend()
    renderApp('/trades')
    await screen.findByText('Closed trades — permanent record')
    expect(screen.getByText('INFY')).toBeInTheDocument()
    // Gross +120 on 10 shares @100 -> @112, net of 1.12 charges. Gross appears
    // on the row and the summary card; net appears on the row and the total.
    expect(screen.getAllByText('+₹120.00')).toHaveLength(2)
    expect(screen.getAllByText('+₹118.88')).toHaveLength(2)
    expect(screen.getByText('Charges paid')).toBeInTheDocument()
    expect(screen.getAllByText('₹1.12')).toHaveLength(2)
    expect(screen.getByText('Realized P&L (net)')).toBeInTheDocument()
  })
})

describe('API client', () => {
  it('targets the configured backend base URL', () => {
    expect(api.baseUrl).toMatch(/^http:\/\//)
  })
})
