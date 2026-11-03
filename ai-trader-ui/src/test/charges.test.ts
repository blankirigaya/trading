import { describe, expect, it } from 'vitest'

import { estimateExitChargesInr } from '@/utils/charges'
import type { ChargeSchedule } from '@/api/types'

// Live Zerodha intraday schedule from /charges/schedule (matches .env).
const RATES = {
  brokerage_pct: '0.0003',
  brokerage_cap_inr: '20',
  stt_sell_pct: '0.00025',
  exchange_pct: '0.0000297',
  sebi_pct: '0.000001',
  stamp_duty_buy_pct: '0.00003',
  gst_pct: '0.18',
} satisfies NonNullable<ChargeSchedule['rates']>

describe('estimateExitChargesInr', () => {
  // Expected values computed by the backend's own compute_charges
  // (ai_trader/brokers/charges.py) for the same notionals.
  it.each([
    [5084.3, 3.26],
    [14741.1, 9.44],
    [10930.42, 6.99],
    [28329.0, 18.14],
    [24450.1, 15.66],
    [5782.5, 3.7],
    [10116.25, 6.47],
    [5001.4, 3.21],
    [23029.6, 14.74],
    [4984.7, 3.2],
    [7018.44, 4.5],
  ])('matches the backend for notional ₹%s (₹%s)', (notional, expected) => {
    expect(estimateExitChargesInr(notional, RATES)).toBeCloseTo(expected, 2)
  })

  it('is zero-safe on bad input', () => {
    expect(estimateExitChargesInr(0, RATES)).toBe(0)
    expect(estimateExitChargesInr(-10, RATES)).toBe(0)
    expect(estimateExitChargesInr(Number.NaN, RATES)).toBe(0)
  })

  it('caps brokerage per order like the schedule', () => {
    // ₹10,00,000 turnover: 0.03% would be ₹300, capped at ₹20.
    // Total: 20 + 250 + 29.70 + 1.00 + 9.13 = ₹309.83.
    expect(estimateExitChargesInr(1000000, RATES)).toBeCloseTo(309.83, 2)
  })
})
