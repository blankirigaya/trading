import { QueryClient } from '@tanstack/react-query'
import { Route, Routes } from 'react-router-dom'

import { AppShell } from '@/components/layout/AppShell'
import { PageTransition } from '@/components/common/PageTransition'
import { PanelErrorBoundary } from '@/components/common/PanelErrorBoundary'
import { Bots } from '@/pages/Bots'
import { Analytics } from '@/pages/Analytics'
import { Charts } from '@/pages/Charts'
import { Dashboard } from '@/pages/Dashboard'
import { Decisions } from '@/pages/Decisions'
import { Market } from '@/pages/Market'
import { News } from '@/pages/News'
import { Portfolio } from '@/pages/Portfolio'
import { Positions } from '@/pages/Positions'
import { Performance } from '@/pages/Performance'
import { System } from '@/pages/System'
import { Timeline } from '@/pages/Timeline'
import { Trades } from '@/pages/Trades'

export function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: 1, refetchOnWindowFocus: true, staleTime: 2_000 },
    },
  })
}

export const queryClient = createQueryClient()

/**
 * Routes only. The router and query client are provided by the entry point (or by a
 * test harness) so this component can be mounted with an isolated cache.
 *
 * Each page renders inside its own error boundary: a crash in one panel shows an
 * inline error with a retry button instead of blanking the whole app (which used
 * to force a manual Ctrl+R to recover).
 */
export function App() {
  return (
    <AppShell>
      <PageTransition>
        <Routes>
          <Route path="/" element={<PanelErrorBoundary panel="Dashboard"><Dashboard /></PanelErrorBoundary>} />
          <Route path="/market" element={<PanelErrorBoundary panel="Market"><Market /></PanelErrorBoundary>} />
          <Route path="/charts" element={<PanelErrorBoundary panel="Charts"><Charts /></PanelErrorBoundary>} />
          <Route path="/news" element={<PanelErrorBoundary panel="News"><News /></PanelErrorBoundary>} />
          <Route path="/timeline" element={<PanelErrorBoundary panel="Timeline"><Timeline /></PanelErrorBoundary>} />
          <Route path="/positions" element={<PanelErrorBoundary panel="Positions"><Positions /></PanelErrorBoundary>} />
          <Route path="/portfolio" element={<PanelErrorBoundary panel="Portfolio"><Portfolio /></PanelErrorBoundary>} />
          <Route path="/trades" element={<PanelErrorBoundary panel="Trades"><Trades /></PanelErrorBoundary>} />
          <Route path="/decisions" element={<PanelErrorBoundary panel="Decisions"><Decisions /></PanelErrorBoundary>} />
          <Route path="/performance" element={<PanelErrorBoundary panel="Performance"><Performance /></PanelErrorBoundary>} />
          <Route path="/bots" element={<PanelErrorBoundary panel="Bots"><Bots /></PanelErrorBoundary>} />
        <Route path="/analytics" element={<PanelErrorBoundary panel="Analytics"><Analytics /></PanelErrorBoundary>} />
          <Route path="/system" element={<PanelErrorBoundary panel="System"><System /></PanelErrorBoundary>} />
          <Route path="*" element={<PanelErrorBoundary panel="Dashboard"><Dashboard /></PanelErrorBoundary>} />
        </Routes>
      </PageTransition>
    </AppShell>
  )
}
