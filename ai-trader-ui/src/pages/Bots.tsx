import { Link } from 'react-router-dom'

import { Badge, Empty, Stat } from '@/components/common/Primitives'
import { PageHeader } from '@/components/common/PageHeader'
import { useBots } from '@/hooks/useBots'
import { useChargeSchedule } from '@/hooks/useCharges'
import { useMarketSnapshots } from '@/hooks/useSystemStatus'
import type { TradingBot } from '@/api/types'
import { estimateExitChargesInr } from '@/utils/charges'
import {
  formatInr,
  formatInteger,
  formatPrice,
  formatQuantity,
  formatSignedInr,
  toNumber,
} from '@/utils/format'

function BotCard({ bot }: { bot: TradingBot }) {
  const { data: snapshots } = useMarketSnapshots()
  const { data: schedule } = useChargeSchedule()
  const rates = schedule?.enabled === true ? schedule.rates : null
  const priceBySymbol = new Map(
    (snapshots ?? []).map((snapshot) => [snapshot.instrument.symbol, snapshot.last_price]),
  )
  const totalValue = bot.positions.reduce((sum, position) => {
    const ltp = toNumber(priceBySymbol.get(position.symbol) ?? position.ltp)
    return sum + ltp * position.quantity
  }, 0)
  const totalPnl = bot.positions.reduce((sum, position) => {
    const ltp = toNumber(priceBySymbol.get(position.symbol) ?? position.ltp)
    return sum + (ltp - toNumber(position.average_entry_price)) * position.quantity
  }, 0)

  return (
    <div className="card" data-testid={`bot-${bot.index}`}>
      <div className="card-title">
        <span>
          {bot.name} <span className="dim">· {bot.symbols.join(', ')}</span>
        </span>
        <Badge tone="paper">{bot.ai_model}</Badge>
      </div>
      <div className="stat-grid">
        <Stat label="Cash" value={formatInr(bot.cash)} />
        <Stat label="Positions value" value={formatInr(totalValue)} />
        <Stat
          label="Unrealised P&L"
          value={formatSignedInr(totalPnl)}
          tone={totalPnl < 0 ? 'neg' : 'pos'}
        />
        <Stat label="Reviews run" value={formatInteger(bot.reviews)} testId={`bot-reviews-${bot.index}`} />
      </div>
      {bot.positions.length === 0 ? (
        <Empty>No open positions — this bot holds only cash right now.</Empty>
      ) : (
        <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th className="num">Qty</th>
              <th className="num">Avg</th>
              <th className="num">LTP</th>
              <th className="num">Value</th>
              <th className="num">Unrealized</th>
              <th className="num" title="Unrealized P&L minus estimated exit charges at LTP (same Zerodha intraday schedule as fills)">Est.&nbsp;net</th>
            </tr>
          </thead>
          <tbody>
            {bot.positions.map((position) => {
              const avg = toNumber(position.average_entry_price)
              const ltp = toNumber(priceBySymbol.get(position.symbol) ?? position.ltp)
              const value = ltp * position.quantity
              const pnl = (ltp - avg) * position.quantity
              const exitFee = rates ? estimateExitChargesInr(value, rates) : null
              const net = exitFee === null ? null : pnl - exitFee
              return (
                <tr key={position.symbol}>
                  <td>
                    <Link to={`/positions?symbol=${position.symbol}`}>{position.symbol}</Link>
                  </td>
                  <td className="num">{formatQuantity(position.quantity)}</td>
                  <td className="num">{formatPrice(avg)}</td>
                  <td className="num">{formatPrice(ltp)}</td>
                  <td className="num">{formatInr(value)}</td>
                  <td className={`num ${pnl < 0 ? 'neg' : 'pos'}`}>{formatSignedInr(pnl)}</td>
                  <td className={`num ${net === null ? '' : net < 0 ? 'neg' : 'pos'}`} title={net === null ? 'Exit-charge schedule unavailable' : `Unrealized ${formatSignedInr(pnl)} minus est. exit charges ${formatInr(exitFee ?? 0)}`}>{net === null ? '—' : formatSignedInr(net)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
      )}
      <div className="card-title" style={{ marginTop: 12 }}>
        <span>Recent decisions</span>
      </div>
      {bot.recent_decisions.length === 0 ? (
        <div className="faint">No decisions journalled for this bot&apos;s symbols yet.</div>
      ) : (
        <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Symbol</th>
              <th>Status</th>
              <th>Action</th>
              <th>Time</th>
            </tr>
          </thead>
          <tbody>
            {bot.recent_decisions.map((decision) => (
              <tr key={decision.decision_id}>
                <td className="faint">
                  <Link to={`/decisions?selected=${decision.decision_id}`}>
                    {decision.decision_id.slice(0, 8)}
                  </Link>
                </td>
                <td>{decision.symbol ?? '—'}</td>
                <td>
                  <Badge tone={decision.status === 'EXECUTED' ? 'ok' : 'neutral'}>
                    {decision.status}
                  </Badge>
                </td>
                <td className="faint">{decision.ai_action ?? '—'}</td>
                <td className="faint">{decision.timestamp.slice(11, 19)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </div>
  )
}

/** One card per AI reviewer: its symbols, paper book, and what it has decided. */
export function Bots() {
  const { data: bots, isLoading } = useBots()
  if (isLoading) return <Empty>Loading bots…</Empty>
  if (!bots?.length) {
    return (
      <div>
        <PageHeader
          eyebrow="AI intelligence"
          title="Reviewer bots"
          sub="One card per parallel AI reviewer with its own paper book."
        />
        <Empty>
          No reviewer bots are attached. Start a shadow session to activate them.
        </Empty>
      </div>
    )
  }
  return (
    <div>
      <PageHeader
        eyebrow="AI intelligence"
        title="Reviewer bots"
        sub="One card per parallel AI reviewer with its own paper book."
      />
      <p className="faint" style={{ marginBottom: 12 }}>
        {bots.length} AI reviewer{bots.length === 1 ? '' : 's'} running in parallel, each with its
        own paper book. Combined totals stay on the Dashboard.
      </p>
      {bots.map((bot) => (
        <BotCard key={bot.index} bot={bot} />
      ))}
    </div>
  )
}
