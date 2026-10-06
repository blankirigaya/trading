import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'

export function useNews(limit = 100) {
  return useQuery({
    queryKey: ['news', limit],
    queryFn: () => api.news(limit),
    refetchInterval: 20_000,
  })
}

export function useNewsSources() {
  return useQuery({ queryKey: ['newsSources'], queryFn: api.newsSources, refetchInterval: 60_000 })
}

/** Provider health is polled faster than the feed list because it changes on failure. */
export function useNewsProviders() {
  return useQuery({
    queryKey: ['newsProviders'],
    queryFn: api.newsProviders,
    refetchInterval: 5_000,
  })
}

export function useNewsLatency(limit = 25) {
  return useQuery({
    queryKey: ['newsLatency', limit],
    queryFn: () => api.newsLatency(limit),
    refetchInterval: 10_000,
  })
}