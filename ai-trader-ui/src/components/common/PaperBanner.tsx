/**
 * Permanent paper-mode indicator. Rendered in the app shell on every page.
 * The UI must never visually imply that positions are real money.
 */
export function PaperBanner({ liveOrderExecution = false }: { liveOrderExecution?: boolean }) {
  return (
    <div className="paper-banner" role="status" aria-label="Paper trading mode">
      <strong>Paper Mode</strong>
      <span>Live Shadow &mdash; No Real Orders</span>
      <span>Simulated funds only</span>
      <span className="no-live-orders">Live order execution: {String(liveOrderExecution)}</span>
    </div>
  )
}
