import { createPortal } from 'react-dom'

/**
 * Confirmation gate for controls that change runtime behaviour.
 *
 * Rendered as a centred modal over a backdrop via a portal to document.body,
 * so no ancestor stacking context or drawer slide-in animation can move it
 * off-screen. It owns no state: the caller decides which action is pending
 * and what confirmation does.
 */
export function ConfirmDialog({
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
  return createPortal(
    <>
      <div className="drawer-backdrop" onClick={onCancel} />
      <div
        className="drawer"
        style={{
          left: '50%',
          right: 'auto',
          top: '50%',
          bottom: 'auto',
          transform: 'translate(-50%, -50%)',
          animation: 'none',
          width: 'min(520px, 92vw)',
          maxHeight: '88vh',
          border: '1px solid var(--border-strong)',
          borderRadius: 10,
          zIndex: 41,
        }}
        role="dialog"
        aria-modal="true"
        data-testid={testId}
      >
        <div className="drawer-head">
          <h2>{title}</h2>
        </div>
        <div className="card" style={{ marginBottom: 12 }}>
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
    </>,
    document.body,
  )
}
