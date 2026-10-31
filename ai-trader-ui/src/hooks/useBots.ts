import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'

export function useBots() {
  return useQuery({
    queryFn: api.bots,
    queryKey: ['bots'],
    refetchInterval: 10_000,
    refetchIntervalInBackground: true,
  })
}
