import { QueryClient } from '@tanstack/react-query'
import { Route, Routes } from 'react-router-dom'

import { AppShell } from '@/components/layout/AppShell'
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
 */
export function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/market" element={<Market />} />
        <Route path="/news" element={<News />} />
        <Route path="/timeline" element={<Timeline />} />
        <Route path="/positions" element={<Positions />} />
        <Route path="/portfolio" element={<Portfolio />} />
        <Route path="/trades" element={<Trades />} />
        <Route path="/decisions" element={<Decisions />} />
        <Route path="/performance" element={<Performance />} />
        <Route path="/system" element={<System />} />
        <Route path="*" element={<Dashboard />} />
      </Routes>
    </AppShell>
  )
}
