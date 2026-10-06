import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { api } from '@/api/client'
import { Badge, Empty, StateBadge } from '@/components/common/Primitives'
import { useLiveEvents } from '@/store/liveEvents'
import { useMarketStatus, useSystemStatus } from '@/hooks/useSystemStatus'
import { formatDateTime, formatInr } from '@/utils/format'

/** Confirmation gate for destructive controls. Required for kill switch and reset. */
function ConfirmDialog({
  title,
  body,
  confirmLabel,
  onConfirm,
  onCancel,
  testId,
}: {
  title: string
  body: string
  confirmLabel: string
  onConfirm: () => void
  onCancel: () => void
  testId: string
}) {
  return (
    <>
      <div className="drawer-backdrop" onClick={onCancel} />
      <div
        className="drawer"
        style={{ left: '50%', right: 'auto', transform: 'translateX(-50%)', width: 'min(520px, 92vw)', zIndex: 41 }}
        role="dialog"
        aria-modal="true"
        data-testid={testId}
      >
        <div className="drawer-head">
          <h2>{title}</h2>
        </div>
        <div className="card">
          <div className="stage-body">{body}</div>
        </div>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button type="button" className="btn" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="btn danger" onClick={onConfirm} data-testid="confirm-button">
            {confirmLabel}
          </button>
        </div>
      </div>
    </>
  )
}

export function System() {
  const queryClient = useQueryClient()
  const { data: status } = useSystemStatus()
  const { data: market } = useMarketStatus()
  const streamState = useLiveEvents((state) => state.streamState)
  const [pending, setPending] = useState<'kill-switch' | 'reset' | null>(null)
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
          <dt>Watched symbols</dt>
          <dd>{status.shadow_symbols.join(', ')}</dd>
          <dt>Starting capital</dt>
          <dd>{formatInr(status.shadow_initial_capital_inr)}</dd>
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
