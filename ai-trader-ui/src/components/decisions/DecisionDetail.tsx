import { Fragment } from 'react'

import type { CriticAssessment, DecisionRecord, PortfolioDecision, TradeProposal } from '@/api/types'
import { Badge } from '@/components/common/Primitives'
import { formatDateTime, formatInr, formatLatency, formatPercent, formatPrice } from '@/utils/format'

function actionTone(action: string | undefined): 'ok' | 'warn' | 'bad' | 'neutral' {
  if (!action) return 'neutral'
  if (action.includes('CLOSE')) return 'bad'
  if (action.includes('REDUCE')) return 'warn'
  if (action.includes('OPEN') || action.includes('INCREASE')) return 'ok'
  return 'neutral'
}

/** Read-only decision header: action, symbol, confidence, timestamp. */
function DecisionHeader({ record, invalid }: { record: DecisionRecord; invalid: boolean }) {
  const proposal = record.final_ai_decision?.proposals[0] ?? record.initial_ai_proposal.proposals[0]
  const action = invalid ? 'INVALID' : proposal?.action ?? 'HOLD'
  const confidence = proposal?.confidence
  return (
    <div className="card" data-testid="decision-header">
      <div className="stage-head">
        <span className="stage-title">AI decision</span>
        <Badge tone={invalid ? 'bad' : actionTone(action)}>{action}</Badge>
      </div>
      <div className="kv">
        <dt>Symbol</dt>
        <dd>{proposal?.symbol ?? record.symbol ?? '—'}</dd>
        <dt>Side</dt>
        <dd>{proposal?.side ?? '—'}</dd>
        <dt>Confidence</dt>
        <dd>{confidence === undefined || confidence === null ? 'n/a' : formatPercent(confidence, 0)}</dd>
        <dt>Timestamp</dt>
        <dd>{formatDateTime(record.timestamp)}</dd>
      </div>
    </div>
  )
}

/** Trade facts, rendered only when the proposal actually carries them. */
function TradeInfo({ proposal }: { proposal: TradeProposal | undefined }) {
  if (!proposal) return null
  const rows: Array<[string, string]> = []
  if (proposal.desired_entry_price) rows.push(['Entry price', formatPrice(proposal.desired_entry_price)])
  if (proposal.target_price) rows.push(['Target price', formatPrice(proposal.target_price)])
  if (proposal.invalidation_price) rows.push(['Stop-loss level', formatPrice(proposal.invalidation_price)])
  if (proposal.quantity_shares !== null && proposal.quantity_shares !== undefined)
    rows.push(['Position size', `${proposal.quantity_shares} shares`])
  if (proposal.capital_allocation_inr) rows.push(['Allocation', formatInr(proposal.capital_allocation_inr)])
  if (!rows.length) return null
  return (
    <div className="card" data-testid="decision-trade-info">
      <div className="card-title">Trade information</div>
      <div className="kv">
        {rows.map(([label, value]) => (
          <Fragment key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </Fragment>
        ))}
      </div>
    </div>
  )
}

function proposalSummary(proposal: TradeProposal | undefined): string {
  if (!proposal) return 'No trade proposed'
  const side = proposal.side ?? '—'
  const allocation = proposal.capital_allocation_inr
    ? formatInr(proposal.capital_allocation_inr)
    : 'n/a'
  return `${proposal.action} · ${side} ${proposal.symbol ?? '—'} · ${allocation} · conf ${formatPercent(
    proposal.confidence,
    0,
  )}`
}

function DecisionBlock({
  title,
  stage,
  decision,
  note,
}: {
  title: string
  stage: string
  decision: PortfolioDecision
  note?: string
}) {
  const first = decision.proposals[0]
  return (
    <div className={`stage ${stage}`}>
      <div className="stage-head">
        <span className="stage-title">{title}</span>
        <Badge tone={stage === 'final' ? 'ok' : 'accent'}>{proposalSummary(first)}</Badge>
      </div>
      <div className="stage-body">
        <div className="dim" style={{ marginBottom: 6 }}>
          {decision.decision_summary}
        </div>
        {first ? (
          <>
            <div className="thesis">
              <strong>Thesis:</strong> {first.thesis}
            </div>
            {first.evidence.length ? (
              <>
                <div className="faint" style={{ marginTop: 7 }}>
                  Evidence
                </div>
                <ul className="evidence-list">
                  {first.evidence.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </>
            ) : null}
            {first.conditions_that_change_thesis.length ? (
              <>
                <div className="faint" style={{ marginTop: 7 }}>
                  Changes the thesis if
                </div>
                <ul className="evidence-list">
                  {first.conditions_that_change_thesis.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </>
            ) : null}
          </>
        ) : null}
        {note ? <div className="faint" style={{ marginTop: 7 }}>{note}</div> : null}
      </div>
    </div>
  )
}

function JevBlock({ critique }: { critique: CriticAssessment | null }) {
  if (!critique) {
    return (
      <div className="stage jev">
        <div className="stage-head">
          <span className="stage-title">Jev Critique</span>
          <Badge tone="neutral">NOT CALLED</Badge>
        </div>
        <div className="faint">No critic assessment recorded for this decision.</div>
      </div>
    )
  }
  const disposition = critique.bounded_decisions[0] ?? 'UNKNOWN'
  return (
    <div className="stage jev">
      <div className="stage-head">
        <span className="stage-title">Jev Critique</span>
        <Badge tone={critique.agrees ? 'ok' : 'warn'}>{disposition}</Badge>
      </div>
      <div className="stage-body">
        <div className="kv">
          <dt>Evidence strength</dt>
          <dd>{Math.round(critique.score * 100)}/100</dd>
          <dt>Agrees</dt>
          <dd>{critique.agrees ? 'YES' : 'NO'}</dd>
          <dt>Disposition</dt>
          <dd>{disposition}</dd>
        </div>
        <div className="thesis" style={{ marginTop: 7 }}>
          {critique.critique}
        </div>
        {critique.disagreement_signals.length ? (
          <>
            <div className="faint" style={{ marginTop: 7 }}>
              Disagreement signals
            </div>
            <ul className="evidence-list">
              {critique.disagreement_signals.map((signal) => (
                <li key={signal}>{signal}</li>
              ))}
            </ul>
          </>
        ) : null}
      </div>
    </div>
  )
}

function SafetyBlock({ record }: { record: DecisionRecord }) {
  const allowed = record.safety_allowed
  return (
    <div className="stage safety">
      <div className="stage-head">
        <span className="stage-title">Safety Result</span>
        <Badge tone={allowed === true ? 'ok' : allowed === false ? 'bad' : 'neutral'}>
          {allowed === true ? 'PASS' : allowed === false ? 'DENY' : 'NOT APPLICABLE'}
        </Badge>
      </div>
      <div className="faint">{record.safety_reason ?? 'No safety evaluation was required.'}</div>
    </div>
  )
}

function PaperBlock({ record }: { record: DecisionRecord }) {
  const order = record.orders_generated[0]
  return (
    <div className="stage paper">
      <div className="stage-head">
        <span className="stage-title">Paper Execution</span>
        <Badge tone="paper">{order ? order.status : 'NO ORDER'}</Badge>
      </div>
      <div className="stage-body">
        {order ? (
          <div className="kv">
            <dt>Side / Qty</dt>
            <dd>
              {order.request.side} {order.request.quantity} {order.request.symbol}
            </dd>
            <dt>Order value</dt>
            <dd>{formatInr(order.request.actual_order_value)}</dd>
            <dt>Requested allocation</dt>
            <dd>{formatInr(order.request.requested_allocation)}</dd>
            <dt>Conversion</dt>
            <dd>{order.request.conversion_reason}</dd>
            <dt>Paper order id</dt>
            <dd>{order.broker_order_id}</dd>
          </div>
        ) : (
          <div className="faint">No simulated order was produced. Execution provider: PaperBroker.</div>
        )}
      </div>
    </div>
  )
}

/**
 * Renders the three decision stages separately. The initial proposal is never
 * merged with or overwritten by the final decision.
 */
export function DecisionDetail({ record }: { record: DecisionRecord }) {
  // A reply that failed validation is a provider/prompt fault, not a decision
  // to hold. Showing "HOLD" for it tells the operator the AI chose to abstain
  // when in fact nothing was judged at all.
  const invalidOutput = record.status === 'FAILED' && (record.failure_reason ?? '').startsWith('ai:')
  return (
    <div data-testid="decision-detail">
      <DecisionHeader record={record} invalid={invalidOutput} />
      {invalidOutput ? (
        <div
          className="card"
          data-testid="ai-output-invalid"
          style={{ borderColor: 'rgba(240,87,106,0.5)' }}
        >
          <div className="card-title">
            <span>AI output invalid</span>
            <Badge tone="bad">NO DECISION</Badge>
          </div>
          <div className="faint">
            The model replied but its output failed schema validation, so nothing was traded. This is
            a provider or prompt fault, not a decision to stay flat.
          </div>
        </div>
      ) : null}
      <TradeInfo proposal={record.final_ai_decision?.proposals[0] ?? record.initial_ai_proposal.proposals[0]} />
      <DecisionBlock
        title="Initial AI Decision"
        stage="initial"
        decision={record.initial_ai_proposal}
        note={`model ${record.initial_provider_metadata.model} · prompt ${record.initial_provider_metadata.prompt_name}@${record.initial_provider_metadata.prompt_version} · ${formatLatency(
          record.initial_provider_metadata.latency_ms,
        )}`}
      />
      <JevBlock critique={record.jev_critique} />
      {record.reconsideration_requested ? (
        <div className="card" style={{ padding: '8px 12px' }}>
          <Badge tone="warn">
            AI {record.reconsideration_performed ? 'RECONSIDERED' : 'DID NOT RECONSIDER'} AFTER JEV
          </Badge>
        </div>
      ) : (
        <div className="card" style={{ padding: '8px 12px' }}>
          <Badge tone="neutral">NO RECONSIDERATION REQUESTED</Badge>
        </div>
      )}
      {record.final_ai_decision ? (
        <div className="stage final">
          <div className="stage-head">
            <span className="stage-title">Final AI Decision</span>
            <Badge tone="ok">{proposalSummary(record.final_ai_decision.proposals[0])}</Badge>
          </div>
          <div className="stage-body">
            <div className="dim">{record.final_ai_decision.decision_summary}</div>
            {record.final_ai_decision.proposals[0]?.thesis ? (
              <div className="thesis" style={{ marginTop: 7 }}>
                <strong>Thesis:</strong> {record.final_ai_decision.proposals[0].thesis}
              </div>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="stage final">
          <div className="stage-head">
            <span className="stage-title">Final AI Decision</span>
            <Badge tone="neutral">NOT REACHED</Badge>
          </div>
        </div>
      )}
      <SafetyBlock record={record} />
      <PaperBlock record={record} />
      {record.failure_reason ? (
        <div className="card" style={{ borderColor: 'rgba(240,87,106,0.5)' }}>
          <div className="card-title">Failure reason</div>
          <div style={{ color: '#ffb3bd', fontFamily: 'var(--mono)', fontSize: 12 }}>{record.failure_reason}</div>
        </div>
      ) : null}
    </div>
  )
}
