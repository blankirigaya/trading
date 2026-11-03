import { useState } from 'react'

import { PaperBanner } from '@/components/common/PaperBanner'
import { OfflineBanner } from '@/components/common/Primitives'
import { Sidebar } from '@/components/layout/Sidebar'
import { StatusHeader } from '@/components/status/StatusHeader'
import { useEventStream } from '@/hooks/useEventStream'
import { useMarketStatus, useSystemStatus } from '@/hooks/useSystemStatus'
import { useLiveEvents } from '@/store/liveEvents'

function ErrorToasts() {
  const lastError = useLiveEvents((state) => state.lastError)
  if (!lastError) return null
  return (
    <div className="toast-stack" role="alert" data-testid="error-toasts">
      <div className="toast">
        {lastError.timestamp.slice(11, 19)} {lastError.kind}: {lastError.message}
      </div>
    </div>
  )
}

/**
 * Application shell. The paper banner is permanent and cannot be dismissed.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const { data: status, isError } = useSystemStatus()
  const { data: market, isError: marketFailed } = useMarketStatus()
  const [navCollapsed, setNavCollapsed] = useState(false)
  useEventStream()

  return (
    <div className={`app-shell${navCollapsed ? ' nav-collapsed' : ''}`}>
      <PaperBanner liveOrderExecution={status?.live_order_execution ?? false} />
      <StatusHeader status={status} market={market} marketFailed={marketFailed} />
      <Sidebar collapsed={navCollapsed} onToggle={() => setNavCollapsed((value) => !value)} />
      <main className="app-main">
        {isError ? <OfflineBanner /> : null}
        {children}
      </main>
      <ErrorToasts />
    </div>
  )
}
