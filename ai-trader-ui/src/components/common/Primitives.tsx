import type { ProviderState } from '@/api/types'

const TONE: Record<ProviderState, string> = {
  CONNECTED: 'ok',
  DEGRADED: 'warn',
  OFFLINE: 'bad',
  UNKNOWN: 'neutral',
}

export function StateBadge({ state }: { state: ProviderState }) {
  return (
    <span className={`badge ${TONE[state]}`} data-testid="state-badge" data-state={state}>
      {state}
    </span>
  )
}

export function Badge({
  tone = 'neutral',
  children,
  testId,
}: {
  tone?: 'ok' | 'warn' | 'bad' | 'neutral' | 'paper' | 'accent'
  children: React.ReactNode
  testId?: string
}) {
  return (
    <span className={`badge ${tone}`} data-testid={testId}>
      {children}
    </span>
  )
}

export function Stat({
  label,
  value,
  tone,
  testId,
}: {
  label: string
  value: React.ReactNode
  tone?: 'pos' | 'neg'
  testId?: string
}) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className={`stat-value${tone ? ` ${tone}` : ''}`} data-testid={testId}>
        {value}
      </div>
    </div>
  )
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>
}

export function OfflineBanner({ message }: { message?: string }) {
  return (
    <div className="offline-banner" role="alert" data-testid="backend-offline">
      BACKEND OFFLINE{message ? ` &mdash; ${message}` : ''}. Retrying with backoff; the UI will
      reload state when the FastAPI service returns.
    </div>
  )
}
