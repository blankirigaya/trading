import { describe, expect, it } from 'vitest'

import { resampleCandles, type MinuteBarInput } from '@/utils/resample'

function bar(minute: number, open: number, high: number, low: number, close: number, volume = 100): MinuteBarInput {
  const base = Date.UTC(2026, 9, 30, 3, 30, 0)
  const start = new Date(base + minute * 60_000).toISOString()
  const end = new Date(base + (minute + 1) * 60_000).toISOString()
  return { start_timestamp: start, end_timestamp: end, open, high, low, close, volume }
}

describe('resampleCandles', () => {
  it('passes 1-minute bars through unchanged', () => {
    const bars = [bar(0, 10, 12, 9, 11), bar(1, 11, 13, 10, 12)]
    const out = resampleCandles(bars, 1)
    expect(out).toHaveLength(2)
    expect(out[0]).toMatchObject({ open: 10, high: 12, low: 9, close: 11, volume: 100 })
  })

  it('aggregates OHLCV into 5-minute buckets', () => {
    const bars = [
      bar(0, 10, 12, 9, 11, 100),
      bar(1, 11, 11.5, 10, 10.5, 50),
      bar(5, 10.5, 14, 10, 13, 200),
    ]
    const out = resampleCandles(bars, 5)
    expect(out).toHaveLength(2)
    expect(out[0]).toMatchObject({ open: 10, high: 12, low: 9, close: 10.5, volume: 150 })
    expect(out[1]).toMatchObject({ open: 10.5, high: 14, low: 10, close: 13, volume: 200 })
  })

  it('skips empty buckets instead of inventing candles', () => {
    const bars = [bar(0, 10, 10, 10, 10), bar(10, 20, 20, 20, 20)]
    expect(resampleCandles(bars, 5)).toHaveLength(2)
  })

  it('returns empty for empty input', () => {
    expect(resampleCandles([], 60)).toEqual([])
  })
})
