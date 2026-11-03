/** Honest OHLCV resampling of backend 1-minute candles.
 *
 * The backend retains at most the last 240 one-minute candles per symbol, so
 * higher timeframes are pure aggregations of those same candles — never
 * invented history. Buckets with no constituent candles are omitted, not
 * interpolated.
 */

export interface MinuteBarInput {
  start_timestamp: string
  end_timestamp: string
  open: number
  high: number
  low: number
  close: number
  volume: number | null
}

export interface ResampledBar {
  /** Bucket open time (ISO). */
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export const TIMEFRAMES = [
  { id: '1m', label: '1m', minutes: 1 },
  { id: '5m', label: '5m', minutes: 5 },
  { id: '15m', label: '15m', minutes: 15 },
  { id: '30m', label: '30m', minutes: 30 },
  { id: '1H', label: '1H', minutes: 60 },
  { id: '1D', label: 'Daily', minutes: 1440 },
] as const

export type TimeframeId = (typeof TIMEFRAMES)[number]['id']

export function resampleCandles(bars: MinuteBarInput[], minutes: number): ResampledBar[] {
  if (minutes <= 1 || bars.length === 0) {
    return bars.map((bar) => ({
      time: bar.start_timestamp,
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
      volume: bar.volume ?? 0,
    }))
  }
  const buckets = new Map<number, MinuteBarInput[]>()
  for (const bar of bars) {
    const start = Date.parse(bar.start_timestamp)
    if (!Number.isFinite(start)) continue
    const key = Math.floor(start / (minutes * 60_000))
    const group = buckets.get(key)
    if (group) group.push(bar)
    else buckets.set(key, [bar])
  }
  return [...buckets.entries()]
    .sort((a, b) => a[0] - b[0])
    .flatMap(([, group]) => {
      const first = group[0]
      const last = group[group.length - 1]
      if (!first || !last) return []
      return [{
        time: first.start_timestamp,
        open: first.open,
        high: Math.max(...group.map((bar) => bar.high)),
        low: Math.min(...group.map((bar) => bar.low)),
        close: last.close,
        volume: group.reduce((sum, bar) => sum + (bar.volume ?? 0), 0),
      }]
    })
}
