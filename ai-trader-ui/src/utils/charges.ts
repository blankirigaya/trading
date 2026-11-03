/**
 * Estimated SELL-side charges for closing a position at the displayed LTP.
 *
 * Mirrors the backend's Zerodha equity-intraday schedule component by
 * component (brokerage capped per order, STT on sells, exchange, SEBI, GST),
 * computed in integer paise with half-up rounding exactly like the broker —
 * so this estimate and the booked fill charges come from the same math.
 * Rates always come from `/charges/schedule`; never hardcode them here.
 */

import type { ChargeSchedule } from '@/api/types'

/** Round half up to whole paise (all inputs are non-negative). */
function paise(value: number): number {
  return Math.round(value)
}

export function estimateExitChargesInr(
  notionalInr: number,
  rates: NonNullable<ChargeSchedule['rates']>,
): number {
  if (!Number.isFinite(notionalInr) || notionalInr <= 0) return 0
  // Integer paise throughout, mirroring the backend quantize-after-multiply.
  // turnoverPaise already carries the x100, so rate multiplication lands
  // directly in paise — no further scaling before rounding.
  const turnoverPaise = Math.round(notionalInr * 100)
  const brokerage = Math.min(
    paise(turnoverPaise * Number(rates.brokerage_pct)),
    Math.round(Number(rates.brokerage_cap_inr) * 100),
  )
  const stt = paise(turnoverPaise * Number(rates.stt_sell_pct))
  const exchange = paise(turnoverPaise * Number(rates.exchange_pct))
  const sebi = paise(turnoverPaise * Number(rates.sebi_pct))
  const gst = paise((brokerage + exchange + sebi) * Number(rates.gst_pct))
  return paise(brokerage + stt + exchange + sebi + gst) / 100
}
