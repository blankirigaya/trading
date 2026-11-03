import { useEffect, useMemo, useState } from 'react'

import type { Candle } from '@/api/types'
import { Badge, Empty } from '@/components/common/Primitives'
import { PageHeader } from '@/components/common/PageHeader'
import { DecisionDetail } from '@/components/decisions/DecisionDetail'
import { TradeChart, type ChartBar, type ChartMarker, type PriceLineSpec } from '@/components/market/TradeChart'
import { useDecisions } from '@/hooks/useDecisions'
import { useMarketCandles, useMarketSnapshots } from '@/hooks/useSystemStatus'
import { usePortfolio } from '@/hooks/usePortfolio'
import { useTradeHistory, useTrades } from '@/hooks/useTrades'
import { TIMEFRAMES, resampleCandles, type TimeframeId } from '@/utils/resample'
import {
  formatDateTime,
  formatPercent,
  formatPrice,
  formatQuantity,
  formatSignedInr,
  pctChange,
  toNumber,
} from '@/utils/format'

type SideFilter = 'ALL' | 'BUY' | 'SELL'
type OutcomeFilter = 'ALL' | 'WIN' | 'LOSS'
type StatusFilter = 'ALL' | 'OPEN' | 'CLOSED'

interface HistoryRow {
  key: string
  symbol: string
  side: string
  entryTime: string | null
  exitTime: string | null
  entryPrice: number | null
  exitPrice: number | null
  quantity: number
  holdingMs: number | null
  pnl: number | null
  status: 'OPEN' | 'CLOSED'
  decisionId: string | null
}

function toMinuteBars(candles: Candle[] | undefined): Array<{
  start_timestamp: string
  end_timestamp: string
  open: number
  high: number
  low: number
  close: number
  volume: number | null
}> {
  return (candles ?? [])
    .map((candle) => ({
      start_timestamp: candle.start_timestamp,
      end_timestamp: candle.end_timestamp,
      open: toNumber(candle.open),
      high: toNumber(candle.high),
      low: toNumber(candle.low),
      close: toNumber(candle.close),
      volume: candle.volume,
    }))
    .filter((bar) => [bar.open, bar.high, bar.low, bar.close].every((value) => Number.isFinite(value)) && !Number.isNaN(Date.parse(bar.start_timestamp)))
    .sort((a, b) => Date.parse(a.start_timestamp) - Date.parse(b.start_timestamp))
}

function holdingLabel(ms: number | null): string {
  if (ms === null || ms < 0) return '—'
  const minutes = Math.floor(ms / 60000)
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ${minutes % 60}m`
  return `${Math.floor(hours / 24)}d ${hours % 24}h`
}

export function Charts() {
  const { data: snapshots } = useMarketSnapshots()
  const { data: trades } = useTrades()
  const { data: history } = useTradeHistory()
  const { data: decisions } = useDecisions()
  const { data: portfolio } = usePortfolio()

  const symbols = useMemo(
    () => [...new Set((snapshots ?? []).map((s) => s.instrument.symbol))].sort(),
    [snapshots],
  )
  const [symbol, setSymbol] = useState<string | null>(null)
  const activeSymbol = symbol ?? symbols[0] ?? null
  const { data: symbolCandles } = useMarketCandles(activeSymbol)
  const [timeframe, setTimeframe] = useState<TimeframeId>('1m')
  const [sideFilter, setSideFilter] = useState<SideFilter>('ALL')
  const [outcomeFilter, setOutcomeFilter] = useState<OutcomeFilter>('ALL')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('ALL')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [selectedTradeKey, setSelectedTradeKey] = useState<string | null>(null)
  const [selectedDecisionId, setSelectedDecisionId] = useState<string | null>(null)
  const [selectedInstant, setSelectedInstant] = useState<string | null>(null)
  const [replayKey, setReplayKey] = useState<string | null>(null)
  const [replayStep, setReplayStep] = useState(0)
  const [replayPlaying, setReplayPlaying] = useState(false)
  const [visibleRange, setVisibleRange] = useState<{ from: string; to: string } | null>(null)

  const tfMinutes = TIMEFRAMES.find((tf) => tf.id === timeframe)?.minutes ?? 1

  const bars: ChartBar[] = useMemo(
    () =>
      resampleCandles(toMinuteBars(symbolCandles), tfMinutes).map((bar) => ({
        time: bar.time,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
        volume: bar.volume,
      })),
    [symbolCandles, tfMinutes],
  )

  const symbolFills = useMemo(
    () =>
      (trades ?? [])
        .filter((trade) => trade.symbol === activeSymbol)
        .filter((trade) => sideFilter === 'ALL' || trade.side === sideFilter)
        .filter((trade) => !dateFrom || trade.time.slice(0, 10) >= dateFrom)
        .filter((trade) => !dateTo || trade.time.slice(0, 10) <= dateTo)
        .slice()
        .sort((a, b) => a.time.localeCompare(b.time)),
    [trades, activeSymbol, sideFilter, dateFrom, dateTo],
  )

  const symbolDecisions = useMemo(
    () =>
      (decisions ?? [])
        .filter((record) => (record.symbol ?? record.initial_ai_proposal.proposals[0]?.symbol) === activeSymbol)
        .filter((record) => !dateFrom || record.timestamp.slice(0, 10) >= dateFrom)
        .filter((record) => !dateTo || record.timestamp.slice(0, 10) <= dateTo)
        .slice()
        .sort((a, b) => a.timestamp.localeCompare(b.timestamp)),
    [decisions, activeSymbol, dateFrom, dateTo],
  )

  const markers: ChartMarker[] = useMemo(() => {
    const items: ChartMarker[] = []
    for (const fill of symbolFills) {
      const isBuy = fill.side === 'BUY'
      items.push({
        time: fill.time,
        position: isBuy ? 'belowBar' : 'aboveBar',
        shape: isBuy ? 'arrowUp' : 'arrowDown',
        color: isBuy ? '#00D69A' : '#FF5364',
        text: `${isBuy ? 'BUY' : 'SELL'} @ ${formatPrice(fill.entry_price)}`,
      })
    }
    for (const record of symbolDecisions) {
      const action = record.final_ai_decision?.proposals[0]?.action ?? record.initial_ai_proposal.proposals[0]?.action ?? 'HOLD'
      const executed = record.orders_generated.length > 0
      // Executed decisions are already drawn as BUY/SELL fills above; only
      // unexecuted AI calls get a recommendation marker. Never a fake trade.
      if (executed || !record.market_timestamp) continue
      items.push({
        time: record.market_timestamp,
        position: 'belowBar',
        shape: 'circle',
        color: '#8B7CFF',
        text: action,
      })
      if (record.jev_critique && !record.jev_critique.agrees) {
        items.push({
          time: record.market_timestamp,
          position: 'aboveBar',
          shape: 'circle',
          color: '#F5B942',
          text: 'JEV CHALLENGE',
        })
      }
    }
    return items
  }, [symbolFills, symbolDecisions])

  const historyRows: HistoryRow[] = useMemo(() => {
    const rows: HistoryRow[] = []
    for (const trade of history?.closed_trades ?? []) {
      if (activeSymbol && trade.symbol !== activeSymbol) continue
      const pnl = toNumber(trade.realized_pnl)
      if (outcomeFilter === 'WIN' && !(pnl > 0)) continue
      if (outcomeFilter === 'LOSS' && !(pnl < 0)) continue
      if (statusFilter === 'OPEN') continue
      if (dateFrom && trade.closed_at.slice(0, 10) < dateFrom) continue
      if (dateTo && trade.closed_at.slice(0, 10) > dateTo) continue
      rows.push({
        key: `closed:${trade.closed_trade_id}`,
        symbol: trade.symbol,
        side: 'LONG',
        entryTime: trade.opened_at,
        exitTime: trade.closed_at,
        entryPrice: toNumber(trade.entry_price),
        exitPrice: toNumber(trade.exit_price),
        quantity: trade.quantity,
        holdingMs: trade.opened_at ? Date.parse(trade.closed_at) - Date.parse(trade.opened_at) : null,
        pnl,
        status: 'CLOSED',
        decisionId: trade.exit_decision_id,
      })
    }
    for (const position of portfolio?.positions ?? []) {
      if (position.quantity <= 0) continue
      if (activeSymbol && position.symbol !== activeSymbol) continue
      if (statusFilter === 'CLOSED') continue
      rows.push({
        key: `open:${position.symbol}`,
        symbol: position.symbol,
        side: 'LONG',
        entryTime: null,
        exitTime: null,
        entryPrice: toNumber(position.average_entry_price),
        exitPrice: null,
        quantity: position.quantity,
        holdingMs: null,
        pnl: null,
        status: 'OPEN',
        decisionId: null,
      })
    }
    return rows.sort((a, b) => (b.exitTime ?? b.entryTime ?? '').localeCompare(a.exitTime ?? a.entryTime ?? ''))
  }, [history, portfolio, activeSymbol, outcomeFilter, statusFilter, dateFrom, dateTo])

  const selectedTrade = historyRows.find((row) => row.key === selectedTradeKey) ?? null

  const lines: PriceLineSpec[] = useMemo(() => {
    if (!selectedTrade) {
      const open = historyRows.find((row) => row.status === 'OPEN' && row.symbol === activeSymbol)
      if (!open || open.entryPrice === null) return []
      return [
        {
          price: open.entryPrice,
          color: '#8495AA',
          lineWidth: 1,
          lineStyle: 2,
          title: `AVG ${formatPrice(open.entryPrice)} (open)`,
        },
      ]
    }
    const result: PriceLineSpec[] = []
    if (selectedTrade.status === 'OPEN') {
      if (selectedTrade.entryPrice !== null) {
        result.push({
          price: selectedTrade.entryPrice,
          color: '#8495AA',
          lineWidth: 1,
          lineStyle: 2,
          title: `AVG ${formatPrice(selectedTrade.entryPrice)} (open, no entry timestamp)`,
        })
      }
      return result
    }
    if (selectedTrade.entryPrice !== null) {
      result.push({ price: selectedTrade.entryPrice, color: '#00D69A', lineWidth: 2, lineStyle: 0, title: `ENTRY ${formatPrice(selectedTrade.entryPrice)}` })
    }
    if (selectedTrade.exitPrice !== null) {
      result.push({ price: selectedTrade.exitPrice, color: '#FF5364', lineWidth: 2, lineStyle: 0, title: `EXIT ${formatPrice(selectedTrade.exitPrice)}` })
    }
    // Recorded stop/target from the linked entry decision, if it named any.
    // Shown as recorded levels only — never claimed to have triggered.
    const linked = (decisions ?? []).find((record) => record.decision_id === selectedTrade.decisionId)
      ?? (decisions ?? []).find(
        (record) =>
          record.symbol === selectedTrade.symbol &&
          record.final_ai_decision?.proposals.some((proposal) => proposal.action === 'OPEN_POSITION'),
      )
    const proposal = linked?.final_ai_decision?.proposals[0] ?? linked?.initial_ai_proposal.proposals[0]
    if (proposal?.invalidation_price) {
      result.push({ price: toNumber(proposal.invalidation_price), color: '#F5B942', lineWidth: 1, lineStyle: 2, title: `STOP (recorded) ${formatPrice(proposal.invalidation_price)}` })
    }
    if (proposal?.target_price) {
      result.push({ price: toNumber(proposal.target_price), color: '#398BFF', lineWidth: 1, lineStyle: 2, title: `TARGET (recorded) ${formatPrice(proposal.target_price)}` })
    }
    return result
  }, [selectedTrade, historyRows, activeSymbol, decisions])

  const instantEvents = useMemo(() => {
    if (!selectedInstant) return []
    const at = Date.parse(selectedInstant)
    if (!Number.isFinite(at)) return []
    const span = tfMinutes * 60_000
    const bucket = Math.floor(at / span)
    const hits: Array<{ kind: 'FILL' | 'DECISION'; label: string; key: string }> = []
    for (const fill of symbolFills) {
      if (Math.floor(Date.parse(fill.time) / span) === bucket) {
        hits.push({ kind: 'FILL', label: `${fill.side} ${fill.quantity} @ ${formatPrice(fill.entry_price)}`, key: fill.trade_id })
      }
    }
    for (const record of symbolDecisions) {
      const stamp = record.market_timestamp ?? record.timestamp
      if (Math.floor(Date.parse(stamp) / span) === bucket) {
        const action = record.final_ai_decision?.proposals[0]?.action ?? record.initial_ai_proposal.proposals[0]?.action ?? 'HOLD'
        hits.push({ kind: 'DECISION', label: `${action} · ${formatPercent(record.final_ai_decision?.proposals[0]?.confidence ?? record.initial_ai_proposal.proposals[0]?.confidence ?? 0, 0)}`, key: record.decision_id })
      }
    }
    return hits
  }, [selectedInstant, symbolFills, symbolDecisions, tfMinutes])

  const selectedDecision = (decisions ?? []).find((record) => record.decision_id === selectedDecisionId) ?? null
  // --- trade replay ---------------------------------------------------------
  const replayTrade = historyRows.find((row) => row.key === replayKey && row.status === 'CLOSED') ?? null
  const replayWindow = useMemo(() => {
    if (!replayTrade?.entryTime || !replayTrade.exitTime) return null
    const entry = Date.parse(replayTrade.entryTime)
    const exit = Date.parse(replayTrade.exitTime)
    if (!Number.isFinite(entry) || !Number.isFinite(exit)) return null
    const from = new Date(entry - 30 * 60_000).toISOString()
    const to = new Date(exit + 15 * 60_000).toISOString()
    const window = bars.filter((bar) => bar.time >= from && bar.time <= to)
    if (window.length < 2) return null
    const firstBar = window[0]
    const lastBar = window[window.length - 1]
    if (!firstBar || !lastBar) return null
    return { from: firstBar.time, to: lastBar.time, bars: window }
  }, [replayTrade, bars])

  useEffect(() => {
    if (!replayPlaying || !replayWindow) return
    if (replayStep >= replayWindow.bars.length - 1) {
      setReplayPlaying(false)
      return
    }
    const timer = setTimeout(() => setReplayStep((step) => step + 1), 800)
    return () => clearTimeout(timer)
  }, [replayPlaying, replayStep, replayWindow])

  useEffect(() => {
    setReplayStep(0)
    setReplayPlaying(false)
    setVisibleRange(null)
  }, [replayKey])

  useEffect(() => {
    if (!replayWindow) return
    const upto = Math.min(replayStep, replayWindow.bars.length - 1)
    const bar = replayWindow.bars[upto]
    if (!bar) return
    setVisibleRange({ from: replayWindow.from, to: bar.time })
  }, [replayStep, replayWindow])

  const replayEvents = useMemo(() => {
    if (!replayWindow || !replayTrade) return []
    const upto = replayWindow.bars[Math.min(replayStep, replayWindow.bars.length - 1)]
    if (!upto) return []
    const cutoff = Date.parse(upto.time) + tfMinutes * 60_000
    const items: Array<{ time: string; label: string; kind: 'info' | 'entry' | 'exit' | 'decision' }> = []
    if (replayTrade.entryTime && Date.parse(replayTrade.entryTime) < cutoff) {
      items.push({ time: replayTrade.entryTime, label: `ENTRY ${replayTrade.quantity} @ ${replayTrade.entryPrice !== null ? formatPrice(replayTrade.entryPrice) : 'n/a'}`, kind: 'entry' })
    }
    for (const record of symbolDecisions) {
      const stamp = record.market_timestamp ?? record.timestamp
      const at = Date.parse(stamp)
      if (at >= Date.parse(replayWindow.from) && at < cutoff) {
        const action = record.final_ai_decision?.proposals[0]?.action ?? record.initial_ai_proposal.proposals[0]?.action ?? 'HOLD'
        items.push({ time: stamp, label: `AI ${action}`, kind: 'decision' })
      }
    }
    if (replayTrade.exitTime && Date.parse(replayTrade.exitTime) < cutoff) {
      items.push({ time: replayTrade.exitTime, label: `EXIT @ ${replayTrade.exitPrice !== null ? formatPrice(replayTrade.exitPrice) : 'n/a'} · P&L ${replayTrade.pnl !== null ? formatSignedInr(replayTrade.pnl) : 'n/a'}`, kind: 'exit' })
    }
    return items.sort((a, b) => a.time.localeCompare(b.time))
  }, [replayWindow, replayTrade, replayStep, symbolDecisions, tfMinutes])

  const snapshot = (snapshots ?? []).find((item) => item.instrument.symbol === activeSymbol)
  const change = snapshot ? pctChange(snapshot.last_price, snapshot.previous_close) : null

  return (
    <div>
      <PageHeader
        eyebrow="AI intelligence"
        title="Trade charts"
        sub="Real candles, real fills, real AI calls. Recommendations are never drawn as executed trades."
      />
      <div className="card">
        <div className="card-title">
          <span>Symbol &amp; timeframe</span>
          <span className="faint">candles refresh every 5s; last 240 one-minute bars retained</span>
        </div>
        <div className="filter-bar">
          <select value={activeSymbol ?? ''} onChange={(event) => { setSymbol(event.target.value || null); setSelectedTradeKey(null); setSelectedInstant(null); setVisibleRange(null) }} aria-label="Select symbol">
            {symbols.map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
          <span className="num" style={{ fontSize: 16 }}>
            {snapshot ? formatPrice(snapshot.last_price) : '—'}
            {' '}
            <span className={change !== null && change < 0 ? 'neg' : 'pos'}>
              {change === null ? '' : `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`}
            </span>
          </span>
          <span className="seg-group" role="group" aria-label="Select timeframe">
            {TIMEFRAMES.map((frame) => (
              <button
                key={frame.id}
                type="button"
                className={`btn${timeframe === frame.id ? ' primary' : ''}`}
                onClick={() => setTimeframe(frame.id)}
              >
                {frame.label}
              </button>
            ))}
          </span>
        </div>
      </div>

      <div className="card">
        <div className="card-title">
          <span>{activeSymbol ?? 'No symbol'} · {timeframe}{timeframe !== '1m' ? ' (resampled intraday)' : ''}</span>
          <Badge tone="neutral">{bars.length} candles</Badge>
        </div>
        {bars.length < 2 ? (
          <Empty>
            {timeframe === '1D'
              ? 'Daily view needs multiple sessions, but the backend retains only the last 240 one-minute bars. Intraday timeframes carry the full picture.'
              : 'No candles for this symbol yet. The backend retains only the most recent session.'}
          </Empty>
        ) : (
          <TradeChart
            bars={bars}
            volume
            markers={markers}
            lines={lines}
            testId="trade-chart"
            visibleRange={visibleRange}
            onBarClick={setSelectedInstant}
          />
        )}
        <div className="faint" style={{ marginTop: 8 }}>
          Green/red arrows are executed paper fills at recorded prices. Purple circles are AI
          recommendations that produced no order. Click any bar to inspect its recorded events.
        </div>
      </div>

      {selectedInstant ? (
        <div className="card" data-testid="chart-event-detail">
          <div className="card-title">
            <span>Recorded events · {selectedInstant.slice(0, 19).replace('T', ' ')}</span>
            <button type="button" className="btn" onClick={() => setSelectedInstant(null)}>Clear</button>
          </div>
          {instantEvents.length === 0 ? (
            <div className="faint">No recorded fill or AI call falls in this bar. Nothing was invented for it.</div>
          ) : (
            <table>
              <tbody>
                {instantEvents.map((event) => (
                  <tr key={`${event.kind}-${event.key}`}>
                    <td>
                      <Badge tone={event.kind === 'FILL' ? 'ok' : 'ai'}>
                        {event.kind === 'FILL' ? 'EXECUTED FILL' : 'AI RECOMMENDATION'}
                      </Badge>
                    </td>
                    <td>{event.label}</td>
                    <td>
                      {event.kind === 'DECISION' ? (
                        <button type="button" className="btn" onClick={() => setSelectedDecisionId(event.key)}>
                          Open reasoning
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : null}

      <div className="card">
        <div className="card-title">
          <span>Trade history</span>
          <Badge tone="neutral">{historyRows.length} rows</Badge>
        </div>
        <div className="filter-bar">
          <select value={outcomeFilter} onChange={(event) => setOutcomeFilter(event.target.value as OutcomeFilter)} aria-label="Filter by outcome">
            <option value="ALL">All outcomes</option>
            <option value="WIN">Wins</option>
            <option value="LOSS">Losses</option>
          </select>
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as StatusFilter)} aria-label="Filter by status">
            <option value="ALL">Open + closed</option>
            <option value="OPEN">Open only</option>
            <option value="CLOSED">Closed only</option>
          </select>
          <input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} aria-label="Filter from date" />
          <input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} aria-label="Filter to date" />
          <select value={sideFilter} onChange={(event) => setSideFilter(event.target.value as SideFilter)} aria-label="Filter markers by side">
            <option value="ALL">All markers</option>
            <option value="BUY">Buy fills</option>
            <option value="SELL">Sell fills</option>
          </select>
        </div>
        {historyRows.length === 0 ? (
          <Empty>No trades match these filters. Open positions and closed rounds appear here.</Empty>
        ) : (
          <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Side</th>
                <th>Entry time</th>
                <th>Exit time</th>
                <th className="num">Entry price</th>
                <th className="num">Exit price</th>
                <th className="num">Qty</th>
                <th>Holding</th>
                <th className="num">Realized P&amp;L</th>
                <th>Status</th>
                <th>Review</th>
              </tr>
            </thead>
            <tbody>
              {historyRows.map((row) => (
                <tr
                  key={row.key}
                  className={`clickable${selectedTradeKey === row.key ? ' selected' : ''}`}
                  onClick={() => {
                    setSelectedTradeKey(row.key)
                    setReplayKey(null)
                    if (row.status === 'CLOSED' && row.entryTime && row.exitTime) {
                      setVisibleRange({
                        from: new Date(Date.parse(row.entryTime) - 30 * 60_000).toISOString(),
                        to: new Date(Date.parse(row.exitTime) + 15 * 60_000).toISOString(),
                      })
                    } else {
                      setVisibleRange(null)
                    }
                  }}
                  data-testid="chart-trade-row"
                >
                  <td>{row.symbol}</td>
                  <td className="dim">{row.side}</td>
                  <td className="faint">{row.entryTime ? formatDateTime(row.entryTime) : '—'}</td>
                  <td className="faint">{row.exitTime ? formatDateTime(row.exitTime) : '—'}</td>
                  <td className="num">{row.entryPrice !== null ? formatPrice(row.entryPrice) : '—'}</td>
                  <td className="num">{row.exitPrice !== null ? formatPrice(row.exitPrice) : '—'}</td>
                  <td className="num">{formatQuantity(row.quantity)}</td>
                  <td className="faint">{holdingLabel(row.holdingMs)}</td>
                  <td className={`num ${row.pnl !== null && row.pnl < 0 ? 'neg' : 'pos'}`}>{row.pnl !== null ? formatSignedInr(row.pnl) : '—'}</td>
                  <td>
                    <Badge tone={row.status === 'OPEN' ? 'warn' : 'neutral'}>{row.status}</Badge>
                  </td>
                  <td>
                    {row.status === 'CLOSED' && row.entryTime ? (
                      <button
                        type="button"
                        className="btn"
                        onClick={(event) => { event.stopPropagation(); setReplayKey(row.key) }}
                      >
                        Replay
                      </button>
                    ) : (
                      <span className="faint">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </div>

      {replayTrade ? (
        <div className="card" data-testid="trade-replay">
          <div className="card-title">
            <span>
              Trade replay &middot; {replayTrade.symbol} {replayTrade.entryTime ? formatDateTime(replayTrade.entryTime) : ''} &rarr; {replayTrade.exitTime ? formatDateTime(replayTrade.exitTime) : ''}
            </span>
            <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <button
                type="button"
                className="btn"
                disabled={!replayWindow}
                onClick={() => {
                  if (replayPlaying) {
                    setReplayPlaying(false)
                  } else if (replayWindow && replayStep >= replayWindow.bars.length - 1) {
                    setReplayStep(0)
                    setReplayPlaying(true)
                  } else {
                    setReplayPlaying(true)
                  }
                }}
              >
                {replayPlaying ? 'Pause' : replayWindow ? `Play (${replayWindow.bars.length} candles)` : 'Play'}
              </button>
              <button type="button" className="btn" onClick={() => { setReplayKey(null); setReplayPlaying(false); setVisibleRange(null) }}>
                Close replay
              </button>
            </span>
          </div>
          {!replayWindow ? (
            <Empty>
              Candles for this trade&apos;s window are no longer retained (the backend keeps
              only the last 240 one-minute bars). The journalled entry, decision and exit
              facts below are still exact.
            </Empty>
          ) : (
            <>
              <input
                type="range"
                min={0}
                max={replayWindow.bars.length - 1}
                value={Math.min(replayStep, replayWindow.bars.length - 1)}
                onChange={(event) => { setReplayPlaying(false); setReplayStep(Number(event.target.value)) }}
                aria-label="Replay position"
                style={{ width: '100%' }}
              />
              <div className="faint" style={{ margin: '4px 0 10px' }}>
                Bar {Math.min(replayStep + 1, replayWindow.bars.length)} of {replayWindow.bars.length}
                {' · '}
                {(() => { const bar = replayWindow.bars[Math.min(replayStep, replayWindow.bars.length - 1)]; return bar ? `${formatDateTime(bar.time)} O ${formatPrice(bar.open)} H ${formatPrice(bar.high)} L ${formatPrice(bar.low)} C ${formatPrice(bar.close)}` : '' })()}
              </div>
            </>
          )}
          {replayEvents.length === 0 ? (
            <div className="faint">No recorded events at or before the cursor yet — only journalled facts appear here.</div>
          ) : (
            <table>
              <tbody>
                {replayEvents.map((event, index) => (
                  <tr key={`${event.time}-${index}`}>
                    <td className="faint">{formatDateTime(event.time)}</td>
                    <td>
                      <Badge tone={event.kind === 'entry' ? 'ok' : event.kind === 'exit' ? 'neutral' : event.kind === 'decision' ? 'ai' : 'neutral'}>
                        {event.kind === 'entry' ? 'ENTRY' : event.kind === 'exit' ? 'EXIT' : event.kind === 'decision' ? 'AI DECISION' : 'INFO'}
                      </Badge>
                    </td>
                    <td>{event.label}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : null}

      {selectedDecision ? (
        <>
          <div className="drawer-backdrop" onClick={() => setSelectedDecisionId(null)} />
          <aside className="drawer" data-testid="chart-decision-drawer">
            <div className="drawer-head">
              <h2>Decision reasoning</h2>
              <button type="button" className="btn" onClick={() => setSelectedDecisionId(null)}>
                Close
              </button>
            </div>
            <DecisionDetail record={selectedDecision} />
          </aside>
        </>
      ) : null}
    </div>
  )
}
