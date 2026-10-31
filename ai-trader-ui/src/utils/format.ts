/**
 * Display formatting only. Nothing here computes trading intent, sizing, or
 * performance beyond straightforward arithmetic on backend-supplied values.
 */

/** Indian numbering system: 1,00,000 and 12,450.30 */
const inrFormatter = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

const usdFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 6,
})

const compactFormatter = new Intl.NumberFormat('en-IN', {
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
})

const tokenFormatter = new Intl.NumberFormat('en', {
  notation: 'compact',
  maximumFractionDigits: 1,
})

const priceFormatter = new Intl.NumberFormat('en-IN', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

export function toNumber(value: string | number | null | undefined): number {
  if (value === null || value === undefined) return 0
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

export function formatInr(value: string | number | null | undefined): string {
  return inrFormatter.format(toNumber(value))
}

export function formatPrice(value: string | number | null | undefined): string {
  return priceFormatter.format(toNumber(value))
}

export function formatInteger(value: string | number | null | undefined): string {
  return compactFormatter.format(toNumber(value))
}

export function formatQuantity(value: number | null | undefined): string {
  return compactFormatter.format(toNumber(value))
}

export function formatTokens(value: number | null | undefined): string {
  return tokenFormatter.format(toNumber(value))
}

export function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'n/a'
  return usdFormatter.format(value)
}

export function formatPercent(fraction: number | null | undefined, digits = 1): string {
  if (fraction === null || fraction === undefined) return 'n/a'
  return `${(fraction * 100).toFixed(digits)}%`
}

export function formatSignedInr(value: string | number | null | undefined): string {
  const amount = toNumber(value)
  const formatted = inrFormatter.format(Math.abs(amount))
  if (amount > 0) return `+${formatted}`
  if (amount < 0) return `-${formatted}`
  return formatted
}

export function formatSignedNumber(value: string | number | null | undefined, digits = 1): string {
  const amount = toNumber(value)
  if (amount > 0) return `+${amount.toFixed(digits)}`
  if (amount < 0) return amount.toFixed(digits)
  return (0).toFixed(digits)
}

export function formatLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return 'n/a'
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${ms} ms`
}

/** All timestamps are exchange time (Asia/Kolkata), regardless of browser locale. */
const IST = 'Asia/Kolkata'

function asUtc(iso: string): string {
  // Backend timestamps are UTC wall time; a missing offset means UTC, not local.
  return /([+-]\d{2}:?\d{2}|[zZ])$/.test(iso) ? iso : `${iso}Z`
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return 'n/a'
  const date = new Date(asUtc(iso))
  if (Number.isNaN(date.getTime())) return 'n/a'
  return date.toLocaleTimeString('en-GB', { hour12: false, timeZone: IST })
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return 'n/a'
  const date = new Date(asUtc(iso))
  if (Number.isNaN(date.getTime())) return 'n/a'
  return `${date.toLocaleDateString('en-GB', { timeZone: IST })} ${date.toLocaleTimeString('en-GB', { hour12: false, timeZone: IST })}`
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return 'n/a'
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
  const hours = Math.floor(seconds / 3600)
  return `${hours}h ${Math.floor((seconds % 3600) / 60)}m`
}

export function pctChange(current: string | number | null, base: string | number | null): number | null {
  const currentValue = toNumber(current)
  const baseValue = toNumber(base)
  if (!baseValue || baseValue === 0) return null
  return ((currentValue - baseValue) / baseValue) * 100
}
