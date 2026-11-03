import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'
import { estimateExitChargesInr } from '@/utils/charges'

/**
 * Active paper charge schedule for exit-cost estimates. Rates change only on
 * backend restart, so they are cached aggressively; every holdings table reads
 * through this hook so there is exactly one copy of the schedule.
 */
export function useChargeSchedule() {
  return useQuery({
    queryKey: ['charges', 'schedule'],
    queryFn: api.chargesSchedule,
    staleTime: 5 * 60_000,
    refetchInterval: false,
    retry: 1,
  })
}

/**
 * Estimated SELL-side charges for closing `notionalInr` of stock right now,
 * or null when the schedule is unavailable (backend offline, charges
 * disabled). Callers must render an unavailable marker, never zero.
 */
export function useExitCharges(notionalInr: number): number | null {
  const { data } = useChargeSchedule()
  if (!data?.enabled || !data.rates) return null
  if (!Number.isFinite(notionalInr) || notionalInr <= 0) return null
  return estimateExitChargesInr(notionalInr, data.rates)
}
