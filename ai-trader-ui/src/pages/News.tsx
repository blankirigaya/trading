import { useMemo, useState } from 'react'

import type { NewsRow } from '@/api/newsTypes'
import { Badge, Empty, StateBadge } from '@/components/common/Primitives'
import { LiveNewsFeed, ProviderStatusStrip } from '@/components/news/LiveNews'
import { NewsLatencyTable } from '@/components/news/NewsLatency'
import { useNews, useNewsSources } from '@/hooks/useNews'
import { formatDateTime, formatLatency, formatPercent, formatTime } from '@/utils/format'

/**
 * Source material and AI interpretation are rendered as separate blocks so an AI
 * reading is never presented as a source fact. This page displays only; it never
 * influences a trade.
 */

function SourceBlock({ row }: { row: NewsRow }) {
  const { source } = row
  return (
    <div className="stage initial" data-testid="source-material">
      <div className="stage-head">
        <span className="stage-title">Source Material</span>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <Badge tone={source.is_official ? 'accent' : 'neutral'}>
            {source.is_official ? 'OFFICIAL' : 'SECONDARY'}
          </Badge>
          <Badge tone="neutral">{source.trust_level}</Badge>
        </div>
      </div>
      <div className="stage-body">
        <div className="dim" style={{ fontWeight: 600, marginBottom: 5 }}>
          {source.headline}
        </div>
        <div className="kv">
          <dt>Publisher</dt>
          <dd>{source.source_name}</dd>
          <dt>Source type</dt>
          <dd>{source.source_type}</dd>
          <dt>Category</dt>
          <dd>{source.category}</dd>
          <dt>Published</dt>
          <dd>{formatDateTime(source.published_at)}</dd>
          <dt>Received</dt>
          <dd>{formatDateTime(source.received_at)}</dd>
          <dt>Symbols</dt>
          <dd>{source.symbols.length ? source.symbols.join(', ') : 'none extracted'}</dd>
          <dt>Other sources</dt>
          <dd>{source.related_source_count}</dd>
          <dt>Content hash</dt>
          <dd>{source.content_hash.slice(0, 24)}…</dd>
        </div>
        {source.excerpt ? <div className="thesis" style={{ marginTop: 8 }}>{source.excerpt}</div> : null}
        {/* Opens in the user's normal browser; the app shell denies in-app navigation. */}
        <div style={{ marginTop: 8 }}>
          <a
            className="btn"
            href={source.canonical_url}
            target="_blank"
            rel="noreferrer noopener external"
            data-testid="source-link"
          >
            Open original source
          </a>
        </div>
      </div>
    </div>
  )
}

function AnalysisBlock({ row }: { row: NewsRow }) {
  const analysis = row.ai_analysis
  if (!analysis) {
    return (
      <div className="stage final" data-testid="ai-analysis">
        <div className="stage-head">
          <span className="stage-title">AI Analysis</span>
          <Badge tone="neutral">NOT ANALYSED</Badge>
        </div>
        <div className="faint">The remote AI has not assessed this item yet.</div>
      </div>
    )
  }
  if (analysis.status !== 'COMPLETED') {
    return (
      <div className="stage final" data-testid="ai-analysis">
        <div className="stage-head">
          <span className="stage-title">AI Analysis</span>
          <Badge tone={analysis.status === 'FAILED' ? 'bad' : 'neutral'}>{analysis.status}</Badge>
        </div>
        <div className="faint">{analysis.failure_reason ?? 'No interpretation available.'}</div>
      </div>
    )
  }
  return (
    <div className="stage jev" data-testid="ai-analysis">
      <div className="stage-head">
        <span className="stage-title">AI Analysis</span>
        <Badge tone="warn">{analysis.event_type}</Badge>
      </div>
      <div className="stage-body">
        <div className="kv">
          <dt>Materiality</dt>
          <dd>{analysis.materiality ?? 'UNCLEAR'}</dd>
          <dt>Direction</dt>
          <dd>{analysis.direction ?? 'UNCLEAR'}</dd>
          <dt>Horizon</dt>
          <dd>{analysis.horizon ?? 'UNCLEAR'}</dd>
          <dt>Confidence</dt>
          <dd>{formatPercent(analysis.confidence)}</dd>
          <dt>Model</dt>
          <dd>{analysis.model ?? 'unknown'}</dd>
          <dt>Latency</dt>
          <dd>{formatLatency(analysis.latency_ms)}</dd>
        </div>
        <div className="thesis" style={{ marginTop: 8 }}>{analysis.summary}</div>
        {analysis.evidence.length ? (
          <>
            <div className="faint" style={{ marginTop: 8 }}>
              Evidence
            </div>
            <ul className="evidence-list">
              {analysis.evidence.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </>
        ) : null}
        {analysis.uncertainties.length ? (
          <>
            <div className="faint" style={{ marginTop: 8 }}>
              Uncertainties
            </div>
            <ul className="evidence-list">
              {analysis.uncertainties.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </>
        ) : null}
      </div>
    </div>
  )
}

function NewsDetail({ row, onClose }: { row: NewsRow; onClose: () => void }) {
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" data-testid="news-drawer">
        <div className="drawer-head">
          <h2>News detail</h2>
          <button type="button" className="btn" onClick={onClose}>
            Close
          </button>
        </div>
        <SourceBlock row={row} />
        <AnalysisBlock row={row} />
        {row.review_triggered ? (
          <div className="card" style={{ padding: '8px 12px' }}>
            <Badge tone="ok">PORTFOLIO REVIEW TRIGGERED</Badge>
          </div>
        ) : null}
      </aside>
    </>
  )
}

export function News() {
  const { data: rows, isLoading } = useNews(200)
  const { data: sources } = useNewsSources()
  const [filter, setFilter] = useState<'ALL' | 'OFFICIAL' | 'SECONDARY'>('ALL')
  const [materiality, setMateriality] = useState('')
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<string | null>(null)

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return (rows ?? []).filter((row) => {
      if (filter === 'OFFICIAL' && !row.source.is_official) return false
      if (filter === 'SECONDARY' && row.source.is_official) return false
      if (materiality && row.ai_analysis?.materiality !== materiality) return false
      if (
        needle &&
        !row.source.headline.toLowerCase().includes(needle) &&
        !row.source.source_name.toLowerCase().includes(needle)
      )
        return false
      return true
    })
  }, [rows, filter, materiality, search])

  const selectedRow = (rows ?? []).find((row) => row.source.item_id === selected) ?? null
  const officialCount = (sources ?? []).filter((source) => source.source_type === 'OFFICIAL').length

  if (isLoading) return <Empty>Loading news</Empty>

  return (
    <div>
      <ProviderStatusStrip />
      <LiveNewsFeed limit={30} />
      <NewsLatencyTable />
      <div className="card">
        <div className="card-title">
          <span>News sources</span>
          <div style={{ display: 'flex', gap: 6 }}>
            <Badge tone="accent">{officialCount} official</Badge>
            <Badge tone="neutral">{(sources ?? []).length} configured</Badge>
          </div>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {(sources ?? []).map((source) => (
            <span key={source.source_id} className="provider-chip">
              <span className="label">{source.source_type}</span>
              <span className="value">{source.name}</span>
              <StateBadge state={source.enabled ? 'CONNECTED' : 'UNKNOWN'} />
            </span>
          ))}
        </div>
      </div>

      <div className="card">
        <div className="card-title">
          <span>News feed</span>
          <Badge tone="neutral">{filtered.length} items</Badge>
        </div>
        <div className="filter-bar">
          <input
            placeholder="Search headlines…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="Search news"
          />
          {(['ALL', 'OFFICIAL', 'SECONDARY'] as const).map((value) => (
            <button
              key={value}
              type="button"
              className={`btn${filter === value ? ' primary' : ''}`}
              onClick={() => setFilter(value)}
            >
              {value}
            </button>
          ))}
          <select
            value={materiality}
            onChange={(event) => setMateriality(event.target.value)}
            aria-label="Filter by AI materiality"
          >
            <option value="">All materiality</option>
            {['LOW', 'MEDIUM', 'HIGH', 'VERY_HIGH', 'UNCLEAR'].map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </div>

        {filtered.length === 0 ? (
          <Empty>
            No news yet. Run <code>uv run python -m ai_trader news poll --once</code> on the backend.
          </Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Source</th>
                <th>Official/Secondary</th>
                <th>Symbol</th>
                <th>Headline</th>
                <th>AI Materiality</th>
                <th>AI Direction</th>
                <th>Review Triggered</th>
                <th>Analysis Status</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((row: NewsRow) => (
                <tr
                  key={row.source.item_id}
                  className="clickable"
                  onClick={() => setSelected(row.source.item_id)}
                  data-testid="news-row"
                >
                  <td className="faint">{formatTime(row.source.published_at)}</td>
                  <td>{row.source.source_name}</td>
                  <td>
                    <Badge tone={row.source.is_official ? 'accent' : 'neutral'}>
                      {row.source.is_official ? 'OFFICIAL' : 'SECONDARY'}
                    </Badge>
                  </td>
                  <td>{row.source.symbols.join(', ') || '—'}</td>
                  <td>{row.source.headline.slice(0, 90)}</td>
                  <td>{row.ai_analysis?.materiality ?? '—'}</td>
                  <td>{row.ai_analysis?.direction ?? '—'}</td>
                  <td>{row.review_triggered ? 'YES' : '—'}</td>
                  <td className="faint">{row.ai_analysis?.status ?? 'PENDING'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {selectedRow ? <NewsDetail row={selectedRow} onClose={() => setSelected(null)} /> : null}
    </div>
  )
}
