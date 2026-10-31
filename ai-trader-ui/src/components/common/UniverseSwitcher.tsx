import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { api } from '@/api/client'
import type { UniverseMode } from '@/api/types'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { Badge } from '@/components/common/Primitives'
import { useSystemStatus } from '@/hooks/useSystemStatus'

const MODES: Array<{ id: UniverseMode; label: string; hint: string }> = [
  { id: 'cheap', label: 'Cheap', hint: '50 symbols under ₹200' },
  { id: 'mixed', label: 'Mixed', hint: '25 cheap + 25 expensive' },
  { id: 'expensive', label: 'Expensive', hint: '50 symbols at or above ₹200' },
]

const BODY: Record<UniverseMode, string> = {
  cheap: 'The AI will review 50 liquid symbols trading below ₹200, re-classified from live FYERS prices.',
  mixed: 'The AI will review 25 cheap and 25 expensive liquid symbols, re-classified from live FYERS prices.',
  expensive: 'The AI will review 50 liquid symbols trading at or above ₹200, re-classified from live FYERS prices.',
}

/**
 * Operator control for the auto-classified review universe.
 *
 * The switch is persisted by the backend and takes effect at the next review
 * cycle (resubscribing mid-review would blank the board). Open positions stay
 * on the feed and remain closable in every mode.
 */
export function UniverseSwitcher() {
  const { data: status } = useSystemStatus()
  const queryClient = useQueryClient()
  const [pending, setPending] = useState<UniverseMode | null>(null)
  const [busy, setBusy] = useState(false)

  const current: UniverseMode = status?.universe_mode ?? 'cheap'
  const watched = status?.universe_selected?.length ?? status?.shadow_symbols.length ?? 0
  const masterSize = status?.universe_master_size ?? 0

  const apply = async (mode: UniverseMode) => {
    setBusy(true)
    try {
      await api.setUniverseMode(mode)
      await queryClient.invalidateQueries({ queryKey: ['systemStatus'] })
    } finally {
      setBusy(false)
      setPending(null)
    }
  }

  return (
    <div className="card">
      <div className="card-title">
        <span>AI universe</span>
        <Badge tone="accent" testId="universe-badge">
          UNIVERSE · {current.toUpperCase()}
        </Badge>
      </div>
      <div className="faint" style={{ marginBottom: 10 }}>
        Auto-classified from live FYERS prices. Applies at the next AI review; open positions stay
        closable.
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        {MODES.map((mode) => (
          <button
            key={mode.id}
            type="button"
            className={mode.id === current ? 'btn primary' : 'btn'}
            disabled={busy || mode.id === current}
            onClick={() => setPending(mode.id)}
            data-testid={`universe-${mode.id}`}
            title={mode.hint}
          >
            {mode.label}
          </button>
        ))}
      </div>
      <div className="faint" style={{ marginTop: 8 }}>
        Reviewing {watched} of {masterSize || '—'} master symbols.
      </div>

      {pending ? (
        <ConfirmDialog
          testId="universe-dialog"
          title={`Switch the AI universe to ${pending}?`}
          body={`${BODY[pending]} The feed resubscribes at the next review cycle. Positions are not closed.`}
          confirmLabel="Switch universe"
          onCancel={() => setPending(null)}
          onConfirm={() => void apply(pending)}
        />
      ) : null}
    </div>
  )
}
