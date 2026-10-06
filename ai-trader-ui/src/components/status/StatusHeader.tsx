import type { ProviderState, SystemStatus } from '@/api/types'
import { StateBadge } from '@/components/common/Primitives'

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

function marketState(status: SystemStatus | undefined): ProviderState {
  if (!status) return 'OFFLINE'
  return status.market_connected ? 'CONNECTED' : 'OFFLINE'
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
export function StatusHeader({ status }: { status: SystemStatus | undefined }) {
  const backend: ProviderState = status ? 'CONNECTED' : 'OFFLINE'
  return (
    <header className="app-header">
      <div className="header-title">
        <h1>AI TRADER</h1>
        <span data-testid="mode-label">{status ? status.mode : 'BACKEND OFFLINE'}</span>
      </div>
      <div className="provider-strip">
        <Chip label="Backend" state={backend} />
        <Chip label="Market" state={marketState(status)} value={status?.market_provider} />
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
