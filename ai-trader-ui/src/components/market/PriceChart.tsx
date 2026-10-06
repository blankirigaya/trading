import { useEffect, useRef } from 'react'
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
} from 'lightweight-charts'

import { useMarketCandles } from '@/hooks/useSystemStatus'

export interface CandleInput {
  time: string
  open: number
  high: number
  low: number
  close: number
}

export interface DecisionMarker {
  time: string
  position: 'aboveBar' | 'belowBar'
  shape: 'arrowUp' | 'arrowDown' | 'circle'
  color: string
  text: string
}

function toSeconds(iso: string): Time {
  return Math.floor(new Date(iso).getTime() / 1000) as Time
}

/** Candlestick chart for real FYERS prices, with backend-derived AI markers. */
export function PriceChart({
  candles,
  markers,
  testId,
}: {
  candles: CandleInput[]
  markers: DecisionMarker[]
  testId?: string
}) {
  const host = useRef<HTMLDivElement | null>(null)
  const chart = useRef<IChartApi | null>(null)
  const series = useRef<ISeriesApi<'Candlestick'> | null>(null)

  useEffect(() => {
    if (!host.current) return
    const instance = createChart(host.current, {
      layout: {
        background: { color: '#0e141d' },
        textColor: '#8496a8',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#161f2b' },
        horzLines: { color: '#161f2b' },
      },
      rightPriceScale: { borderColor: '#1e2a38' },
      timeScale: { borderColor: '#1e2a38', timeVisible: true },
      height: 320,
    })
    const candleSeries = instance.addCandlestickSeries({
      upColor: '#35c88a',
      downColor: '#f0576a',
      borderVisible: false,
      wickUpColor: '#35c88a',
      wickDownColor: '#f0576a',
    })
    chart.current = instance
    series.current = candleSeries

    const observer = new ResizeObserver(() => {
      if (host.current) instance.applyOptions({ width: host.current.clientWidth })
    })
    observer.observe(host.current)

    return () => {
      observer.disconnect()
      instance.remove()
      chart.current = null
      series.current = null
    }
  }, [])

  useEffect(() => {
    if (!series.current) return
    series.current.setData(
      candles.map((candle) => ({
        time: toSeconds(candle.time),
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
      })),
    )
  }, [candles])

  useEffect(() => {
    if (!series.current) return
    const payload: SeriesMarker<Time>[] = markers.map((marker) => ({
      time: toSeconds(marker.time),
      position: marker.position,
      shape: marker.shape,
      color: marker.color,
      text: marker.text,
    }))
    series.current.setMarkers(payload)
  }, [markers])

  return <div className="chart-host" ref={host} data-testid={testId} />
}

export function useCandleData(symbol: string | null) {
  const { data } = useMarketCandles(symbol)
  const candles: CandleInput[] = (data ?? []).map((candle) => ({
    time: candle.end_timestamp,
    open: Number(candle.open),
    high: Number(candle.high),
    low: Number(candle.low),
    close: Number(candle.close),
  }))
  return candles
}
