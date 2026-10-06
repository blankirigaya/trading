import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'

export function usePortfolio() {
  return useQuery({ queryKey: ['portfolio'], queryFn: api.portfolio, refetchInterval: 5_000 })
}

export function usePositions() {
  return useQuery({ queryKey: ['positions'], queryFn: api.positions, refetchInterval: 5_000 })
}

export function usePerformance() {
  return useQuery({ queryKey: ['performance'], queryFn: api.performance, refetchInterval: 20_000 })
}
