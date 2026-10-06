import { useMemo, useState } from 'react'

import type { EventCategory, TimelineEvent } from '@/api/types'
import { Badge, Empty } from '@/components/common/Primitives'
import { useLiveEvents } from '@/store/liveEvents'
import { formatTime } from '@/utils/format'

const CATEGORIES: (EventCategory | 'ALL')[] = [
  'ALL',
  'MARKET',
  'AI',
  'JEV',
  'SAFETY',
  'PAPER',
  'SYSTEM',
  'ERROR',
  'JOURNAL',
]

/** Chronological live event stream fed by the backend WebSocket. */
export function LiveTimeline({ limit, compact = false }: { limit?: number; compact?: boolean }) {
  const events = useLiveEvents((state) => state.events)
  const streamState = useLiveEvents((state) => state.streamState)
  const [filter, setFilter] = useState<EventCategory | 'ALL'>('ALL')
  const [search, setSearch] = useState('')

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return events
      .filter((event) => filter === 'ALL' || event.category === filter)
      .filter(
        (event) =>
          !needle ||
          event.message.toLowerCase().includes(needle) ||
          (event.symbol ?? '').toLowerCase().includes(needle),
      )
      .slice(-(limit ?? 200))
      .reverse()
  }, [events, filter, search, limit])

  const counts = useMemo(() => {
    const map = new Map<string, number>()
    for (const event of events) map.set(event.category, (map.get(event.category) ?? 0) + 1)
    return map
  }, [events])

  return (
    <div className="card" data-testid="timeline">
      <div className="card-title">
        <span>Live event timeline</span>
        <Badge tone={streamState === 'open' ? 'ok' : streamState === 'closed' ? 'bad' : 'warn'}>
          STREAM {streamState.toUpperCase()}
        </Badge>
      </div>

      {!compact ? (
        <div className="filter-bar">
          <input
            placeholder="Filter messages…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="Filter timeline"
          />
          {CATEGORIES.map((category) => (
            <button
              key={category}
              type="button"
              className={`btn${filter === category ? ' primary' : ''}`}
              onClick={() => setFilter(category)}
            >
              {category}
              {category !== 'ALL' && counts.get(category) ? ` (${counts.get(category)})` : ''}
            </button>
          ))}
        </div>
      ) : null}

      {filtered.length === 0 ? (
        <Empty>No events yet. Events appear here as the backend reports them.</Empty>
      ) : (
        <ul className="timeline">
          {filtered.map((event: TimelineEvent) => (
            <li key={event.seq ?? `${event.timestamp}-${event.kind}`} className={`timeline-item ${event.category}`}>
              <span className="timeline-time">{formatTime(event.timestamp)}</span>
              <span className="timeline-cat" style={{ color: 'var(--text-dim)' }}>
                {event.category}
              </span>
              <span>
                {event.message}
                {event.decision_id ? (
                  <span className="faint"> · {event.decision_id.slice(0, 8)}</span>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
