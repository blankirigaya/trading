import { Badge, Empty, Stat } from '@/components/common/Primitives'
import { usePortfolio } from '@/hooks/usePortfolio'
import { useSystemStatus } from '@/hooks/useSystemStatus'
import { formatInr, formatSignedInr, toNumber } from '@/utils/format'

/**
 * Paper portfolio valuation and allocation. The UI only displays backend state; it
 * performs no allocation advice or portfolio mathematics.
 */
export function Portfolio() {
  const { data: portfolio } = usePortfolio()
  const { data: status } = useSystemStatus()

  if (!portfolio) return <Empty>Loading paper portfolio</Empty>

  const cash = toNumber(portfolio.cash)
  const positions = (Array.isArray(portfolio.positions) ? portfolio.positions : []).filter(
    (position) => position.quantity > 0,
  )
  const invested = positions.reduce(
    (sum, position) => sum + toNumber(position.average_entry_price) * position.quantity,
    0,
  )
  const total = cash + invested + toNumber(portfolio.unrealized_pnl)
  const starting = toNumber(status?.shadow_initial_capital_inr) || total

  const allocation = [
    { label: 'Cash', value: cash },
    ...positions.map((position) => ({
      label: position.symbol,
      value: toNumber(position.average_entry_price) * position.quantity,
    })),
  ].filter((entry) => entry.value > 0)

  return (
    <div>
      <div className="card">
        <div className="card-title">
          <span>Paper portfolio</span>
          <Badge tone="paper">PaperBroker &mdash; simulated funds</Badge>
        </div>
        <div className="stat-grid">
          <Stat label="Starting capital" value={formatInr(starting)} />
          <Stat label="Portfolio value" value={formatInr(total)} testId="portfolio-value" />
          <Stat label="Cash" value={formatInr(cash)} />
          <Stat label="Invested" value={formatInr(invested)} />
          <Stat
            label="Realized P&L"
            value={formatSignedInr(portfolio.realized_pnl)}
            tone={toNumber(portfolio.realized_pnl) < 0 ? 'neg' : 'pos'}
          />
          <Stat
            label="Unrealized P&L"
            value={formatSignedInr(portfolio.unrealized_pnl)}
            tone={toNumber(portfolio.unrealized_pnl) < 0 ? 'neg' : 'pos'}
          />
        </div>
      </div>

      <div className="card">
        <div className="card-title">
          <span>Allocation by symbol</span>
          <span className="faint">display only</span>
        </div>
        {allocation.length === 0 ? (
          <Empty>Nothing allocated</Empty>
        ) : (
          allocation.map((entry) => {
            const share = total ? (entry.value / total) * 100 : 0
            return (
              <div key={entry.label} style={{ marginBottom: 8 }}>
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    fontSize: 12,
                    marginBottom: 3,
                  }}
                >
                  <span>{entry.label}</span>
                  <span className="num">
                    {formatInr(entry.value)} ({share.toFixed(1)}%)
                  </span>
                </div>
                <div style={{ height: 7, background: 'var(--bg)', borderRadius: 3 }}>
                  <div
                    style={{
                      width: `${Math.min(share, 100)}%`,
                      height: '100%',
                      borderRadius: 3,
                      background: entry.label === 'Cash' ? 'var(--neutral)' : 'var(--accent)',
                    }}
                  />
                </div>
              </div>
            )
          })
        )}
      </div>

      {positions.length ? (
        <div className="card">
          <div className="card-title">Position cards</div>
          <div className="stat-grid">
            {positions.map((position) => (
              <Stat
                key={position.symbol}
                label={position.symbol}
                value={`${position.quantity} @ ${position.average_entry_price}`}
              />
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}
