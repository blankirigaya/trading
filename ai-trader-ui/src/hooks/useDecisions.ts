import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'

export function useDecisions() {
  return useQuery({
    queryKey: ['decisions'],
    queryFn: api.decisions,
    refetchInterval: 10_000,
    refetchIntervalInBackground: true,
  })
}

export function useDecision(decisionId: string | null) {
  return useQuery({
    queryKey: ['decisions', decisionId],
    queryFn: () => api.decision(decisionId as string),
    enabled: Boolean(decisionId),
  })
}
