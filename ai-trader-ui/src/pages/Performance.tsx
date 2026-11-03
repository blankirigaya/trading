import { Empty, Stat } from '@/components/common/Primitives'
import { PageHeader } from '@/components/common/PageHeader'
import { usePerformance } from '@/hooks/usePortfolio'
import { formatInr, formatLatency, formatSignedInr, toNumber } from '@/utils/format'

/**
 * Reporting over backend counters only. No performance model, ranking, or
 * derived trading signal is computed here.
 */
export function Performance() {
  const { data } = usePerformance()
  if (!data) return <Empty>Loading performance summary</Empty>

  const actionEntries = Object.entries(data.actions)
  const totalActions = actionEntries.reduce((sum, [, count]) => sum + count, 0)

  return (
    <div>
      <PageHeader
        eyebrow="AI intelligence"
        title="Performance"
        sub="Backend-reported paper results. No models, rankings, or trade signals here."
      />
      <div className="card">
        <div className="card-title">Paper P&amp;L</div>
        <div className="kpi-hero">
          <Stat hero label="Total P&L" value={formatSignedInr(data.total_pnl)} tone={toNumber(data.total_pnl) < 0 ? 'neg' : 'pos'} testId="perf-total-pnl" />
          <Stat hero label="Realized P&L (net)" value={formatSignedInr(data.realized_pnl)} tone={toNumber(data.realized_pnl) < 0 ? 'neg' : 'pos'} testId="perf-realized-pnl" />
          <Stat hero label="Charges paid" value={formatInr(data.charges_paid)} tone="neg" testId="perf-charges-paid" />
          <Stat hero label="Gross realized P&L" value={formatSignedInr(data.gross_realized_pnl)} tone={toNumber(data.gross_realized_pnl) < 0 ? 'neg' : 'pos'} testId="perf-gross-pnl" />
        </div>
        <div className="stat-grid" style={{ marginTop: 10 }}>
          <Stat label="Unrealized P&L" value={formatSignedInr(data.unrealized_pnl)} />
          <Stat label="Cash" value={formatInr(data.cash)} />
          <Stat label="Open positions" value={data.open_positions} />
          <Stat label="Executed decisions" value={data.executed} />
          <Stat label="Failed decisions" value={data.failed} />
          <Stat label="Denied by safety" value={data.denied_by_safety} />
        </div>
      </div>

      <div className="card">
        <div className="card-title">AI decision analysis</div>
        <div className="stat-grid">
          <Stat label="Total decisions" value={data.total_decisions} />
          <Stat label="Jev approvals" value={data.jev_approvals} />
          <Stat label="Jev challenges" value={data.jev_challenges} />
          <Stat label="AI changed after Jev" value={data.ai_changed_after_jev} />
          <Stat label="AI retained after Jev" value={data.ai_retained_after_jev} />
          <Stat label="Avg AI latency" value={formatLatency(data.avg_ai_latency_ms)} />
          <Stat label="Avg Jev latency" value={formatLatency(data.avg_jev_latency_ms)} />
        </div>
        {totalActions > 0 ? (
          <div style={{ marginTop: 12 }}>
            <div className="faint" style={{ marginBottom: 6 }}>
              Actions taken by the AI
            </div>
            {actionEntries.map(([action, count]) => (
              <div key={action} style={{ display: 'flex', gap: 8, fontSize: 12, padding: '2px 0' }}>
                <span style={{ width: 170 }}>{action}</span>
                <span className="num">{count}</span>
                <span className="faint">
                  {totalActions ? `${((count / totalActions) * 100).toFixed(0)}%` : ''}
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </div>

      <div className="card">
        <div className="card-title">Outcome measurements</div>
        {data.outcome_measurements.length ? (
          <div className="faint">
            Backend reports {data.outcome_measurements.length} outcome measurements.
          </div>
        ) : (
          <Empty>
            The backend has not recorded post-decision outcome measurements yet (5m, 15m, 30m, 1h,
            end of day).
          </Empty>
        )}
      </div>
    </div>
  )
}
