import type { MarketStatus, ProviderState, SystemStatus } from '@/api/types'
import { StateBadge } from '@/components/common/Primitives'
import { formatDuration } from '@/utils/format'

/** A feed that has not delivered a tick for this long during NSE hours is stale. */
export const MARKET_STALE_AFTER_SECONDS = 90

function aiState(status: SystemStatus | undefined): ProviderState {
  if (!status) return 'OFFLINE'
  if (status.ai_server_reachable === true) return 'CONNECTED'
  if (status.ai_server_reachable === false) return 'DEGRADED'
  return 'UNKNOWN'
}

function jevState(status: SystemStatus | undefined): ProviderState {
  if (!status) return 'OFFLINE'
  if (!status.jev_enabled) return 'UNKNOWN'
  return status.ai_server_reachable === true ? 'CONNECTED' : 'UNKNOWN'
}

/**
 * The socket can be up while the feed is frozen, which used to render as a
 * green CONNECTED pill next to minutes-old prices. Trust snapshot age during
 * market hours; overnight silence is normal closure, not a fault.
 */
export function marketState(
  status: SystemStatus | undefined,
  market: MarketStatus | undefined,
  marketFailed = false,
): ProviderState {
  if (!status) return 'OFFLINE'
  if (!status.market_connected) return 'OFFLINE'
  if (marketFailed) return 'DEGRADED'
  const age = market?.snapshot_age_seconds
  if (market?.market_open && age !== null && age !== undefined && age > MARKET_STALE_AFTER_SECONDS) {
    return 'DEGRADED'
  }
  return 'CONNECTED'
}

function marketValue(status: SystemStatus | undefined, market: MarketStatus | undefined): string | undefined {
  const provider = status?.market_provider
  const age = market?.snapshot_age_seconds
  if (!status || !market?.market_open || age === null || age === undefined) return provider
  if (age <= MARKET_STALE_AFTER_SECONDS) return provider
  return `${provider ?? ''} · stale ${formatDuration(age)}`.trim()
}

function dbState(status: SystemStatus | undefined): ProviderState {
  if (!status) return 'OFFLINE'
  return status.database === 'connected' ? 'CONNECTED' : 'DEGRADED'
}

function Chip({ label, state, value }: { label: string; state: ProviderState; value?: string }) {
  return (
    <span className="provider-chip">
      <span className="label">{label}</span>
      <StateBadge state={state} />
      {value ? <span className="value">{value}</span> : null}
    </span>
  )
}

/** Top status bar: provider connectivity, mode, and the live-order guarantee. */
export function StatusHeader({
  status,
  market,
  marketFailed = false,
}: {
  status: SystemStatus | undefined
  market?: MarketStatus
  marketFailed?: boolean
}) {
  const backend: ProviderState = status ? 'CONNECTED' : 'OFFLINE'
  const marketProvider = marketState(status, market, marketFailed)
  return (
    <header className="app-header">
      <div className="header-title">
        <h1>
          AI TRADER <span className="ai-mark">/ TERMINAL</span>
        </h1>
        <span data-testid="mode-label">{status ? status.mode : 'BACKEND OFFLINE'}</span>
      </div>
      <div className="provider-strip" role="status" aria-label="Connection status">
        <Chip label="Backend" state={backend} />
        <Chip
          label="Market"
          state={marketProvider}
          value={marketValue(status, market)}
        />
        <Chip label="AI" state={aiState(status)} value={status?.ai_model || undefined} />
        <Chip label="Jev" state={jevState(status)} value={status?.jev_model || undefined} />
        <Chip label="DB" state={dbState(status)} />
        <Chip label="Execution" state={status ? 'CONNECTED' : 'OFFLINE'} value="PaperBroker" />
        <span className="no-live-orders" data-testid="live-order-execution">
          LIVE ORDER EXECUTION: FALSE
        </span>
      </div>
    </header>
  )
}
