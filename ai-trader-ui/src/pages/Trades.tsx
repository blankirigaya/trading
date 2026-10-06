import { useState } from 'react'

import type { TradeRow } from '@/api/types'
import { Badge, Empty } from '@/components/common/Primitives'
import { useTrade, useTrades } from '@/hooks/useTrades'
import { formatDateTime, formatInr, formatPrice, formatQuantity } from '@/utils/format'

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
  const [selected, setSelected] = useState<string | null>(null)

  if (isLoading) return <Empty>Loading paper trades</Empty>
  if (!trades?.length) return <Empty>No simulated trades yet</Empty>

  return (
    <div className="card">
      <div className="card-title">
        <span>Paper trades</span>
        <Badge tone="paper">All rows are simulated &mdash; PaperBroker</Badge>
      </div>
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
              <td>{trade.status}</td>
              <td className="faint">{trade.ai_action ?? '—'}</td>
              <td className="faint">{trade.decision_id.slice(0, 8)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {selected ? <TradeDrawer tradeId={selected} onClose={() => setSelected(null)} /> : null}
    </div>
  )
}
