import type { CriticAssessment, DecisionRecord, PortfolioDecision, TradeProposal } from '@/api/types'
import { Badge } from '@/components/common/Primitives'
import { formatInr, formatLatency, formatPercent } from '@/utils/format'

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
  return (
    <div data-testid="decision-detail">
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
