import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'

/**
 * Live queries keep polling while the window is unfocused.
 *
 * Electron (like any Chromium window) throttles background timers, and
 * TanStack Query additionally pauses `refetchInterval` when the window is not
 * focused unless this flag is set. Without it the dashboard silently froze at
 * the last price whenever the user switched to another app, which looked like
 * a backend crash even though the API was healthy.
 */
const LIVE = { refetchIntervalInBackground: true } as const

export function useSystemStatus() {
  return useQuery({
    queryKey: ['systemStatus'],
    queryFn: api.systemStatus,
    refetchInterval: 10_000,
    retry: 1,
    ...LIVE,
  })
}

export function useMarketStatus() {
  // Small payload; polled at tick speed so the header can tell "feed stale"
  // apart from "backend offline" while the window is in the background.
  return useQuery({
    queryKey: ['marketStatus'],
    queryFn: api.marketStatus,
    refetchInterval: 2_000,
    ...LIVE,
  })
}

export function useMarketSnapshots() {
  return useQuery({
    queryKey: ['marketSnapshots'],
    queryFn: api.marketSnapshots,
    refetchInterval: 1_000,
    ...LIVE,
  })
}

export function useMarketCandles(symbol: string | null) {
  return useQuery({
    queryKey: ['marketCandles', symbol],
    queryFn: () => api.marketCandles(symbol as string),
    enabled: Boolean(symbol),
    refetchInterval: 5_000,
    ...LIVE,
  })
}
