import { Badge, Empty, Stat } from '@/components/common/Primitives'
import { useDecisions } from '@/hooks/useDecisions'
import { useMarketSnapshots } from '@/hooks/useSystemStatus'
import { usePortfolio } from '@/hooks/usePortfolio'
import {
  formatInr,
  formatPrice,
  formatQuantity,
  formatSignedInr,
  toNumber,
} from '@/utils/format'

/** Open simulated positions, valued against the latest real FYERS prices. */
export function Positions() {
  const { data: portfolio } = usePortfolio()
  const { data: snapshots } = useMarketSnapshots()
  const { data: decisions } = useDecisions()
  const priceBySymbol = new Map(
    (snapshots ?? []).map((snapshot) => [snapshot.instrument.symbol, snapshot.last_price]),
  )
  const positions = (portfolio?.positions ?? []).filter((position) => position.quantity > 0)

  if (!positions.length) return <Empty>No open simulated positions</Empty>

  return (
    <div className="card">
      <div className="card-title">
        <span>Open paper positions</span>
        <Badge tone="paper">PaperBroker</Badge>
      </div>
      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Side</th>
            <th className="num">Qty</th>
            <th className="num">Entry</th>
            <th className="num">LTP</th>
            <th className="num">Value</th>
            <th className="num">Unrealized</th>
            <th className="num">Realized</th>
            <th>Last AI decision</th>
            <th>Related decisions</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((position) => {
            const avg = toNumber(position.average_entry_price)
            const ltp = toNumber(priceBySymbol.get(position.symbol) ?? avg)
            const value = ltp * position.quantity
            const pnl = (ltp - avg) * position.quantity
            const related = (decisions ?? [])
              .filter((record) => record.symbol === position.symbol)
              .slice(0, 3)
            return (
              <tr key={position.symbol}>
                <td>{position.symbol}</td>
                <td className="dim">LONG</td>
                <td className="num">{formatQuantity(position.quantity)}</td>
                <td className="num">{formatPrice(avg)}</td>
                <td className="num">{formatPrice(ltp)}</td>
                <td className="num">{formatInr(value)}</td>
                <td className={`num ${pnl < 0 ? 'neg' : 'pos'}`}>{formatSignedInr(pnl)}</td>
                <td className="num">{formatSignedInr(position.realized_pnl)}</td>
                <td className="faint">{related[0]?.final_ai_decision?.proposals[0]?.action ?? '—'}</td>
                <td className="faint">
                  {related.map((record) => record.decision_id.slice(0, 8)).join(', ') || '—'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="stat-grid" style={{ marginTop: 12 }}>
        <Stat label="Positions" value={positions.length} />
        <Stat label="Unrealized" value={formatSignedInr(portfolio?.unrealized_pnl)} />
      </div>
    </div>
  )
}
