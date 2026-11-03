import { useMemo, useState } from 'react'

import { Badge, Empty } from '@/components/common/Primitives'
import { PageHeader } from '@/components/common/PageHeader'
import { PanelErrorBoundary } from '@/components/common/PanelErrorBoundary'
import { PriceChart, useCandleData, type DecisionMarker } from '@/components/market/PriceChart'
import { useDecisions } from '@/hooks/useDecisions'
import { useMarketSnapshots, useMarketStatus } from '@/hooks/useSystemStatus'
import { formatDateTime, formatPrice, pctChange } from '@/utils/format'

const MARKER_STYLE: Record<
  string,
  { position: 'aboveBar' | 'belowBar'; shape: 'arrowUp' | 'arrowDown' | 'circle'; color: string }
> = {
  OPEN_POSITION: { position: 'belowBar', shape: 'arrowUp', color: '#35c88a' },
  INCREASE_POSITION: { position: 'belowBar', shape: 'arrowUp', color: '#35c88a' },
  REDUCE_POSITION: { position: 'aboveBar', shape: 'arrowDown', color: '#e2b341' },
  CLOSE_POSITION: { position: 'aboveBar', shape: 'arrowDown', color: '#f0576a' },
}

function label(action: string): string {
  return action.replace('_POSITION', '')
}

/** Real FYERS prices for the watched universe, with backend AI decision markers. */
export function Market() {
  const { data: status } = useMarketStatus()
  const { data: snapshots } = useMarketSnapshots()
  const { data: decisions } = useDecisions()
  const symbols = status?.symbols ?? []
  const [selected, setSelected] = useState<string | null>(null)

  const activeSymbol = selected ?? (snapshots?.[0]?.instrument.symbol ?? null)
  const candles = useCandleData(activeSymbol)

  const markers = useMemo<DecisionMarker[]>(() => {
    if (!activeSymbol) return []
    return (decisions ?? [])
      .filter((record) => record.symbol === activeSymbol && record.market_timestamp)
      .flatMap((record) => {
        const proposal =
          record.final_ai_decision?.proposals[0] ?? record.initial_ai_proposal.proposals[0]
        const items: DecisionMarker[] = []
        if (proposal && MARKER_STYLE[proposal.action]) {
          const style = MARKER_STYLE[proposal.action]!
          items.push({
            time: record.market_timestamp as string,
            position: style.position,
            shape: style.shape,
            color: style.color,
            text: label(proposal.action),
          })
        }
        if (record.jev_critique && !record.jev_critique.agrees) {
          items.push({
            time: record.market_timestamp as string,
            position: 'aboveBar',
            shape: 'circle',
            color: '#e2b341',
            text: 'JEV CHALLENGE',
          })
        }
        return items
      })
  }, [decisions, activeSymbol])

  return (
    <div>
      <PageHeader
        eyebrow="Overview"
        title="Market"
        sub="Real FYERS prices with backend AI decision markers."
      />
      <div className="card">
        <div className="card-title">
          <span>Market data</span>
          <Badge tone="neutral">
            provider {status?.provider ?? 'fyers'} &mdash; market data only
          </Badge>
        </div>
        <div className="filter-bar">
          <div className="seg-group" role="group" aria-label="Select symbol">
            {(snapshots ?? []).map((snapshot) => (
              <button
                key={snapshot.instrument.symbol}
                type="button"
                className={`btn${activeSymbol === snapshot.instrument.symbol ? ' primary' : ''}`}
                onClick={() => setSelected(snapshot.instrument.symbol)}
              >
                {snapshot.instrument.symbol}
              </button>
            ))}
          </div>
          {!snapshots?.length ? <span className="faint">Watching: {symbols.join(', ')}</span> : null}
        </div>

        {snapshots?.length ? (
          <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th className="num">LTP</th>
                <th className="num">Change</th>
                <th className="num">Open</th>
                <th className="num">High</th>
                <th className="num">Low</th>
                <th className="num">Prev close</th>
                <th className="num">Volume</th>
                <th>Last update</th>
              </tr>
            </thead>
            <tbody>
              {snapshots.map((snapshot) => {
                const candle = snapshot.current_1m_candle
                const change = pctChange(snapshot.last_price, snapshot.previous_close)
                return (
                  <tr
                    key={snapshot.instrument.symbol}
                    className="clickable"
                    onClick={() => setSelected(snapshot.instrument.symbol)}
                  >
                    <td>
                      {snapshot.instrument.exchange}:{snapshot.instrument.symbol}-EQ
                    </td>
                    <td className="num">{formatPrice(snapshot.last_price)}</td>
                    <td className={`num ${change !== null && change < 0 ? 'neg' : 'pos'}`}>
                      {change === null ? 'n/a' : `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`}
                    </td>
                    <td className="num">{candle ? formatPrice(candle.open) : 'n/a'}</td>
                    <td className="num">{candle ? formatPrice(candle.high) : 'n/a'}</td>
                    <td className="num">{candle ? formatPrice(candle.low) : 'n/a'}</td>
                    <td className="num">
                      {snapshot.previous_close ? formatPrice(snapshot.previous_close) : 'n/a'}
                    </td>
                    <td className="num">{candle?.volume ?? 'n/a'}</td>
                    <td className="faint">{formatDateTime(snapshot.timestamp)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          </div>
        ) : (
          <Empty>
            No market data yet. The UI never contacts FYERS directly; it waits for the backend feed.
          </Empty>
        )}
      </div>

      {activeSymbol ? (
        <div className="card">
          <div className="card-title">
            <span>
              {activeSymbol} &mdash; 1 minute candles with AI decision markers
            </span>
            <span className="faint">{markers.length} markers</span>
          </div>
          {candles.length ? (
            <PanelErrorBoundary panel="Price chart">
              <PriceChart candles={candles} markers={markers} testId="market-chart" />
            </PanelErrorBoundary>
          ) : (
            <Empty>No candles yet for {activeSymbol}. Awaiting the backend feed.</Empty>
          )}
        </div>
      ) : null}
    </div>
  )
}
