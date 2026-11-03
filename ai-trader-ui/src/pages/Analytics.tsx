import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import type { ClosedTradeRow, DecisionRecord } from '@/api/types'
import { Stat } from '@/components/common/Primitives'
import { EquityCurve } from '@/components/common/EquityCurve'
import { PageHeader } from '@/components/common/PageHeader'
import { useDecisions } from '@/hooks/useDecisions'
import { usePortfolio } from '@/hooks/usePortfolio'
import { useSystemStatus } from '@/hooks/useSystemStatus'
import { useTradeHistory } from '@/hooks/useTrades'
import {
  adverseSlippageBps,
  currentDrawdown,
  dailyPerformance,
  decisionConversion,
  dedupeClosedTrades,
  holdingDurations,
  maxDrawdown,
  realizedEquityPoints,
  streaks,
  summarizeTrades,
  symbolPerformance,
} from '@/analytics/metrics'
import { formatInr, formatSignedInr, toNumber } from '@/utils/format'

type Range = 'today' | '7d' | '30d' | '90d' | 'all'
type Outcome = 'all' | 'win' | 'loss' | 'breakeven'

const RANGES: { id: Range; label: string }[] = [
  { id: 'today', label: 'Today' },
  { id: '7d', label: '7D' },
  { id: '30d', label: '30D' },
  { id: '90d', label: '90D' },
  { id: 'all', label: 'All time' },
]

function rangeCutoff(range: Range, now: number): number | null {
  switch (range) {
    case 'today': {
      const d = new Date(now)
      return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate())
    }
    case '7d':
      return now - 7 * 86_400_000
    case '30d':
      return now - 30 * 86_400_000
    case '90d':
      return now - 90 * 86_400_000
    case 'all':
      return null
  }
}

function StatCard({ label, value, tone }: { label: string; value: string; tone?: 'pos' | 'neg' }) {
  return <Stat label={label} value={value} tone={tone} />
}

function HeroCard({ label, value, tone }: { label: string; value: string; tone?: 'pos' | 'neg' }) {
  return <Stat hero label={label} value={value} tone={tone} />
}

function Unavailable({ label, reason }: { label: string; reason: string }) {
  return (
    <div className="card" data-testid={`unavailable-${label}`}>
      <div className="card-title">{label}</div>
      <div className="faint">Insufficient historical data. {reason}</div>
    </div>
  )
}

function fmtPct(pct: number | null): string {
  return pct === null ? '—' : `${pct.toFixed(1)}%`
}

function fmtFactor(factor: number | null): string {
  if (factor === null) return '—'
  if (!Number.isFinite(factor)) return '∞'
  return factor.toFixed(2)
}

function holdingLabel(ms: number | null): string {
  if (ms === null || ms < 0 || !Number.isFinite(ms)) return '—'
  const minutes = Math.floor(ms / 60_000)
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ${minutes % 60}m`
  return `${Math.floor(hours / 24)}d ${hours % 24}h`
}

function outcomeOf(trade: ClosedTradeRow): 'win' | 'loss' | 'breakeven' {
  const pnl = toNumber(trade.realized_pnl)
  if (pnl > 0) return 'win'
  if (pnl < 0) return 'loss'
  return 'breakeven'
}

/** P&L relative to entry notional; null when the notional is not computable. */
function returnOnNotional(trade: ClosedTradeRow): number | null {
  const notional = toNumber(trade.entry_price) * trade.quantity
  if (!(notional > 0) || !Number.isFinite(notional)) return null
  return (toNumber(trade.realized_pnl) / notional) * 100
}

function downloadCsv(filename: string, rows: string[][]) {
  const body = rows
    .map((row) => row.map((cell) => `"${cell.replace(/"/g, '""')}"`).join(','))
    .join('\n')
  const blob = new Blob([body], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

export function Analytics() {
  const { data: history } = useTradeHistory()
  const { data: decisions } = useDecisions()
  const { data: portfolio } = usePortfolio()
  const { data: status } = useSystemStatus()

  const [range, setRange] = useState<Range>('all')
  const [symbol, setSymbol] = useState<string>('all')
  const [outcome, setOutcome] = useState<Outcome>('all')
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [symbolSort, setSymbolSort] = useState<'pnl' | 'winRate' | 'trades' | 'profitFactor'>('pnl')
  const [tradeSort, setTradeSort] = useState<'closed_at' | 'realized_pnl' | 'symbol'>('closed_at')
  const [tradeDir, setTradeDir] = useState<'asc' | 'desc'>('desc')
  const [page, setPage] = useState(0)

  const now = Date.now()
  const cutoff = rangeCutoff(range, now)

  const closedAll = useMemo(() => dedupeClosedTrades(history?.closed_trades ?? []), [history])
  const symbols = useMemo(
    () => [...new Set(closedAll.map((t) => t.symbol))].sort(),
    [closedAll],
  )

  const closed = useMemo(() => {
    return closedAll.filter((t) => {
      if (cutoff !== null) {
        const ts = Date.parse(t.closed_at)
        if (!Number.isFinite(ts) || ts < cutoff) return false
      }
      if (symbol !== 'all' && t.symbol !== symbol) return false
      if (outcome !== 'all' && outcomeOf(t) !== outcome) return false
      return true
    })
  }, [closedAll, cutoff, symbol, outcome])

  // Reset pagination whenever the filtered set changes.
  useEffect(() => {
    setPage(0)
  }, [closedAll, cutoff, symbol, outcome, tradeSort, tradeDir])

  const summary = useMemo(() => summarizeTrades(closed), [closed])
  const charges = useMemo(
    () => closed.reduce((s, t) => s + toNumber(t.charges), 0),
    [closed],
  )
  const gross = useMemo(
    () => closed.reduce((s, t) => s + toNumber(t.gross_pnl), 0),
    [closed],
  )
  const holds = useMemo(() => holdingDurations(closed), [closed])
  const streak = useMemo(() => streaks(closed), [closed])
  const days = useMemo(() => dailyPerformance(closed), [closed])
  const symbolsRows = useMemo(() => {
    const rows = symbolPerformance(closed)
    const sorted = [...rows]
    switch (symbolSort) {
      case 'pnl':
        sorted.sort((a, b) => b.totalPnl - a.totalPnl)
        break
      case 'winRate':
        sorted.sort((a, b) => (b.winRatePct ?? -1) - (a.winRatePct ?? -1))
        break
      case 'trades':
        sorted.sort((a, b) => b.trades - a.trades)
        break
      case 'profitFactor': {
        const rank = (v: number | null) => (v === null ? -1 : v)
        sorted.sort((a, b) => rank(b.profitFactor) - rank(a.profitFactor))
        break
      }
    }
    return sorted
  }, [closed, symbolSort])

  const largestGain = useMemo(
    () => days.reduce<number | null>((m, d) => (m === null ? d.pnl : Math.max(m, d.pnl)), null),
    [days],
  )
  const largestLossDay = useMemo(
    () => days.reduce<number | null>((m, d) => (m === null ? d.pnl : Math.min(m, d.pnl)), null),
    [days],
  )

  // --- Realized equity curve (closed-trade ledger; filtered by the same range).
  // Journalled portfolio_after snapshots are per-shard books, so they cannot
  // be plotted against total starting capital. The permanent combined ledger
  // can: starting capital plus cumulative realized P&L in close order.
  const startingCapital = toNumber(status?.shadow_initial_capital_inr ?? '100000')
  const curvePoints = useMemo(() => {
    const pts = realizedEquityPoints(closed, startingCapital)
    if (cutoff === null) return pts
    return pts.filter((p) => {
      const ts = Date.parse(p.time)
      return Number.isFinite(ts) && ts >= cutoff
    })
  }, [closed, cutoff, startingCapital])
  const ddMax = useMemo(() => maxDrawdown(curvePoints), [curvePoints])
  const ddNow = useMemo(() => currentDrawdown(curvePoints), [curvePoints])
  const ddAvg = useMemo(() => {
    if (curvePoints.length < 2) return null
    let peak = curvePoints[0]?.equity ?? 0
    let sum = 0
    let n = 0
    for (const p of curvePoints) {
      if (!Number.isFinite(p.equity)) continue
      if (p.equity > peak) peak = p.equity
      sum += peak - p.equity
      n += 1
    }
    return n > 0 ? sum / n : null
  }, [curvePoints])

  // --- AI decision intelligence (record-established links only) ---
  const decisionsInScope = useMemo(() => {
    const list = decisions ?? []
    if (cutoff === null) return list
    return list.filter((d) => {
      const ts = Date.parse(d.timestamp)
      return Number.isFinite(ts) && ts >= cutoff
    })
  }, [decisions, cutoff])
  const actionCounts = useMemo(() => {
    let buy = 0
    let sell = 0
    let hold = 0
    let other = 0
    for (const d of decisionsInScope) {
      for (const proposal of d.initial_ai_proposal.proposals) {
        const action = proposal.action.trim().toUpperCase()
        if (action === 'BUY') buy += 1
        else if (action === 'SELL') sell += 1
        else if (action === 'HOLD') hold += 1
        else other += 1
      }
    }
    return { buy, sell, hold, other }
  }, [decisionsInScope])
  const conversion = useMemo(() => decisionConversion(decisionsInScope), [decisionsInScope])

  const decisionById = useMemo(() => {
    const map = new Map<string, DecisionRecord>()
    for (const d of decisions ?? []) map.set(d.decision_id, d)
    return map
  }, [decisions])

  const decisionDelay = useMemo(() => {
    let sum = 0
    let n = 0
    let unlinked = 0
    for (const t of closed) {
      if (!t.entry_decision_id || !t.opened_at) {
        unlinked += 1
        continue
      }
      const d = decisionById.get(t.entry_decision_id)
      if (!d) {
        unlinked += 1
        continue
      }
      const opened = Date.parse(t.opened_at)
      const decided = Date.parse(d.timestamp)
      if (!Number.isFinite(opened) || !Number.isFinite(decided) || opened < decided) {
        unlinked += 1
        continue
      }
      sum += opened - decided
      n += 1
    }
    return { avgMs: n > 0 ? sum / n : null, linked: n, unlinked }
  }, [closed, decisionById])

  // --- Execution quality: entry fill vs the proposal's desired entry price ---
  const executionQuality = useMemo(() => {
    let sumBps = 0
    let n = 0
    let adverse = 0
    let favorable = 0
    let missingRef = 0
    let unlinked = 0
    for (const t of closed) {
      const d = t.entry_decision_id ? decisionById.get(t.entry_decision_id) : undefined
      if (!d) {
        unlinked += 1
        continue
      }
      const proposal = d.initial_ai_proposal.proposals.find((p) => p.symbol === t.symbol)
      const ref = proposal ? toNumber(proposal.desired_entry_price) : 0
      const exec = toNumber(t.entry_price)
      const side = (proposal?.side ?? proposal?.action ?? '').trim().toUpperCase()
      const bps =
        ref > 0 && exec > 0 && (side === 'BUY' || side === 'SELL')
          ? adverseSlippageBps(side, ref, exec)
          : null
      if (bps === null) {
        missingRef += 1
        continue
      }
      sumBps += bps
      n += 1
      if (bps > 0) adverse += 1
      else if (bps < 0) favorable += 1
    }
    return { avgBps: n > 0 ? sumBps / n : null, linked: n, adverse, favorable, missingRef, unlinked }
  }, [closed, decisionById])

  // --- Distributions ---
  const hist = useMemo(() => {
    if (closed.length === 0) return []
    const pnls = closed.map((t) => toNumber(t.realized_pnl))
    const min = Math.min(...pnls)
    const max = Math.max(...pnls)
    const bins = 12
    const span = max - min || 1
    const counts = new Array<number>(bins).fill(0)
    for (const pnl of pnls) {
      const idx = Math.min(bins - 1, Math.floor(((pnl - min) / span) * bins))
      counts[idx] = (counts[idx] ?? 0) + 1
    }
    return counts.map((count, i) => ({
      count,
      lo: min + (span / bins) * i,
      hi: min + (span / bins) * (i + 1),
    }))
  }, [closed])
  const histMax = hist.reduce((m, b) => Math.max(m, b.count), 0)

  const cumulative = useMemo(() => {
    const ordered = [...closed].sort((a, b) => Date.parse(a.closed_at) - Date.parse(b.closed_at))
    let running = 0
    return ordered.map((t) => {
      running += toNumber(t.realized_pnl)
      return { time: t.closed_at, value: running }
    })
  }, [closed])

  // --- Trade table ---
  const tableRows = useMemo(() => {
    const rows = [...closed]
    rows.sort((a, b) => {
      let cmp: number
      if (tradeSort === 'realized_pnl') cmp = toNumber(a.realized_pnl) - toNumber(b.realized_pnl)
      else if (tradeSort === 'symbol') cmp = a.symbol.localeCompare(b.symbol)
      else cmp = Date.parse(a.closed_at) - Date.parse(b.closed_at)
      return tradeDir === 'asc' ? cmp : -cmp
    })
    return rows
  }, [closed, tradeSort, tradeDir])
  const pageSize = 20
  const pageCount = Math.max(1, Math.ceil(tableRows.length / pageSize))
  const safePage = Math.min(page, pageCount - 1)
  const pageRows = tableRows.slice(safePage * pageSize, safePage * pageSize + pageSize)

  const selectedDayTrades = useMemo(() => {
    if (!selectedDate) return []
    return closed
      .filter((t) => {
        const ts = Date.parse(t.closed_at)
        if (!Number.isFinite(ts)) return false
        return new Date(ts).toISOString().slice(0, 10) === selectedDate
      })
      .sort((a, b) => Date.parse(a.closed_at) - Date.parse(b.closed_at))
  }, [closed, selectedDate])

  const scopeLabel = useMemo(() => {
    const parts = [RANGES.find((r) => r.id === range)?.label ?? range]
    parts.push(symbol === 'all' ? 'All symbols' : symbol)
    parts.push(outcome === 'all' ? 'All outcomes' : outcome)
    return parts.join(' · ')
  }, [range, symbol, outcome])

  const exportCsv = () => {
    const header = [
      'symbol', 'quantity', 'entry_time', 'exit_time', 'entry_price', 'exit_price',
      'realized_pnl_inr', 'charges_inr', 'gross_pnl_inr', 'return_on_notional_pct',
      'outcome', 'entry_decision_id', 'exit_decision_id',
    ]
    const rows = closed.map((t) => {
      const ret = returnOnNotional(t)
      return [
        t.symbol, String(t.quantity), t.opened_at ?? '', t.closed_at, t.entry_price,
        t.exit_price, t.realized_pnl, t.charges, t.gross_pnl,
        ret === null ? '' : ret.toFixed(2), outcomeOf(t),
        t.entry_decision_id ?? '', t.exit_decision_id,
      ]
    })
    downloadCsv(`analytics-${range}-${symbol}-${outcome}.csv`, [header, ...rows])
  }

  const openPositions = portfolio?.positions?.filter((p) => p.quantity > 0).length ?? 0
  const dailyMax = days.reduce((m, d) => Math.max(m, Math.abs(d.pnl)), 0) || 1

  return (
    <div>
      <PageHeader
        eyebrow="AI intelligence"
        title="Analytics"
        sub="Quantitative trading analytics computed strictly from recorded history. Unavailable metrics are marked, never estimated."
        actions={
          <button className="btn" onClick={exportCsv} disabled={closed.length === 0}>
            Export filtered CSV
          </button>
        }
      />

      <div className="card" style={{ marginBottom: 12 }}>
        <div className="filter-bar">
          <div className="seg-group" role="group" aria-label="Date range">
            {RANGES.map((r) => (
              <button
                key={r.id}
                className="btn"
                data-testid={`range-${r.id}`}
                aria-pressed={range === r.id}
                disabled={range === r.id}
                onClick={() => setRange(r.id)}
              >
                {r.label}
              </button>
            ))}
          </div>
          <label className="faint">
            Symbol{' '}
            <select
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              data-testid="filter-symbol"
            >
              <option value="all">All symbols</option>
              {symbols.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label className="faint">
            Outcome{' '}
            <select
              value={outcome}
              onChange={(e) => setOutcome(e.target.value as Outcome)}
              data-testid="filter-outcome"
            >
              <option value="all">All outcomes</option>
              <option value="win">Wins</option>
              <option value="loss">Losses</option>
              <option value="breakeven">Breakeven</option>
            </select>
          </label>
        </div>
        <div className="faint" data-testid="analytics-scope">
          Showing {closed.length} of {closedAll.length} completed trades · {scopeLabel}. Open
          positions and unexecuted recommendations are always excluded from realized statistics.
        </div>
      </div>

      <div className="kpi-hero">
        <HeroCard
          label="Total realized P&L"
          value={summary.total > 0 ? formatSignedInr(summary.totalPnl) : '—'}
          tone={summary.totalPnl < 0 ? 'neg' : 'pos'}
        />
        <HeroCard label="Win rate" value={fmtPct(summary.winRatePct)} />
        <HeroCard label="Profit factor" value={fmtFactor(summary.profitFactor)} />
        <HeroCard
          label="Expectancy / trade"
          value={summary.expectancy === null ? '—' : formatSignedInr(summary.expectancy)}
          tone={(summary.expectancy ?? 0) < 0 ? 'neg' : 'pos'}
        />
      </div>

      <div className="stat-grid">
        <StatCard label="Total completed trades" value={String(summary.total)} />
        <StatCard label="Open positions (excluded)" value={String(openPositions)} />
        <StatCard
          label="Average win"
          value={summary.avgWin === null ? '—' : formatSignedInr(summary.avgWin)}
        />
        <StatCard
          label="Average loss"
          value={summary.avgLossMagnitude === null ? '—' : formatSignedInr(-summary.avgLossMagnitude)}
        />
        <StatCard label="Charges paid" value={formatInr(charges)} />
        <StatCard label="Gross realized P&L" value={formatSignedInr(gross)} />
        <StatCard label="Total decisions" value={String(decisionsInScope.length)} />
        <StatCard
          label="Max realized drawdown"
          value={ddMax !== null ? formatInr(ddMax.amount) : '—'}
        />
      </div>

      <div className="card">
        <div className="card-title">Equity curve</div>
        {curvePoints.length >= 2 ? (
          <EquityCurve
            points={curvePoints}
            startingCapital={startingCapital}
            basisLabel="Realized equity (closed trades)"
          />
        ) : (
          <Unavailable
            label="Equity curve"
            reason="Need at least two closed trades in the selected range."
          />
        )}
        <div className="faint" style={{ marginTop: 8 }}>
          Starting capital plus cumulative realized P&amp;L from the permanent closed-trade
          ledger ({formatInr(startingCapital)} start). Journalled per-decision snapshots are
          per-shard books and cannot be plotted against total starting capital. Open-position
          (unrealized) value is not in this curve — see Portfolio for live valuation. Drawdown
          windows are peak-to-trough declines on this realized series — not estimated.
        </div>
      </div>

      <div className="card">
        <div className="card-title">Daily performance</div>
        {days.length === 0 ? (
          <Unavailable
            label="Daily performance"
            reason="No completed trades in the selected scope."
          />
        ) : (
          <>
            <div
              style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}
              data-testid="daily-heatmap"
            >
              {days.map((d) => (
                <button
                  key={d.date}
                  title={`${d.date}: ${formatSignedInr(d.pnl)} · ${d.trades} trades · win ${fmtPct(d.winRatePct)}`}
                  onClick={() => setSelectedDate((cur) => (cur === d.date ? null : d.date))}
                  aria-pressed={selectedDate === d.date}
                  style={{
                    width: 26,
                    height: 26,
                    borderRadius: 4,
                    border: `1px solid ${selectedDate === d.date ? 'var(--accent)' : 'var(--border-strong)'}`,
                    background:
                      d.pnl > 0
                        ? 'rgba(46,160,67,0.35)'
                        : d.pnl < 0
                          ? 'rgba(220,60,60,0.35)'
                          : 'transparent',
                    cursor: 'pointer',
                  }}
                />
              ))}
            </div>
            <div className="faint" style={{ marginTop: 6 }}>
              Green = profitable day, red = losing day. Days without completed trades are omitted —
              missing data is never shown as zero. Click a day to list its trades.
            </div>
            <div className="stat-grid" style={{ marginTop: 10 }}>
              <StatCard
                label="Best day"
                value={largestGain === null ? '—' : formatSignedInr(largestGain)}
                tone="pos"
              />
              <StatCard
                label="Worst day"
                value={largestLossDay === null ? '—' : formatSignedInr(largestLossDay)}
                tone="neg"
              />
              <StatCard label="Trading days" value={String(days.length)} />
              <StatCard
                label="Avg profit / trade-day"
                value={
                  days.length
                    ? formatSignedInr(days.reduce((s, d) => s + d.pnl, 0) / days.length)
                    : '—'
                }
              />
            </div>
            {selectedDate && (
              <div style={{ marginTop: 10 }}>
                <div className="card-title">
                  Trades on {selectedDate} ({selectedDayTrades.length})
                </div>
                {selectedDayTrades.length === 0 ? (
                  <div className="faint">No completed trades that day in this scope.</div>
                ) : (
                  <table>
                    <thead>
                      <tr>
                        <th>Symbol</th>
                        <th className="num">Qty</th>
                        <th className="num">Entry</th>
                        <th className="num">Exit</th>
                        <th className="num">Realized P&L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedDayTrades.map((t) => (
                        <tr key={t.closed_trade_id}>
                          <td>{t.symbol}</td>
                          <td className="num">{t.quantity}</td>
                          <td className="num">{t.entry_price}</td>
                          <td className="num">{t.exit_price}</td>
                          <td className={`num ${toNumber(t.realized_pnl) < 0 ? 'neg' : 'pos'}`}>
                            {formatSignedInr(toNumber(t.realized_pnl))}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}
          </>
        )}
      </div>

      <div className="card">
        <div className="card-title">Trade performance breakdown</div>
        {summary.total === 0 ? (
          <Unavailable
            label="Trade breakdown"
            reason="No completed trades in the selected scope."
          />
        ) : (
          <div className="stat-grid">
            <StatCard label="Winning trades" value={String(summary.wins)} />
            <StatCard label="Losing trades" value={String(summary.losses)} />
            <StatCard label="Breakeven trades" value={String(summary.breakeven)} />
            <StatCard
              label="Largest win"
              value={summary.largestWin === null ? '—' : formatSignedInr(summary.largestWin)}
              tone="pos"
            />
            <StatCard
              label="Largest loss"
              value={summary.largestLoss === null ? '—' : formatSignedInr(summary.largestLoss)}
              tone="neg"
            />
            <StatCard
              label="Avg holding"
              value={holdingLabel(holds.avgMs)}
            />
            <StatCard label="Longest hold" value={holdingLabel(holds.maxMs)} />
            <StatCard label="Shortest hold" value={holdingLabel(holds.minMs)} />
            <StatCard label="Best win streak" value={String(streak.maxWinStreak)} />
            <StatCard label="Worst loss streak" value={String(streak.maxLossStreak)} />
            <StatCard
              label="Durations measured"
              value={`${holds.count} of ${summary.total} trades`}
            />
            <StatCard
              label="Partials / fills"
              value="—"
            />
          </div>
        )}
        <div className="faint" style={{ marginTop: 8 }}>
          Completed round trips only. Partially closed positions appear only when the ledger emits a
          closed round trip; per-fill and partial-fill records are not retained, so no fill-level
          aggregation is available. Durations need both entry and exit timestamps.
        </div>
      </div>

      <div className="card">
        <div className="card-title">Symbol performance</div>
        {symbolsRows.length === 0 ? (
          <Unavailable
            label="Symbol performance"
            reason="No closed trades in the selected scope."
          />
        ) : (
          <>
            <div className="filter-bar">
              <div className="seg-group" role="group" aria-label="Sort symbols">
                {(
                  [
                    ['pnl', 'Profit'],
                    ['winRate', 'Win rate'],
                    ['trades', 'Trades'],
                    ['profitFactor', 'Profit factor'],
                  ] as const
                ).map(([id, label]) => (
                  <button
                    key={id}
                    className="btn"
                    aria-pressed={symbolSort === id}
                    disabled={symbolSort === id}
                    onClick={() => setSymbolSort(id)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <Link className="btn" to="/charts">
                Inspect in Charts
              </Link>
            </div>
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th className="num">Trades</th>
                  <th className="num">Wins</th>
                  <th className="num">Win rate</th>
                  <th className="num">Realized P&L</th>
                  <th className="num">Avg P&L</th>
                  <th className="num">Profit factor</th>
                  <th className="num">Avg hold</th>
                </tr>
              </thead>
              <tbody>
                {symbolsRows.map((row) => (
                  <tr key={row.symbol}>
                    <td>
                      <button
                        className="btn"
                        title={`Filter to ${row.symbol} and show its trade history below`}
                        onClick={() => {
                          setSymbol(row.symbol)
                          setOutcome('all')
                        }}
                      >
                        {row.symbol}
                      </button>
                    </td>
                    <td className="num">{row.trades}</td>
                    <td className="num">{row.wins}</td>
                    <td className="num">{fmtPct(row.winRatePct)}</td>
                    <td className={`num ${row.totalPnl < 0 ? 'neg' : 'pos'}`}>
                      {formatSignedInr(row.totalPnl)}
                    </td>
                    <td className="num">
                      {row.avgPnl === null ? '—' : formatSignedInr(row.avgPnl)}
                    </td>
                    <td className="num">{fmtFactor(row.profitFactor)}</td>
                    <td className="num">{holdingLabel(row.avgHoldMs)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>

      <div className="card">
        <div className="card-title">AI decision intelligence</div>
        <div className="stat-grid">
          <StatCard label="BUY proposals" value={String(actionCounts.buy)} />
          <StatCard label="SELL proposals" value={String(actionCounts.sell)} />
          <StatCard label="HOLD proposals" value={String(actionCounts.hold)} />
          <StatCard label="Executed decisions" value={String(conversion.executed)} />
          <StatCard label="Unexecuted decisions" value={String(conversion.unexecuted)} />
          <StatCard label="Conversion rate" value={fmtPct(conversion.conversionPct)} />
          <StatCard
            label="Avg decision→execution"
            value={decisionDelay.avgMs === null ? '—' : holdingLabel(decisionDelay.avgMs)}
          />
          <StatCard label="Linked executions" value={String(decisionDelay.linked)} />
        </div>
        <div className="faint" style={{ marginTop: 8 }}>
          Recommendations are analyzed separately from executions: only decisions whose own
          orders_generated record exists count as executed, via entry_decision_id linkage.
          HOLD proposals are never counted as trades. {decisionDelay.unlinked} trade(s) could not
          be linked to a decision and stay out of delay statistics. No price-move success is
          inferred — conversion measures execution linkage only.
        </div>
      </div>

      <div className="card">
        <div className="card-title">Execution quality</div>
        {executionQuality.linked === 0 ? (
          <Unavailable
            label="Execution quality"
            reason="No closed trade in scope links to a decision proposal carrying a desired entry price."
          />
        ) : (
          <div className="stat-grid">
            <StatCard
              label="Avg adverse slippage"
              value={`${executionQuality.avgBps === null ? '—' : `${executionQuality.avgBps.toFixed(1)} bps`}`}
              tone={(executionQuality.avgBps ?? 0) > 0 ? 'neg' : 'pos'}
            />
            <StatCard label="Adverse fills" value={String(executionQuality.adverse)} />
            <StatCard label="Favorable fills" value={String(executionQuality.favorable)} />
            <StatCard label="Linked fills" value={String(executionQuality.linked)} />
          </div>
        )}
        <div className="faint" style={{ marginTop: 8 }}>
          Sign convention: BUY filled above the proposal&apos;s desired entry price is adverse
          (positive); SELL filled below it is adverse. Entry side is taken from the linked
          proposal record; entry fill price from the closed-trade record. Order-to-fill latency
          and partial-fill frequency are unavailable — per-fill submit/ack timestamps are not
          retained — so they are not estimated.
          {executionQuality.missingRef > 0 || executionQuality.unlinked > 0
            ? ` Excluded: ${executionQuality.missingRef} without reference price, ${executionQuality.unlinked} unlinked.`
            : ''}
        </div>
      </div>

      <div className="card">
        <div className="card-title">Risk analytics</div>
        <div className="stat-grid">
          <StatCard
            label="Max realized drawdown"
            value={
              ddMax === null
                ? '—'
                : `${formatInr(ddMax.amount)}${ddMax.percent === null ? '' : ` (${ddMax.percent.toFixed(1)}%)`}`
            }
          />
          <StatCard
            label="Current realized drawdown"
            value={
              ddNow === null
                ? '—'
                : `${formatInr(ddNow.amount)}${ddNow.percent === null ? '' : ` (${ddNow.percent.toFixed(1)}%)`}`
            }
          />
          <StatCard
            label="Avg realized drawdown"
            value={ddAvg === null ? '—' : formatInr(ddAvg)}
          />
          <StatCard
            label="Largest daily loss"
            value={largestLossDay === null ? '—' : formatSignedInr(largestLossDay)}
            tone="neg"
          />
          <StatCard
            label="Largest daily gain"
            value={largestGain === null ? '—' : formatSignedInr(largestGain)}
            tone="pos"
          />
          <StatCard label="Worst loss streak" value={String(streak.maxLossStreak)} />
          <StatCard label="Best win streak" value={String(streak.maxWinStreak)} />
          <StatCard
            label="Avg loss magnitude"
            value={
              summary.avgLossMagnitude === null
                ? '—'
                : formatSignedInr(-summary.avgLossMagnitude)
            }
          />
        </div>
        <div className="faint" style={{ marginTop: 8 }}>
          Drawdowns are measured on the realized-equity series (closed trades only), so
          intraday and open-position swings are not captured. Sharpe and Sortino ratios are
          withheld: no defensible return frequency, annualization, or risk-free assumption can
          be disclosed from irregular close-spaced observations. Realized-trade statistics above
          stay separate from mark-to-market risk.
        </div>
      </div>

      <div className="card">
        <div className="card-title">Distributions</div>
        {closed.length === 0 ? (
          <Unavailable
            label="Distributions"
            reason="No completed trades in the selected scope."
          />
        ) : (
          <>
            <div className="card-title" style={{ marginTop: 4 }}>
              P&L per trade (₹)
            </div>
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 3, height: 90 }}>
              {hist.map((b, i) => (
                <div
                  key={i}
                  title={`${formatSignedInr(b.lo)} … ${formatSignedInr(b.hi)}: ${b.count} trades`}
                  style={{
                    flex: 1,
                    height: histMax ? `${Math.max(4, (b.count / histMax) * 88)}px` : 4,
                    background: b.hi <= 0 ? 'rgba(220,60,60,0.55)' : b.lo >= 0 ? 'rgba(46,160,67,0.55)' : 'rgba(140,150,165,0.55)',
                    borderRadius: 2,
                  }}
                />
              ))}
            </div>
            <div className="card-title" style={{ marginTop: 12 }}>
              Daily realized P&L (₹)
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              {days.map((d) => (
                <div key={d.date} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span className="faint" style={{ width: 86, fontFamily: 'var(--mono)' }}>
                    {d.date}
                  </span>
                  <div style={{ flex: 1, height: 12, background: 'var(--bg-inset)', borderRadius: 2 }}>
                    <div
                      title={`${d.date}: ${formatSignedInr(d.pnl)} over ${d.trades} trades`}
                      style={{
                        width: `${Math.min(100, (Math.abs(d.pnl) / dailyMax) * 100)}%`,
                        height: '100%',
                        borderRadius: 2,
                        background: d.pnl >= 0 ? 'rgba(46,160,67,0.6)' : 'rgba(220,60,60,0.6)',
                      }}
                    />
                  </div>
                  <span className={`num ${d.pnl < 0 ? 'neg' : 'pos'}`} style={{ width: 110, textAlign: 'right' }}>
                    {formatSignedInr(d.pnl)}
                  </span>
                </div>
              ))}
            </div>
            <div className="card-title" style={{ marginTop: 12 }}>
              Cumulative realized P&L (₹)
            </div>
            {cumulative.length >= 2 ? (
              <CumulativeSvg values={cumulative.map((c) => c.value)} />
            ) : (
              <div className="faint">Need at least two completed trades for a cumulative curve.</div>
            )}
          </>
        )}
      </div>

      <div className="card">
        <div className="card-title">Trade analytics</div>
        {tableRows.length === 0 ? (
          <Unavailable
            label="Trade analytics"
            reason="No completed trades in the selected scope."
          />
        ) : (
          <>
            <div className="filter-bar">
              <div className="seg-group" role="group" aria-label="Sort trades">
                {(
                  [
                    ['closed_at', 'Exit time'],
                    ['realized_pnl', 'P&L'],
                    ['symbol', 'Symbol'],
                  ] as const
                ).map(([id, label]) => (
                  <button
                    key={id}
                    className="btn"
                    aria-pressed={tradeSort === id}
                    disabled={tradeSort === id}
                    onClick={() => setTradeSort(id)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <button
                className="btn"
                onClick={() => setTradeDir((d) => (d === 'asc' ? 'desc' : 'asc'))}
                data-testid="trade-sort-dir"
              >
                {tradeDir === 'asc' ? 'Ascending' : 'Descending'}
              </button>
              <span className="faint">
                Page {safePage + 1} of {pageCount} · {tableRows.length} trades
              </span>
              <button
                className="btn"
                disabled={safePage === 0}
                onClick={() => setPage(safePage - 1)}
              >
                Prev
              </button>
              <button
                className="btn"
                disabled={safePage >= pageCount - 1}
                onClick={() => setPage(safePage + 1)}
              >
                Next
              </button>
            </div>
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th className="num">Qty</th>
                  <th>Entry time</th>
                  <th>Exit time</th>
                  <th className="num">Entry</th>
                  <th className="num">Exit</th>
                  <th className="num">Hold</th>
                  <th className="num">Realized P&L</th>
                  <th className="num">Return on notional</th>
                  <th>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((t) => {
                  const ret = returnOnNotional(t)
                  const hold =
                    t.opened_at && Number.isFinite(Date.parse(t.opened_at))
                      ? Date.parse(t.closed_at) - Date.parse(t.opened_at)
                      : null
                  const out = outcomeOf(t)
                  return (
                    <tr key={t.closed_trade_id}>
                      <td>{t.symbol}</td>
                      <td className="num">{t.quantity}</td>
                      <td className="faint">{t.opened_at ?? '—'}</td>
                      <td className="faint">{t.closed_at}</td>
                      <td className="num">{t.entry_price}</td>
                      <td className="num">{t.exit_price}</td>
                      <td className="num">{holdingLabel(hold)}</td>
                      <td className={`num ${toNumber(t.realized_pnl) < 0 ? 'neg' : 'pos'}`}>
                        {formatSignedInr(toNumber(t.realized_pnl))}
                      </td>
                      <td className="num">{ret === null ? '—' : `${ret.toFixed(2)}%`}</td>
                      <td>{out}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
            <div className="faint" style={{ marginTop: 8 }}>
              Return on notional = realized P&L ÷ entry notional (entry price × quantity). Open
              positions never appear here — see Positions. Click a symbol in Symbol performance to
              filter this table, then Inspect in Charts for candle history.
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function CumulativeSvg({ values }: { values: number[] }) {
  const width = 640
  const height = 120
  const pad = 8
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const x = (i: number) => pad + (i / (values.length - 1)) * (width - pad * 2)
  const y = (v: number) => pad + (1 - (v - min) / span) * (height - pad * 2)
  const line = values.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const up = (values[values.length - 1] ?? 0) >= (values[0] ?? 0)
  const stroke = up ? 'var(--ok)' : 'var(--bad)'
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="Cumulative realized profit and loss"
      style={{ width: '100%', height: 'auto' }}
    >
      <path d={line} fill="none" stroke={stroke} strokeWidth="1.75" strokeLinejoin="round" />
      <circle cx={x(values.length - 1)} cy={y(values[values.length - 1] ?? 0)} r="2.5" fill={stroke} />
    </svg>
  )
}
