import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { api } from '@/api/client'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { Badge, Empty, StateBadge } from '@/components/common/Primitives'
import { useLiveEvents } from '@/store/liveEvents'
import { useMarketStatus, useSystemStatus } from '@/hooks/useSystemStatus'
import { formatDateTime, formatInr } from '@/utils/format'

export function System() {
  const queryClient = useQueryClient()
  const { data: status } = useSystemStatus()
  const { data: market } = useMarketStatus()
  const streamState = useLiveEvents((state) => state.streamState)
  const [pending, setPending] = useState<'kill-switch' | 'reset' | 'capital-cap' | 'position-cap' | 'tier1-news' | 'news-analysis' | 'sonnet-escalation' | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['systemStatus'] })
    void queryClient.invalidateQueries({ queryKey: ['portfolio'] })
    void queryClient.invalidateQueries({ queryKey: ['trades'] })
    void queryClient.invalidateQueries({ queryKey: ['decisions'] })
  }

  const run = async (label: string, action: () => Promise<unknown>) => {
    setBusy(label)
    try {
      await action()
      refresh()
    } finally {
      setBusy(null)
      setPending(null)
    }
  }

  if (!status) return <Empty>Backend offline &mdash; no system status available</Empty>

  const killSwitchOn = status.kill_switch_state
  const capitalCapDisabled = status.capital_cap_disabled === true
  const positionCapDisabled = status.position_count_cap_disabled === true
  const aiCap = formatInr(status.max_ai_capital_inr ?? '50000')
  const perPositionCap = formatInr(status.max_single_position_inr ?? '25000')
  const maxPositions = status.max_open_positions ?? 5
  const tier1On = status.tier1_news_enabled !== false
  const analysisOn = status.news_analysis_enabled !== false
  const escalationOn = status.sonnet_escalation_enabled !== false
  const escalationsToday = status.sonnet_escalations_today ?? 0
  const escalationCap = status.ai_max_sonnet_escalations_per_day ?? 20

  return (
    <div>
      <div className="card">
        <div className="card-title">
          <span>Runtime</span>
          <Badge tone="paper">{status.execution_provider}</Badge>
        </div>
        <div className="kv">
          <dt>Mode</dt>
          <dd>{status.mode}</dd>
          <dt>Environment</dt>
          <dd>{status.environment}</dd>
          <dt>Market provider</dt>
          <dd>{status.market_provider}</dd>
          <dt>Market connected</dt>
          <dd>{String(status.market_connected)}</dd>
          <dt>Feed mode</dt>
          <dd>{market?.feed_mode ?? 'n/a'}</dd>
          <dt>Universe mode</dt>
          <dd data-testid="system-universe-mode">
            {(status.universe_mode ?? 'cheap').toUpperCase()} &middot; {status.universe_selected?.length ?? status.shadow_symbols.length} symbols
          </dd>
          <dt>Watched symbols</dt>
          <dd>{status.shadow_symbols.join(', ')}</dd>
          <dt>Starting capital</dt>
          <dd>{formatInr(status.shadow_initial_capital_inr)}</dd>
          <dt>Capital cap</dt>
          <dd data-testid="system-capital-cap">
            {capitalCapDisabled
              ? 'DISABLED · AI sizes within available cash'
              : `${aiCap} AI capital · ${perPositionCap} per position`}
          </dd>
          <dt>Position-count cap</dt>
          <dd data-testid="system-position-cap">
            {positionCapDisabled
              ? 'DISABLED · AI decides how many positions to hold'
              : `At most ${maxPositions} open positions`}
          </dd>
          <dt>Tier-1 news desks</dt>
          <dd data-testid="system-tier1-news">
            {tier1On ? 'ON · CNBC-TV18, NDTV Profit, Business Today' : 'OFF · filings and dailies only'}
          </dd>
          <dt>News AI analysis</dt>
          <dd data-testid="system-news-analysis">
            {analysisOn ? 'ON · headlines analysed as they arrive' : 'OFF · headlines ingest only, no AI calls'}
          </dd>
          <dt>Sonnet escalation</dt>
          <dd data-testid="system-sonnet-escalation">
            {escalationOn
              ? `ON · news reviews on Sonnet (${escalationsToday}/${escalationCap} today)`
              : 'OFF · news reviews on the cadence model'}
          </dd>
          <dt>Review interval</dt>
          <dd>{status.portfolio_review_interval_seconds}s</dd>
          <dt>Session running</dt>
          <dd>{String(status.session_running)}</dd>
          <dt>Event stream</dt>
          <dd>{streamState}</dd>
          <dt>Live broker execution</dt>
          <dd data-testid="system-live-execution">DISABLED</dd>
        </div>
      </div>

      <div className="card">
        <div className="card-title">Providers</div>
        <div className="kv">
          <dt>AI provider</dt>
          <dd>{status.ai_provider}</dd>
          <dt>AI server</dt>
          <dd>{status.ai_server ?? 'not configured'}</dd>
          <dt>AI model</dt>
          <dd>{status.ai_model || 'not configured'}</dd>
          <dt>Inference location</dt>
          <dd>{status.ai_inference_location}</dd>
          <dt>AI reachable</dt>
          <dd>
            <StateBadge
              state={
                status.ai_server_reachable === true
                  ? 'CONNECTED'
                  : status.ai_server_reachable === false
                    ? 'DEGRADED'
                    : 'UNKNOWN'
              }
            />
          </dd>
          <dt>Last AI success</dt>
          <dd>{formatDateTime(status.last_ai_success_at)}</dd>
          <dt>Last AI failure</dt>
          <dd>{formatDateTime(status.last_ai_failure_at)}</dd>
          <dt>Last AI error</dt>
          <dd>{status.last_ai_error ?? 'none'}</dd>
          <dt>Jev enabled</dt>
          <dd>{String(status.jev_enabled)}</dd>
          <dt>Jev model</dt>
          <dd>{status.jev_model ?? 'not configured'}</dd>
          <dt>Database</dt>
          <dd>{status.database}</dd>
          <dt>Execution</dt>
          <dd>{status.execution_provider} (simulated)</dd>
        </div>
      </div>

      {/*
        Control surface. There is deliberately no buy, sell, or order control:
        the UI is an observer and a safe controller of the review loop only.
      */}
      <div className="card">
        <div className="card-title">Controls</div>
        <div className="faint" style={{ marginBottom: 10 }}>
          The AI decides whether to trade. These controls only start or stop the review loop and
          affect the simulated account. There are no order controls by design.
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          <button
            type="button"
            className="btn"
            disabled={busy !== null}
            onClick={() => run('review', api.requestReview)}
            data-testid="control-review"
          >
            Request one AI review
          </button>
          <button
            type="button"
            className="btn"
            disabled={busy !== null}
            onClick={() => run('start', api.startSession)}
          >
            Start review loop
          </button>
          <button
            type="button"
            className="btn"
            disabled={busy !== null}
            onClick={() => run('stop', api.stopSession)}
          >
            Stop review loop
          </button>
          <button
            type="button"
            className="btn"
            disabled={busy !== null}
            onClick={() => setPending('capital-cap')}
            data-testid="control-capital-cap"
          >
            {capitalCapDisabled ? 'Restore capital cap' : 'Disable capital cap'}
          </button>
          <button
            type="button"
            className="btn"
            disabled={busy !== null}
            onClick={() => setPending('position-cap')}
            data-testid="control-position-cap"
          >
            {positionCapDisabled ? 'Restore position cap' : 'Disable position cap'}
          </button>
          <button
            type="button"
            className="btn"
            disabled={busy !== null}
            onClick={() => setPending('tier1-news')}
            data-testid="control-tier1-news"
          >
            {tier1On ? 'Turn off Tier-1 news' : 'Turn on Tier-1 news'}
          </button>
          <button
            type="button"
            className="btn"
            disabled={busy !== null}
            onClick={() => setPending('news-analysis')}
            data-testid="control-news-analysis"
          >
            {analysisOn ? 'Turn off news analysis' : 'Turn on news analysis'}
          </button>
          <button
            type="button"
            className="btn"
            disabled={busy !== null}
            onClick={() => setPending('sonnet-escalation')}
            data-testid="control-sonnet-escalation"
          >
            {escalationOn ? 'Turn off Sonnet escalation' : 'Turn on Sonnet escalation'}
          </button>
          <button
            type="button"
            className="btn danger"
            disabled={busy !== null}
            onClick={() => setPending('kill-switch')}
            data-testid="control-kill-switch"
          >
            {killSwitchOn ? 'Release' : 'Engage'} kill switch
          </button>
          <button
            type="button"
            className="btn danger"
            disabled={busy !== null}
            onClick={() => setPending('reset')}
            data-testid="control-reset"
          >
            Reset paper account
          </button>
        </div>
      </div>

      {pending === 'capital-cap' ? (
        <ConfirmDialog
          testId="capital-cap-dialog"
          title={capitalCapDisabled ? 'Restore the capital cap?' : 'Disable the capital cap?'}
          body={
            capitalCapDisabled
              ? `New buys will again be limited to ${aiCap} AI capital, ${perPositionCap} per position, and the gross-exposure cap. Open positions stay untouched and remain closable.`
              : `New buys will no longer be limited by the fixed AI capital, single-position, or gross-exposure caps. The AI sizes each position with its own judgement inside available paper cash; exits still apply${positionCapDisabled ? '' : ` and the ${maxPositions}-position limit still applies`}. Paper trading only.`
          }
          confirmLabel={capitalCapDisabled ? 'Restore cap' : 'Disable cap'}
          onCancel={() => setPending(null)}
          onConfirm={() => run('capital-cap', () => api.setCapitalCap(!capitalCapDisabled))}
        />
      ) : null}

      {pending === 'position-cap' ? (
        <ConfirmDialog
          testId="position-cap-dialog"
          title={positionCapDisabled ? 'Restore the position cap?' : 'Disable the position cap?'}
          body={
            positionCapDisabled
              ? `New symbols will again be limited to at most ${maxPositions} open positions. Open positions stay untouched and remain closable.`
              : `The AI will no longer be limited to ${maxPositions} open positions. It decides how many symbols to hold with its own judgement inside available paper cash; exits and the money caps still apply unless separately disabled. Paper trading only.`
          }
          confirmLabel={positionCapDisabled ? 'Restore cap' : 'Disable cap'}
          onCancel={() => setPending(null)}
          onConfirm={() => run('position-cap', () => api.setPositionCap(!positionCapDisabled))}
        />
      ) : null}

      {pending === 'tier1-news' ? (
        <ConfirmDialog
          testId="tier1-news-dialog"
          title={tier1On ? 'Turn off Tier-1 news desks?' : 'Turn on Tier-1 news desks?'}
          body={
            tier1On
              ? 'CNBC-TV18, NDTV Profit and Business Today stop polling immediately. Official filings and secondary dailies keep flowing. Takes effect at once, no restart.'
              : 'CNBC-TV18, NDTV Profit and Business Today resume polling on the normal 10s financial cadence. Takes effect at once, no restart.'
          }
          confirmLabel={tier1On ? 'Turn off' : 'Turn on'}
          onCancel={() => setPending(null)}
          onConfirm={() => run('tier1-news', () => api.setTier1News(!tier1On))}
        />
      ) : null}

      {pending === 'news-analysis' ? (
        <ConfirmDialog
          testId="news-analysis-dialog"
          title={analysisOn ? 'Turn off news AI analysis?' : 'Turn on news AI analysis?'}
          body={
            analysisOn
              ? 'Headlines keep ingesting and deduplicating, but no Sonnet analysis runs — so nothing new can trigger a news review either. Cadence reviews are unaffected. Takes effect at once.'
              : 'Headlines are analysed by Sonnet as they arrive again, and significant items can trigger news reviews. Takes effect at once.'
          }
          confirmLabel={analysisOn ? 'Turn off' : 'Turn on'}
          onCancel={() => setPending(null)}
          onConfirm={() => run('news-analysis', () => api.setNewsAnalysis(!analysisOn))}
        />
      ) : null}

      {pending === 'sonnet-escalation' ? (
        <ConfirmDialog
          testId="sonnet-escalation-dialog"
          title={escalationOn ? 'Turn off Sonnet escalation?' : 'Turn on Sonnet escalation?'}
          body={
            escalationOn
              ? `News-triggered reviews go back to the cheap cadence model. The ${escalationsToday} Sonnet review(s) already used today stay journalled.`
              : `News-triggered reviews run on Sonnet instead of the cadence model, bounded at ${escalationCap} per day. Routine 60s reviews stay on the cheap model.`
          }
          confirmLabel={escalationOn ? 'Turn off' : 'Turn on'}
          onCancel={() => setPending(null)}
          onConfirm={() => run('sonnet-escalation', () => api.setSonnetEscalation(!escalationOn))}
        />
      ) : null}

      {pending === 'kill-switch' ? (
        <ConfirmDialog
          testId="kill-switch-dialog"
          title={killSwitchOn ? 'Release kill switch?' : 'Engage kill switch?'}
          body={
            killSwitchOn
              ? 'Releasing the kill switch allows the AI to propose new trades again. The paper account and journal are preserved.'
              : 'Engaging the kill switch blocks all new AI decisions and paper orders immediately. Current state is preserved and no positions are closed.'
          }
          confirmLabel={killSwitchOn ? 'Release' : 'Engage'}
          onCancel={() => setPending(null)}
          onConfirm={() => run('kill', () => api.setKillSwitch(!killSwitchOn))}
        />
      ) : null}

      {pending === 'reset' ? (
        <ConfirmDialog
          testId="reset-dialog"
          title="Reset the paper account?"
          body="This clears simulated cash, positions, and paper orders. Journalled AI decisions are preserved. This cannot be undone."
          confirmLabel="Reset paper account"
          onCancel={() => setPending(null)}
          onConfirm={() => run('reset', api.resetPaper)}
        />
      ) : null}

      <ErrorPanel />
    </div>
  )
}

function ErrorPanel() {
  const lastError = useLiveEvents((state) => state.lastError)
  return (
    <div className="card">
      <div className="card-title">Last system error</div>
      {lastError ? (
        <div className="kv">
          <dt>Time</dt>
          <dd>{formatDateTime(lastError.timestamp)}</dd>
          <dt>Kind</dt>
          <dd>{lastError.kind}</dd>
          <dt>Message</dt>
          <dd>{lastError.message}</dd>
        </div>
      ) : (
        <Empty>No errors recorded in this session</Empty>
      )}
    </div>
  )
}
