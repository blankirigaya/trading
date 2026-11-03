import { useState } from 'react'

import type { ClosedTradeRow, TradeRow } from '@/api/types'
import { Badge, Empty, Stat } from '@/components/common/Primitives'
import { PageHeader } from '@/components/common/PageHeader'
import { useTrade, useTradeHistory, useTrades } from '@/hooks/useTrades'
import {
  formatDateTime,
  formatInr,
  formatPrice,
  formatQuantity,
  formatSignedInr,
  toNumber,
} from '@/utils/format'

function TradeDrawer({ tradeId, onClose }: { tradeId: string; onClose: () => void }) {
  const { data } = useTrade(tradeId)
  if (!data) return null
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" data-testid="trade-drawer">
        <div className="drawer-head">
          <h2>Paper trade {data.trade_id}</h2>
          <button type="button" className="btn" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="card">
          <div className="card-title">Simulated execution</div>
          <div className="kv">
            <dt>Execution provider</dt>
            <dd>{data.execution_provider}</dd>
            <dt>Real money involved</dt>
            <dd>No</dd>
            <dt>Side / Qty</dt>
            <dd>
              {data.side} {formatQuantity(data.quantity)}
            </dd>
            <dt>Entry price</dt>
            <dd>{formatPrice(data.entry_price)}</dd>
            <dt>Order value</dt>
            <dd>{formatInr(data.actual_order_value)}</dd>
            <dt>Charges</dt>
            <dd>{formatInr(data.fees)}</dd>
            {data.entry_fees !== '0' ? (
              <>
                <dt>Entry charges netted</dt>
                <dd>{formatInr(data.entry_fees)}</dd>
              </>
            ) : null}
            <dt>Status</dt>
            <dd>{data.status}</dd>
            <dt>Linked decision</dt>
            <dd>{data.decision_id}</dd>
            <dt>AI action</dt>
            <dd>{data.ai_action ?? 'n/a'}</dd>
          </div>
        </div>
        {data.thesis ? (
          <div className="card">
            <div className="card-title">Entry thesis</div>
            <div className="thesis">{data.thesis}</div>
          </div>
        ) : null}
      </aside>
    </>
  )
}

export function Trades() {
  const { data: trades, isLoading } = useTrades()
  const { data: history } = useTradeHistory()
  const [selected, setSelected] = useState<string | null>(null)

  if (isLoading) return <Empty>Loading paper trades</Empty>

  const closed = history?.closed_trades ?? []
  const totalRealized = toNumber(history?.total_realized_pnl ?? 0)
  const totalCharges = toNumber(history?.total_charges ?? 0)
  const grossRealized = toNumber(history?.gross_realized_pnl ?? totalRealized)

  return (
    <div>
      <PageHeader
        eyebrow="Paper trading"
        title="Trades"
        sub="Permanent record of closed paper rounds plus every simulated fill."
      />
      <div className="card">
        <div className="card-title">
          <span>Closed trades &mdash; permanent record</span>
          <Badge tone="paper">Survives backend restarts</Badge>
        </div>
        {!closed.length ? (
          <Empty>No closed round trips yet. Open positions that get sold appear here with their realized profit or loss.</Empty>
        ) : (
          <>
            <div className="stat-grid" style={{ marginBottom: 12 }}>
              <Stat label="Realized P&L (net)" value={formatSignedInr(totalRealized)} tone={totalRealized < 0 ? 'neg' : 'pos'} />
              <Stat label="Charges paid" value={formatInr(totalCharges)} tone="neg" />
              <Stat label="Gross realized P&L" value={formatSignedInr(grossRealized)} tone={grossRealized < 0 ? 'neg' : 'pos'} />
              <Stat label="Wins" value={history?.wins ?? 0} />
              <Stat label="Losses" value={history?.losses ?? 0} />
            </div>
            <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Closed</th>
                  <th>Symbol</th>
                  <th className="num">Qty</th>
                  <th className="num">Entry</th>
                  <th className="num">Exit</th>
                  <th className="num">Gross P&L</th>
                  <th className="num">Charges</th>
                  <th className="num">Net P&L</th>
                </tr>
              </thead>
              <tbody>
                {closed.map((trade: ClosedTradeRow) => {
                  const pnl = toNumber(trade.realized_pnl)
                  const gross = toNumber(trade.gross_pnl ?? trade.realized_pnl)
                  const charges = toNumber(trade.charges ?? 0)
                  return (
                    <tr key={trade.closed_trade_id}>
                      <td className="faint">{formatDateTime(trade.closed_at)}</td>
                      <td>{trade.symbol}</td>
                      <td className="num">{formatQuantity(trade.quantity)}</td>
                      <td className="num">{formatPrice(trade.entry_price)}</td>
                      <td className="num">{formatPrice(trade.exit_price)}</td>
                      <td className={`num ${gross < 0 ? 'neg' : 'pos'}`}>{formatSignedInr(gross)}</td>
                      <td className="num neg">{formatInr(charges)}</td>
                      <td className={`num ${pnl < 0 ? 'neg' : 'pos'}`}>{formatSignedInr(pnl)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
            </div>
          </>
        )}
      </div>
      <div className="card">
        <div className="card-title">
          <span>Paper trades</span>
          <Badge tone="paper">All rows are simulated &mdash; PaperBroker</Badge>
        </div>
      {!trades?.length ? <Empty>No simulated trades yet</Empty> : (
      <>
      <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Mode</th>
            <th>Symbol</th>
            <th>Side</th>
            <th className="num">Qty</th>
            <th className="num">Entry price</th>
            <th className="num">Order value</th>
            <th className="num">Charges</th>
            <th>Status</th>
            <th>AI action</th>
            <th>Decision ID</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((trade: TradeRow) => (
            <tr
              key={trade.trade_id}
              className="clickable"
              onClick={() => setSelected(trade.trade_id)}
              data-testid="trade-row"
            >
              <td className="faint">{formatDateTime(trade.time)}</td>
              <td>
                <Badge tone="paper">PAPER</Badge>
              </td>
              <td>{trade.symbol}</td>
              <td>{trade.side}</td>
              <td className="num">{formatQuantity(trade.quantity)}</td>
              <td className="num">{formatPrice(trade.entry_price)}</td>
              <td className="num">{formatInr(trade.actual_order_value)}</td>
              <td className="num faint">{formatInr(trade.fees ?? 0)}</td>
              <td>{trade.status}</td>
              <td className="faint">{trade.ai_action ?? '—'}</td>
              <td className="faint">{trade.decision_id.slice(0, 8)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      </>
      )}
      {selected ? <TradeDrawer tradeId={selected} onClose={() => setSelected(null)} /> : null}
    </div>
    </div>
  )
}
