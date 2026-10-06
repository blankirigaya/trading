import { useState } from 'react'

import type { DecisionRecord } from '@/api/types'
import { Badge, Empty } from '@/components/common/Primitives'
import { DecisionDetail } from '@/components/decisions/DecisionDetail'
import { useDecisions } from '@/hooks/useDecisions'
import { formatDateTime, formatInr, formatPercent, formatSignedInr } from '@/utils/format'

function jevDisposition(agrees: boolean | undefined, bounded: string[] | undefined): string {
  const explicit = bounded?.find((item) => /APPROVE|CHALLENGE|RECONSIDERATION/.test(item))
  if (explicit) return explicit
  if (agrees === undefined) return 'NONE'
  return agrees ? 'APPROVE' : 'CHALLENGE'
}

export function Decisions() {
  const { data: decisions, isLoading } = useDecisions()
  const [selected, setSelected] = useState<string | null>(null)
  const [symbol, setSymbol] = useState('')
  const [action, setAction] = useState('')
  const [jev, setJev] = useState('')
  const [safety, setSafety] = useState('')
  const [executed, setExecuted] = useState('')

  const rows = (decisions ?? []).filter((record) => {
    const first = record.initial_ai_proposal.proposals[0]
    if (symbol && record.symbol !== symbol && first?.symbol !== symbol) return false
    if (action && first?.action !== action) return false
    const disposition = jevDisposition(record.jev_critique?.agrees, record.jev_critique?.bounded_decisions)
    if (jev && disposition !== jev) return false
    if (safety) {
      if (safety === 'PASS' && record.safety_allowed !== true) return false
      if (safety === 'DENY' && record.safety_allowed !== false) return false
    }
    if (executed === 'executed' && record.orders_generated.length === 0) return false
    if (executed === 'not-executed' && record.orders_generated.length > 0) return false
    return true
  })

  const selectedRecord = rows.find((record) => record.decision_id === selected) ?? null
  const actions = [
    ...new Set(
      (decisions ?? []).flatMap((record) =>
        record.initial_ai_proposal.proposals.map((proposal) => proposal.action),
      ),
    ),
  ]

  if (isLoading) return <Empty>Loading decision journal</Empty>

  return (
    <div>
      <div className="card">
        <div className="card-title">
          <span>AI decision journal</span>
          <Badge tone="neutral">{rows.length} of {decisions?.length ?? 0}</Badge>
        </div>

        <div className="filter-bar">
          <input
            placeholder="Symbol"
            value={symbol}
            onChange={(event) => setSymbol(event.target.value.toUpperCase())}
            aria-label="Filter by symbol"
          />
          <select value={action} onChange={(event) => setAction(event.target.value)} aria-label="Filter by action">
            <option value="">All actions</option>
            {actions.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          <select value={jev} onChange={(event) => setJev(event.target.value)} aria-label="Filter by Jev result">
            <option value="">All Jev results</option>
            <option value="APPROVE">Approve</option>
            <option value="CHALLENGE">Challenge</option>
            <option value="REQUEST_RECONSIDERATION">Reconsideration</option>
            <option value="NONE">No critique</option>
          </select>
          <select value={safety} onChange={(event) => setSafety(event.target.value)} aria-label="Filter by safety">
            <option value="">All safety results</option>
            <option value="PASS">Pass</option>
            <option value="DENY">Deny</option>
          </select>
          <select
            value={executed}
            onChange={(event) => setExecuted(event.target.value)}
            aria-label="Filter by execution"
          >
            <option value="">All outcomes</option>
            <option value="executed">Executed</option>
            <option value="not-executed">Not executed</option>
          </select>
        </div>

        {rows.length === 0 ? (
          <Empty>No decisions match these filters</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Symbol</th>
                <th>Initial action</th>
                <th>Jev</th>
                <th>Final action</th>
                <th className="num">Allocation</th>
                <th className="num">Confidence</th>
                <th>Safety</th>
                <th>Execution</th>
                <th>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((record) => {
                const initial = record.initial_ai_proposal.proposals[0]
                const final = record.final_ai_decision?.proposals[0]
                return (
                  <tr
                    key={record.decision_id}
                    className="clickable"
                    onClick={() => setSelected(record.decision_id)}
                    data-testid="decision-row"
                  >
                    <td className="faint">{formatDateTime(record.timestamp)}</td>
                    <td>{record.symbol ?? initial?.symbol ?? '—'}</td>
                    <td>{initial?.action ?? 'HOLD'}</td>
                    <td>
                      {jevDisposition(record.jev_critique?.agrees, record.jev_critique?.bounded_decisions)}
                    </td>
                    <td>{final?.action ?? initial?.action ?? 'HOLD'}</td>
                    <td className="num">
                      {final?.capital_allocation_inr
                        ? formatInr(final.capital_allocation_inr)
                        : '—'}
                    </td>
                    <td className="num">{formatPercent(final?.confidence ?? initial?.confidence, 0)}</td>
                    <td>
                      {record.safety_allowed === true
                        ? 'PASS'
                        : record.safety_allowed === false
                          ? 'DENY'
                          : '—'}
                    </td>
                    <td>
                      {record.orders_generated.length ? (
                        <Badge tone="paper">{record.orders_generated.length} paper</Badge>
                      ) : (
                        <span className="faint">none</span>
                      )}
                    </td>
                    <td className="faint">{record.status}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {selectedRecord ? (
        <DecisionDrawer record={selectedRecord} onClose={() => setSelected(null)} />
      ) : null}
    </div>
  )
}

function DecisionDrawer({ record, onClose }: { record: DecisionRecord; onClose: () => void }) {
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" data-testid="decision-drawer">
        <div className="drawer-head">
          <h2>Decision detail</h2>
          <button type="button" className="btn" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="card">
          <div className="card-title">Identifiers</div>
          <div className="kv">
            <dt>decision_id</dt>
            <dd>{record.decision_id}</dd>
            <dt>correlation_id</dt>
            <dd>{record.correlation_id}</dd>
            <dt>timestamp</dt>
            <dd>{formatDateTime(record.timestamp)}</dd>
            <dt>market snapshot</dt>
            <dd>{record.market_snapshot_id ?? 'n/a'}</dd>
            <dt>market timestamp</dt>
            <dd>{formatDateTime(record.market_timestamp)}</dd>
            <dt>input context hash</dt>
            <dd>{record.input_context_hash.slice(0, 16)}…</dd>
          </div>
        </div>
        <DecisionDetail record={record} />
        <div className="card">
          <div className="card-title">Portfolio before / after</div>
          <div className="kv">
            <dt>Cash before</dt>
            <dd>{formatInr(record.portfolio_before.cash)}</dd>
            <dt>Unrealized before</dt>
            <dd>{formatSignedInr(record.portfolio_before.unrealized_pnl)}</dd>
            <dt>Cash after</dt>
            <dd>{record.portfolio_after ? formatInr(record.portfolio_after.cash) : 'n/a'}</dd>
            <dt>Unrealized after</dt>
            <dd>
              {record.portfolio_after ? formatSignedInr(record.portfolio_after.unrealized_pnl) : 'n/a'}
            </dd>
          </div>
        </div>
      </aside>
    </>
  )
}
