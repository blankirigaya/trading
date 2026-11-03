import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { createQueryClient } from '@/App'
import { DecisionDetail } from '@/components/decisions/DecisionDetail'
import { PaperBanner } from '@/components/common/PaperBanner'
import { StatusHeader } from '@/components/status/StatusHeader'
import { LiveTimeline } from '@/components/timeline/LiveTimeline'
import { useLiveEvents } from '@/store/liveEvents'
import type { DecisionRecord, SystemStatus } from '@/api/types'

function renderWithQuery(ui: React.ReactElement) {
  const client = createQueryClient()
  client.setDefaultOptions({ queries: { retry: false } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

const status: SystemStatus = {
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
  session_running: true,
  shadow_symbols: ['NSE:INFY-EQ'],
  shadow_initial_capital_inr: '100000',
  portfolio_review_interval_seconds: 60,
  database: 'connected',
  news_enabled: true,
  ai_server_reachable: true,
  last_ai_success_at: '2026-09-01T04:00:00+00:00',
  last_ai_failure_at: null,
  last_ai_error: null,
}

function decision(overrides: Partial<DecisionRecord> = {}): DecisionRecord {
  return {
    decision_id: 'decision-1',
    correlation_id: 'corr-1',
    event_id: 'event-1',
    timestamp: '2026-09-01T04:00:00+00:00',
    status: 'EXECUTED',
    input_context_hash: 'abc123',
    initial_ai_proposal: {
      cash_percent: 85,
      allocations: [],
      proposals: [
        {
          proposal_id: 'p-1',
          symbol: 'INFY',
          exchange: 'NSE',
          segment: 'EQ',
          action: 'OPEN_POSITION',
          side: 'BUY',
          capital_allocation_inr: '20000',
          quantity_shares: 17,
          desired_entry_price: null,
          order_type: 'MARKET',
          invalidation_price: null,
          target_price: null,
          expected_holding_minutes: null,
          confidence: 0.78,
          thesis: 'Momentum with volume confirmation.',
          evidence: ['FYERS LTP rising', 'Volume above average'],
          conditions_that_change_thesis: ['Index gap down'],
        },
      ],
      market_summary: 'Strength into close.',
      decision_summary: 'Open a long position.',
    },
    initial_provider_metadata: {
      provider: 'remote_openai_compatible',
      model: 'server-model-id',
      request_id: 'req-1',
      prompt_name: 'portfolio_manager',
      prompt_version: 'v2',
      latency_ms: 1234,
      input_tokens: 500,
      output_tokens: 120,
      temperature: null,
      seed: null,
      schema_version: 'v1',
      provider_metadata: { server: '192.168.1.25:8080' },
    },
    jev_critique: {
      score: 0.63,
      agrees: false,
      disagreement_signals: ['Allocation excessive'],
      critique: 'Reduce the size; evidence is directional only.',
      bounded_decisions: ['REQUEST_RECONSIDERATION'],
    },
    jev_provider_metadata: {
      provider: 'typesafe-jev',
      model: 'jev-latest',
      request_id: null,
      prompt_name: 'critic',
      prompt_version: 'v1',
      latency_ms: 890,
      input_tokens: null,
      output_tokens: null,
      temperature: null,
      seed: null,
      schema_version: 'v1',
      provider_metadata: {},
    },
    final_ai_decision: {
      decision_id: 'decision-1',
      correlation_id: 'corr-1',
      approved: true,
      decision_summary: 'Halved the allocation after challenge.',
      proposals: [
        {
          proposal_id: 'p-2',
          symbol: 'INFY',
          exchange: 'NSE',
          segment: 'EQ',
          action: 'OPEN_POSITION',
          side: 'BUY',
          capital_allocation_inr: '12000',
          quantity_shares: 8,
          desired_entry_price: null,
          order_type: 'MARKET',
          invalidation_price: null,
          target_price: null,
          expected_holding_minutes: null,
          confidence: 0.71,
          thesis: 'Reduced size, thesis intact.',
          evidence: ['FYERS LTP rising'],
          conditions_that_change_thesis: ['Index gap down'],
        },
      ],
    },
    portfolio_before: {
      cash: '100000',
      reserved_cash: '0',
      positions: [],
      realized_pnl: '0',
      unrealized_pnl: '0',
    },
    portfolio_after: {
      cash: '88000',
      reserved_cash: '0',
      positions: [],
      realized_pnl: '0',
      unrealized_pnl: '0',
    },
    orders_generated: [
      {
        broker_order_id: 'PAPER-000001',
        status: 'FILLED',
        created_at: '2026-09-01T04:00:26+00:00',
        request: {
          client_order_id: 'decision-1:p-2',
          decision_id: 'decision-1',
          correlation_id: 'corr-1',
          symbol: 'INFY',
          side: 'BUY',
          order_type: 'MARKET',
          quantity: 8,
          limit_price: null,
          requested_allocation: '12000',
          actual_order_value: '11938.4',
          conversion_reason: 'Whole NSE EQ share quantity rounded down.',
        },
      },
    ],
    market_snapshot_id: 'snapshot-1',
    market_timestamp: '2026-09-01T04:00:00+00:00',
    instrument_key: 'NSE_EQ|3045',
    symbol: 'INFY',
    reconsideration_requested: true,
    reconsideration_performed: true,
    safety_allowed: true,
    safety_reason: null,
    failure_reason: null,
    ...overrides,
  }
}

describe('Paper mode indicator', () => {
  it('is always rendered and states no real orders', () => {
    render(<PaperBanner />)
    expect(screen.getByText(/Paper Mode/i)).toBeInTheDocument()
    expect(screen.getByText(/No Real Orders/i)).toBeInTheDocument()
  })

  it('always shows live order execution as false', () => {
    render(<PaperBanner liveOrderExecution={false} />)
    expect(screen.getByText(/Live order execution: FALSE/i)).toBeInTheDocument()
  })
})

describe('StatusHeader', () => {
  it('renders provider chips and the no-live-orders guarantee', () => {
    render(<StatusHeader status={status} />)
    expect(screen.getByText('AI TRADER')).toBeInTheDocument()
    expect(screen.getByTestId('mode-label')).toHaveTextContent('LIVE_SHADOW')
    expect(screen.getByTestId('live-order-execution')).toHaveTextContent(
      'LIVE ORDER EXECUTION: FALSE',
    )
    // Market chip shows the configured provider; execution chip shows PaperBroker.
    expect(screen.getByText('fyers')).toBeInTheDocument()
    expect(screen.getByText('PaperBroker')).toBeInTheDocument()
    expect(screen.getByText('Execution')).toBeInTheDocument()
  })

  it('marks providers offline when the backend is unreachable', () => {
    render(<StatusHeader status={undefined} />)
    expect(screen.getByTestId('mode-label')).toHaveTextContent('BACKEND OFFLINE')
  })

  it('exposes no credentials', () => {
    const { container } = render(<StatusHeader status={status} />)
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/api[_-]?key/i)
    expect(text).not.toMatch(/access[_-]?token/i)
  })
})

describe('DecisionDetail', () => {
  it('renders initial, Jev and final stages separately', () => {
    render(<DecisionDetail record={decision()} />)
    expect(screen.getByText('Initial AI Decision')).toBeInTheDocument()
    expect(screen.getByText('Jev Critique')).toBeInTheDocument()
    expect(screen.getByText('Final AI Decision')).toBeInTheDocument()
    expect(screen.getByText('Safety Result')).toBeInTheDocument()
    expect(screen.getByText('Paper Execution')).toBeInTheDocument()
  })

  it('keeps the original and final allocations distinct', () => {
    render(<DecisionDetail record={decision()} />)
    // Initial stage badge and final stage badge must carry different allocations.
    expect(screen.getByText(/OPEN_POSITION · BUY INFY · ₹20,000.00 · conf 78%/)).toBeInTheDocument()
    expect(screen.getByText(/OPEN_POSITION · BUY INFY · ₹12,000.00 · conf 71%/)).toBeInTheDocument()
    expect(screen.getByText('Momentum with volume confirmation.')).toBeInTheDocument()
    expect(screen.getByText('Reduced size, thesis intact.')).toBeInTheDocument()
  })

  it('shows the Jev challenge and that the AI reconsidered', () => {
    render(<DecisionDetail record={decision()} />)
    expect(screen.getAllByText('REQUEST_RECONSIDERATION').length).toBeGreaterThan(0)
    expect(screen.getByText(/RECONSIDERED AFTER JEV/)).toBeInTheDocument()
    expect(screen.getByText('63/100')).toBeInTheDocument()
  })

  it('records that no reconsideration was requested when Jev approved', () => {
    const record = decision({
      jev_critique: {
        score: 0.9,
        agrees: true,
        disagreement_signals: [],
        critique: 'Agree.',
        bounded_decisions: ['APPROVE'],
      },
      reconsideration_requested: false,
      reconsideration_performed: false,
    })
    render(<DecisionDetail record={record} />)
    expect(screen.getByText('NO RECONSIDERATION REQUESTED')).toBeInTheDocument()
  })

  it('shows the safety denial reason when blocked', () => {
    const record = decision({
      status: 'DENIED_BY_SAFETY',
      safety_allowed: false,
      safety_reason: 'Order would exceed MAX_SINGLE_POSITION_INR',
      orders_generated: [],
    })
    render(<DecisionDetail record={record} />)
    expect(screen.getByText('DENY')).toBeInTheDocument()
    expect(screen.getByText('Order would exceed MAX_SINGLE_POSITION_INR')).toBeInTheDocument()
  })

  it('surfaces a failure reason', () => {
    const record = decision({
      status: 'FAILED',
      failure_reason: 'ai: AI structured completion failed',
      orders_generated: [],
    })
    render(<DecisionDetail record={record} />)
    expect(screen.getByText('ai: AI structured completion failed')).toBeInTheDocument()
  })

  it('separates an invalid AI reply from a deliberate hold', () => {
    const record = decision({
      status: 'FAILED',
      failure_reason:
        'ai: AI response failed schema validation: cash_percent: Field required',
      orders_generated: [],
      final_ai_decision: null,
      jev_critique: null,
    })
    render(<DecisionDetail record={record} />)

    // The header must not claim the AI decided HOLD when nothing was judged.
    expect(screen.queryByText('HOLD')).not.toBeInTheDocument()
    expect(screen.getByText('INVALID')).toBeInTheDocument()
    expect(screen.getByTestId('ai-output-invalid')).toHaveTextContent(/AI output invalid/i)
    expect(screen.getByTestId('ai-output-invalid')).toHaveTextContent(/not a decision to stay flat/i)
  })
})

describe('LiveTimeline', () => {
  it('shows backend events with category and time', () => {
    useLiveEvents.getState().reset()
    useLiveEvents.getState().append({
      seq: 1,
      timestamp: '2026-09-01T11:02:26+00:00',
      category: 'PAPER',
      kind: 'paper_order',
      message: 'BUY 8 INFY @ 11938.40 INR (paper)',
      decision_id: null,
      correlation_id: null,
      symbol: 'INFY',
      data: {},
    })
    renderWithQuery(<LiveTimeline />)
    expect(screen.getByText(/BUY 8 INFY/)).toBeInTheDocument()
    expect(screen.getByText('STREAM CONNECTING')).toBeInTheDocument()
  })

  it('promotes ERROR events to a visible toast', () => {
    useLiveEvents.getState().reset()
    useLiveEvents.getState().append({
      seq: 2,
      timestamp: '2026-09-01T11:02:00+00:00',
      category: 'ERROR',
      kind: 'provider_error',
      message: 'FYERS disconnected',
      decision_id: null,
      correlation_id: null,
      symbol: null,
      data: {},
    })
    renderWithQuery(<div data-testid="error-toasts">{useLiveEvents.getState().lastError?.message}</div>)
    expect(screen.getByTestId('error-toasts')).toHaveTextContent('FYERS disconnected')
  })

  it('renders an empty state when no events exist', async () => {
    useLiveEvents.getState().reset()
    renderWithQuery(<LiveTimeline />)
    await waitFor(() => expect(screen.getByText(/No events yet/i)).toBeInTheDocument())
  })
})
