/**
 * News-to-trade latency. Reports measured stage timings only; a stage that has not
 * happened yet reads "pending" rather than zero, so an incomplete chain is never
 * mistaken for a fast one.
 */

import { Badge, Empty, Stat } from '@/components/common/Primitives'
import { useNewsLatency } from '@/hooks/useNews'
import { formatTime } from '@/utils/format'

const STAGES: { label: string; key: string; title: string }[] = [
  {
    label: 'Source→ingest',
    key: 'source_to_ingestion_ms',
    title: 'publisher timestamp to the moment we stored it',
  },
  {
    label: 'Ingest→analysis',
    key: 'ingestion_to_analysis_ms',
    title: 'stored to the news analyst request starting',
  },
  {
    label: 'Analysis→decision',
    key: 'analysis_to_decision_ms',
    title: 'news analyst finished to the portfolio AI decision',
  },
  { label: 'News→trade', key: 'news_to_trade_ms', title: 'received to the paper order' },
]

export function NewsLatencyTable({ limit = 15 }: { limit?: number }) {
  const { data, isLoading } = useNewsLatency(limit)
  if (isLoading) return <Empty>Loading latency</Empty>

  const rows = data?.rows ?? []
  const summary = data?.news_to_trade_ms

  return (
    <div className="card">
      <div className="card-title">
        <span>News → trade latency</span>
        <Badge tone="neutral">
          {summary?.samples ? `${summary.samples} sample(s)` : 'no completed chain yet'}
        </Badge>
      </div>

      {summary?.samples ? (
        <div className="stat-grid">
          <Stat label="Fastest" value={ms(summary.min_ms)} />
          <Stat label="Median" value={ms(summary.median_ms)} />
          <Stat label="Slowest" value={ms(summary.max_ms)} />
        </div>
      ) : null}

      {rows.length === 0 ? (
        <Empty>
          No analysed news yet. Latency appears once news has been analysed and, where
          applicable, acted on.
        </Empty>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Received</th>
              <th>Symbols</th>
              <th>Source→ingest</th>
              <th>Ingest→analysis</th>
              <th>Analysis→decision</th>
              <th>News→trade</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.analysis_id} data-testid="latency-row">
                <td className="faint">{formatTime(row.received_at)}</td>
                <td>{row.symbols.join(', ') || '—'}</td>
                {STAGES.map((stage) => (
                  <td key={stage.key} title={stage.title}>
                    {ms(row[stage.key as keyof typeof row] as number | null)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function ms(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'pending'
  if (value < 1000) return `${value} ms`
  return `${(value / 1000).toFixed(2)} s`
}