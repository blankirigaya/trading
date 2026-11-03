import { useEffect, useRef } from 'react'
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
} from 'lightweight-charts'

export interface ChartBar {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface ChartMarker {
  time: string
  position: 'aboveBar' | 'belowBar'
  shape: 'arrowUp' | 'arrowDown' | 'circle'
  color: string
  text: string
}

export interface PriceLineSpec {
  price: number
  color: string
  lineStyle: 0 | 1 | 2 | 3 | 4
  lineWidth: 1 | 2 | 3 | 4
  title: string
}

function toSeconds(iso: string): Time {
  return Math.floor(new Date(iso).getTime() / 1000) as Time
}

/**
 * Institutional trade chart: real candles, real volume, real AI/fill markers,
 * real recorded price levels. Clicking the chart reports the clicked bar time
 * so the page can surface the recorded events at that moment — never invented.
 */
export function TradeChart({
  bars,
  volume,
  markers,
  lines,
  visibleRange,
  testId,
  onBarClick,
}: {
  bars: ChartBar[]
  volume: boolean
  markers: ChartMarker[]
  lines: PriceLineSpec[]
  visibleRange?: { from: string; to: string } | null
  testId?: string
  onBarClick?: (isoTime: string) => void
}) {
  const host = useRef<HTMLDivElement | null>(null)
  const chart = useRef<IChartApi | null>(null)
  const candleSeries = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeSeries = useRef<ISeriesApi<'Histogram'> | null>(null)
  const clickHandler = useRef(onBarClick)
  clickHandler.current = onBarClick

  useEffect(() => {
    if (!host.current) return
    const instance = createChart(host.current, {
      layout: {
        background: { color: '#0C1420' },
        textColor: '#8495AA',
        fontSize: 11,
        fontFamily: "'JetBrains Mono', monospace",
      },
      grid: {
        vertLines: { color: 'rgba(36, 53, 71, 0.6)' },
        horzLines: { color: 'rgba(36, 53, 71, 0.6)' },
      },
      rightPriceScale: { borderColor: '#243247' },
      timeScale: { borderColor: '#243247', timeVisible: true, secondsVisible: false },
      crosshair: {
        vertLine: { color: '#398BFF', labelBackgroundColor: '#398BFF' },
        horzLine: { color: '#398BFF', labelBackgroundColor: '#398BFF' },
      },
      height: 380,
    })
    const candles = instance.addCandlestickSeries({
      upColor: '#00D69A',
      downColor: '#FF5364',
      borderVisible: false,
      wickUpColor: '#00D69A',
      wickDownColor: '#FF5364',
    })
    const histogram = instance.addHistogramSeries({
      priceScaleId: '',
      priceFormat: { type: 'volume' },
    })
    histogram.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
    instance.priceScale('right').applyOptions({ scaleMargins: { top: 0.06, bottom: 0.22 } })
    chart.current = instance
    candleSeries.current = candles
    volumeSeries.current = histogram

    const onClick = (param: { time?: Time | string }) => {
      if (param.time === undefined) return
      const iso =
        typeof param.time === 'string'
          ? param.time
          : new Date((param.time as number) * 1000).toISOString()
      clickHandler.current?.(iso)
    }
    instance.subscribeClick(onClick)

    const observer = new ResizeObserver(() => {
      if (host.current) instance.applyOptions({ width: host.current.clientWidth })
    })
    observer.observe(host.current)
    return () => {
      observer.disconnect()
      instance.unsubscribeClick(onClick)
      instance.remove()
      chart.current = null
      candleSeries.current = null
      volumeSeries.current = null
    }
  }, [])

  useEffect(() => {
    if (!candleSeries.current) return
    candleSeries.current.setData(
      bars.map((bar) => ({
        time: toSeconds(bar.time),
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      })),
    )
    if (!volumeSeries.current) return
    if (volume) {
      volumeSeries.current.setData(
        bars.map((bar) => ({
          time: toSeconds(bar.time),
          value: bar.volume,
          color: bar.close >= bar.open ? 'rgba(0, 210, 154, 0.45)' : 'rgba(255, 83, 100, 0.45)',
        })),
      )
    } else {
      volumeSeries.current.setData([])
    }
  }, [bars, volume])

  useEffect(() => {
    if (!candleSeries.current) return
    const payload: SeriesMarker<Time>[] = markers.map((marker) => ({
      time: toSeconds(marker.time),
      position: marker.position,
      shape: marker.shape,
      color: marker.color,
      text: marker.text,
    }))
    // De-duplicate identical markers so overlapping records never stack text.
    const seen = new Set<string>()
    candleSeries.current.setMarkers(
      payload.filter((marker) => {
        const key = `${marker.time}|${marker.position}|${marker.text}`;
        if (seen.has(key)) return false
        seen.add(key)
        return true
      }),
    )
  }, [markers])

  useEffect(() => {
    if (!candleSeries.current) return
    const series = candleSeries.current
    const created = lines.map((line) =>
      series.createPriceLine({
        price: line.price,
        color: line.color,
        lineWidth: line.lineWidth,
        lineStyle: line.lineStyle,
        axisLabelVisible: true,
        title: line.title,
      }),
    )
    return () => {
      for (const priceLine of created) series.removePriceLine(priceLine)
    }
  }, [lines])

  useEffect(() => {
    if (chart.current && bars.length && !visibleRange) chart.current.timeScale().scrollToRealTime()
  }, [bars.length, visibleRange])

  useEffect(() => {
    if (!chart.current || !visibleRange || bars.length === 0) return
    const fromMs = Date.parse(visibleRange.from)
    const toMs = Date.parse(visibleRange.to)
    if (!Number.isFinite(fromMs) || !Number.isFinite(toMs)) return
    const times = bars.map((bar) => Date.parse(bar.time))
    let first = -1
    let last = -1
    for (let index = 0; index < times.length; index += 1) {
      const time = times[index] ?? Number.NaN
      if (!Number.isFinite(time)) continue
      if (time >= fromMs && first === -1) first = index
      if (time <= toMs) last = index
      else if (time > toMs) break
    }
    if (first === -1 || last === -1) return
    chart.current.timeScale().setVisibleLogicalRange({
      from: Math.max(0, first - 2),
      to: Math.min(bars.length - 1, last + 2),
    })
  }, [visibleRange, bars])

  return <div className="chart-host trade-chart-host" ref={host} data-testid={testId} />
}
