/**
 * Confirmation gate for controls that change runtime behaviour.
 *
 * Rendered as a centred drawer over a backdrop. It owns no state: the caller
 * decides which action is pending and what confirmation does.
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
  return (
    <>
      <div className="drawer-backdrop" onClick={onCancel} />
      <div
        className="drawer"
        style={{
          left: '50%',
          right: 'auto',
          transform: 'translateX(-50%)',
          width: 'min(520px, 92vw)',
          zIndex: 41,
        }}
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
