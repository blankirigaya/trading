import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'

export function useSystemStatus() {
  return useQuery({
    queryKey: ['systemStatus'],
    queryFn: api.systemStatus,
    refetchInterval: 10_000,
    retry: 1,
  })
}

export function useMarketStatus() {
  return useQuery({ queryKey: ['marketStatus'], queryFn: api.marketStatus, refetchInterval: 15_000 })
}

export function useMarketSnapshots() {
  return useQuery({ queryKey: ['marketSnapshots'], queryFn: api.marketSnapshots, refetchInterval: 5_000 })
}

export function useMarketCandles(symbol: string | null) {
  return useQuery({
    queryKey: ['marketCandles', symbol],
    queryFn: () => api.marketCandles(symbol as string),
    enabled: Boolean(symbol),
  })
}
