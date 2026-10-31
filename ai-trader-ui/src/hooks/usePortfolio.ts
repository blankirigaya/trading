import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'

export function usePortfolio() {
  return useQuery({
    queryKey: ['portfolio'],
    queryFn: api.portfolio,
    refetchInterval: 2_000,
    refetchIntervalInBackground: true,
  })
}

export function usePositions() {
  return useQuery({
    queryKey: ['positions'],
    queryFn: api.positions,
    refetchInterval: 2_000,
    refetchIntervalInBackground: true,
  })
}

export function usePerformance() {
  return useQuery({
    queryKey: ['performance'],
    queryFn: api.performance,
    refetchInterval: 20_000,
    refetchIntervalInBackground: true,
  })
}
