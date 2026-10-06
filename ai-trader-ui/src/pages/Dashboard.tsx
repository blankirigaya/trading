import { Link } from 'react-router-dom'

import { Badge, Empty, Stat } from '@/components/common/Primitives'
import { DecisionDetail } from '@/components/decisions/DecisionDetail'
import { LiveNewsFeed } from '@/components/news/LiveNews'
import { NewsLatencyTable } from '@/components/news/NewsLatency'
import { LiveTimeline } from '@/components/timeline/LiveTimeline'
import { useDecisions } from '@/hooks/useDecisions'
import { useMarketSnapshots } from '@/hooks/useSystemStatus'
import { usePerformance } from '@/hooks/usePortfolio'
import { usePortfolio } from '@/hooks/usePortfolio'
import { useSystemStatus } from '@/hooks/useSystemStatus'
import {
  formatInr,
  formatInteger,
  formatPrice,
  formatSignedInr,
  pctChange,
  toNumber,
} from '@/utils/format'

function PortfolioSummary() {
  const { data: portfolio, isLoading } = usePortfolio()
  const { data: status } = useSystemStatus()

  if (isLoading || !portfolio) return <Empty>Loading paper portfolio</Empty>

  const positions = Array.isArray(portfolio.positions) ? portfolio.positions : []
  const cash = toNumber(portfolio.cash)
  const invested = positions
    .filter((position) => position.quantity > 0)
    .reduce(
      (sum, position) => sum + toNumber(position.average_entry_price) * position.quantity,
      0,
    )
  const starting = toNumber(status?.shadow_initial_capital_inr) || cash + invested
  const total = cash + toNumber(portfolio.unrealized_pnl)
  const today = toNumber(portfolio.realized_pnl) + toNumber(portfolio.unrealized_pnl)

  return (
    <div className="stat-grid">
      <Stat label="Starting capital" value={formatInr(starting)} testId="stat-starting" />
      <Stat label="Portfolio value" value={formatInr(total)} testId="stat-value" />
      <Stat label="Cash" value={formatInr(cash)} testId="stat-cash" />
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
      <Stat
        label="Total P&L"
        value={formatSignedInr(today)}
        tone={today < 0 ? 'neg' : 'pos'}
        testId="stat-pnl"
      />
      <Stat label="Open positions" value={formatInteger(positions.length)} />
    </div>
  )
}

function OpenPositions() {
  const { data: portfolio } = usePortfolio()
  const { data: snapshots } = useMarketSnapshots()
  const priceBySymbol = new Map(
    (snapshots ?? []).map((snapshot) => [snapshot.instrument.symbol, snapshot.last_price]),
  )
  const positions = (portfolio?.positions ?? []).filter((position) => position.quantity > 0)
  return (
    <div className="card">
      <div className="card-title">
        <span>Open positions</span>
        <Badge tone="paper">PaperBroker</Badge>
      </div>
      {positions.length === 0 ? (
        <Empty>No open simulated positions</Empty>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Side</th>
              <th className="num">Qty</th>
              <th className="num">Avg price</th>
              <th className="num">LTP</th>
              <th className="num">Value</th>
              <th className="num">Unrealized</th>
              <th className="num">P&amp;L %</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((position) => {
              const avg = toNumber(position.average_entry_price)
              const ltp = toNumber(priceBySymbol.get(position.symbol) ?? avg)
              const value = ltp * position.quantity
              const pnl = (ltp - avg) * position.quantity
              const pnlPct = avg ? ((ltp - avg) / avg) * 100 : 0
              return (
                <tr key={position.symbol}>
                  <td>
                    <Link to="/positions">{position.symbol}</Link>
                  </td>
                  <td className="dim">LONG</td>
                  <td className="num">{position.quantity}</td>
                  <td className="num">{formatPrice(avg)}</td>
                  <td className="num">{formatPrice(ltp)}</td>
                  <td className="num">{formatInr(value)}</td>
                  <td className={`num ${pnl < 0 ? 'neg' : 'pos'}`}>{formatSignedInr(pnl)}</td>
                  <td className={`num ${pnlPct < 0 ? 'neg' : 'pos'}`}>{pnlPct.toFixed(2)}%</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}

function MarketSnapshotStrip() {
  const { data: snapshots } = useMarketSnapshots()
  if (!snapshots?.length) {
    return (
      <div className="card">
        <div className="card-title">Market</div>
        <Empty>Waiting for FYERS market data</Empty>
      </div>
    )
  }
  return (
    <div className="card">
      <div className="card-title">
        <span>Market &mdash; real FYERS prices</span>
        <Badge tone="neutral">Market data only</Badge>
      </div>
      <div className="stat-grid">
        {snapshots.map((snapshot) => {
          const change = pctChange(snapshot.last_price, snapshot.previous_close)
          return (
            <Stat
              key={snapshot.instrument.symbol}
              label={`${snapshot.instrument.exchange} ${snapshot.instrument.symbol}`}
              value={
                <>
                  {formatPrice(snapshot.last_price)}{' '}
                  <span className={change !== null && change < 0 ? 'neg' : 'pos'}>
                    {change === null ? '' : `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`}
                  </span>
                </>
              }
            />
          )
        })}
      </div>
    </div>
  )
}

/**
 * Live news activity, straight from the event stream. Read-only: this panel reports
 * what the news layer and the AI did, and offers no control over either.
 */
function LiveNewsPanel() {
  return (
    <div className="two-col">
      <LiveNewsFeed limit={15} />
      <NewsLatencyTable limit={8} />
    </div>
  )
}

function AiAnalysis() {
  const { data } = usePerformance()
  if (!data) return null
  return (
    <div className="card">
      <div className="card-title">AI decision analysis</div>
      <div className="stat-grid">
        <Stat label="Total decisions" value={data.total_decisions} />
        <Stat label="Executed" value={data.executed} />
        <Stat label="Jev approvals" value={data.jev_approvals} />
        <Stat label="Jev challenges" value={data.jev_challenges} />
        <Stat label="AI changed after Jev" value={data.ai_changed_after_jev} />
        <Stat label="AI retained after Jev" value={data.ai_retained_after_jev} />
      </div>
    </div>
  )
}

export function Dashboard() {
  const { data: decisions } = useDecisions()
  const latest = decisions?.[0]

  return (
    <div>
      <PortfolioSummary />
      <div className="two-col">
        <div>
          <OpenPositions />
          <MarketSnapshotStrip />
        </div>
        <div>
          <div className="card">
            <div className="card-title">
              <span>Latest AI decision</span>
              {latest ? (
                <Link to="/decisions" className="dim">
                  View all
                </Link>
              ) : null}
            </div>
            {latest ? (
              <DecisionDetail record={latest} />
            ) : (
              <Empty>No AI decision recorded yet. The AI reviews on its own cadence.</Empty>
            )}
          </div>
        </div>
      </div>
      <AiAnalysis />
      <LiveNewsPanel />
      <LiveTimeline limit={25} compact />
    </div>
  )
}
