import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'

export function useTrades() {
  return useQuery({
    queryKey: ['trades'],
    queryFn: api.trades,
    refetchInterval: 10_000,
    refetchIntervalInBackground: true,
  })
}

/** Permanent closed-trade history with realized P&L; survives backend restarts. */
export function useTradeHistory() {
  return useQuery({
    queryKey: ['trades', 'history'],
    queryFn: api.tradeHistory,
    refetchInterval: 10_000,
    refetchIntervalInBackground: true,
  })
}

export function useTrade(tradeId: string | null) {
  return useQuery({
    queryKey: ['trades', tradeId],
    queryFn: () => api.trade(tradeId as string),
    enabled: Boolean(tradeId),
  })
}
