import type { DecisionRecord } from '@/api/types'
import { toNumber } from '@/utils/format'

export interface EquityPoint {
  time: string
  equity: number
}

/**
 * Portfolio equity curve derived only from journalled decisions: each point
 * is the backend-reported cash + unrealized P&L after a decision. No history
 * is invented; sparse journals simply render sparse curves.
 */
export function equityPoints(records: DecisionRecord[]): EquityPoint[] {
  const points: EquityPoint[] = []
  const ordered = [...records].sort((a, b) => a.timestamp.localeCompare(b.timestamp))
  for (const record of ordered) {
    const after = record.portfolio_after
    if (!after) continue
    points.push({
      time: record.timestamp,
      equity: toNumber(after.cash) + toNumber(after.unrealized_pnl),
    })
  }
  return points
}

export function EquityCurve({
  points,
  startingCapital,
  testId,
  basisLabel = 'Equity (cash + unrealized)',
}: {
  points: EquityPoint[]
  startingCapital: number
  testId?: string
  /** Honest data-basis label, e.g. 'Realized equity (closed trades)'. */
  basisLabel?: string
}) {
  const width = 640
  const height = 180
  const padL = 8
  const padR = 8
  const padT = 10
  const padB = 18
  if (points.length < 2) return null
  const firstPt = points[0] as EquityPoint
  const lastPt = points[points.length - 1] as EquityPoint
  const values = [startingCapital, ...points.map((point) => point.equity)]
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const x = (index: number) =>
    padL + (index / (points.length - 1)) * (width - padL - padR)
  const y = (value: number) =>
    padT + (1 - (value - min) / span) * (height - padT - padB)
  const line = points
    .map((point, index) => `${index === 0 ? 'M' : 'L'}${x(index).toFixed(1)},${y(point.equity).toFixed(1)}`)
    .join(' ')
  const area = `${line} L${x(points.length - 1).toFixed(1)},${height - padB} L${x(0).toFixed(1)},${height - padB} Z`
  const up = lastPt.equity >= startingCapital
  const stroke = up ? 'var(--ok)' : 'var(--bad)'
  const baseY = y(startingCapital)
  const first = new Date(firstPt.time)
  const last = new Date(lastPt.time)
  const fmt = (date: Date) =>
    Number.isNaN(date.getTime()) ? '' : date.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  return (
    <div className="equity-wrap" data-testid={testId}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Portfolio equity over journalled decisions">
        <defs>
          <linearGradient id="equity-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.22" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0.25, 0.5, 0.75].map((fraction) => (
          <line
            key={fraction}
            x1={padL}
            x2={width - padR}
            y1={padT + fraction * (height - padT - padB)}
            y2={padT + fraction * (height - padT - padB)}
            stroke="var(--border)"
            strokeWidth="1"
          />
        ))}
        <line
          x1={padL}
          x2={width - padR}
          y1={baseY}
          y2={baseY}
          stroke="var(--text-faint)"
          strokeWidth="1"
          strokeDasharray="3 3"
        />
        <path d={area} fill="url(#equity-fill)" />
        <path d={line} fill="none" stroke={stroke} strokeWidth="1.75" strokeLinejoin="round" />
        <circle cx={x(points.length - 1)} cy={y(lastPt.equity)} r="2.5" fill={stroke} />
        <text x={padL} y={height - 4} fill="var(--text-faint)" fontSize="9" fontFamily="var(--mono)">
          {fmt(first)}
        </text>
        <text x={width - padR} y={height - 4} fill="var(--text-faint)" fontSize="9" fontFamily="var(--mono)" textAnchor="end">
          {fmt(last)}
        </text>
      </svg>
      <div className="equity-legend">
        <span>
          <span className="swatch" style={{ background: stroke }} />
          {basisLabel}
        </span>
        <span>
          <span className="swatch" style={{ background: 'var(--text-faint)' }} />
          Starting capital
        </span>
      </div>
    </div>
  )
}
