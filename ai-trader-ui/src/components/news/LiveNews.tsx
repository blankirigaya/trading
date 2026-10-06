/**
 * Live news activity, driven by the backend WebSocket stream.
 *
 * No polling and no manual refresh: the backend emits `news_received`,
 * `news_analysis_started`, `news_analysis_completed`, and `news_review_requested`, and
 * this renders them in arrival order. It is a read-only transcript. Nothing here can
 * request, approve, or execute a trade.
 */

import { useMemo } from 'react'

import type { TimelineEvent } from '@/api/types'
import { Badge, Empty } from '@/components/common/Primitives'
import { useNewsProviders } from '@/hooks/useNews'
import { useLiveEvents } from '@/store/liveEvents'
import { formatTime } from '@/utils/format'

const NEWS_KINDS = new Set([
  'news_received',
  'news_correction_received',
  'news_deduplicated',
  'news_analysis_started',
  'news_analysis_completed',
  'news_analysis_failed',
  'news_review_requested',
  'news_review_started',
  'news_ingestion_started',
  'news_ingestion_stopped',
  'news_feed_error',
])

const STAGE_LABEL: Record<string, string> = {
  news_received: 'SOURCE',
  news_correction_received: 'CORRECTION',
  news_deduplicated: 'DEDUPLICATED',
  news_analysis_started: 'AI NEWS',
  news_analysis_completed: 'AI NEWS',
  news_analysis_failed: 'AI NEWS FAILED',
  news_review_requested: 'REVIEW',
  news_review_started: 'TRADING AI',
  news_ingestion_started: 'SYSTEM',
  news_ingestion_stopped: 'SYSTEM',
  news_feed_error: 'SYSTEM',
}

function toneFor(kind: string): 'ok' | 'warn' | 'bad' | 'accent' | 'neutral' {
  if (kind === 'news_analysis_failed' || kind === 'news_feed_error') return 'bad'
  if (kind === 'news_review_requested' || kind === 'news_review_started') return 'warn'
  if (kind === 'news_analysis_completed') return 'ok'
  if (kind === 'news_received' || kind === 'news_correction_received') return 'accent'
  return 'neutral'
}

/** Per-source liveness. STOPPED means no session is ingesting right now. */
export function ProviderStatusStrip() {
  const { data, isLoading } = useNewsProviders()
  if (isLoading) return <Empty>Checking news feeds</Empty>
  const providers = data?.providers ?? []

  return (
    <div className="card">
      <div className="card-title">
        <span>News feeds</span>
        <Badge tone={data?.ingestion_running ? 'ok' : 'neutral'}>
          {data?.ingestion_running ? 'INGESTING' : 'IDLE'}
        </Badge>
      </div>
      {providers.length === 0 ? <Empty>No news sources configured</Empty> : null}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }} data-testid="provider-strip">
        {providers.map((provider) => {
          const since = provider.seconds_since_last_item
          const detail =
            provider.status === 'LIVE'
              ? since === null
                ? 'no items yet'
                : `last item ${Math.round(since)}s ago`
              : provider.last_error ?? 'not ingesting'
          return (
            <span key={provider.source_id} className="provider-chip">
              <span className="label">{provider.source_type === 'OFFICIAL' ? 'OFFICIAL' : 'SECONDARY'}</span>
              <span className="value">{shortName(provider.source_id)}</span>
              <Badge tone={providerTone(provider.status)}>{provider.status}</Badge>
              <span className="faint" title={provider.last_error ?? undefined}>
                {detail}
              </span>
            </span>
          )
        })}
      </div>
      {data ? (
        <div className="faint" style={{ marginTop: 8 }}>
          Analysis queue {data.queue.depth}/{data.queue.capacity} · max concurrent{' '}
          {data.queue.max_concurrent_analyses} · analysed {data.pipeline.analysed} · dropped{' '}
          {data.pipeline.dropped_queue_full}
        </div>
      ) : null}
    </div>
  )
}

function providerTone(status: string): 'ok' | 'warn' | 'bad' | 'neutral' {
  if (status === 'LIVE') return 'ok'
  if (status === 'DEGRADED' || status === 'STARTING') return 'warn'
  if (status === 'OFFLINE') return 'bad'
  return 'neutral'
}

function shortName(sourceId: string): string {
  return sourceId
    .split('-')
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

export function LiveNewsFeed({ limit = 30 }: { limit?: number }) {
  const events = useLiveEvents((state) => state.events)

  const newsEvents = useMemo(
    () => events.filter((event: TimelineEvent) => NEWS_KINDS.has(event.kind)).slice(-limit).reverse(),
    [events, limit],
  )

  if (newsEvents.length === 0) {
    return (
      <div className="card">
        <div className="card-title">
          <span>Live news</span>
          <Badge tone="neutral">Waiting</Badge>
        </div>
        <Empty>
          News appears here the moment a source publishes it. Start a shadow session to
          begin continuous ingestion.
        </Empty>
      </div>
    )
  }

  return (
    <div className="card">
      <div className="card-title">
        <span>Live news</span>
        <Badge tone="accent">{newsEvents.length}</Badge>
      </div>
      <ul className="timeline" data-testid="live-news-feed">
        {newsEvents.map((event: TimelineEvent) => (
          <li
            key={event.seq ?? `${event.timestamp}-${event.kind}`}
            className={`timeline-item ${event.category}`}
            data-testid="live-news-item"
          >
            <span className="timeline-time">{formatTime(event.timestamp)}</span>
            <span className="timeline-cat">
              <Badge tone={toneFor(event.kind)}>
                {event.symbol ? `${STAGE_LABEL[event.kind] ?? event.kind} / ${event.symbol}` : (STAGE_LABEL[event.kind] ?? event.kind)}
              </Badge>
            </span>
            <span>{event.message}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}