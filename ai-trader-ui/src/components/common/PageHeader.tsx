/** Consistent page masthead: eyebrow, title, subtitle, optional actions. */
export function PageHeader({
  eyebrow,
  title,
  sub,
  actions,
}: {
  eyebrow: string
  title: string
  sub?: React.ReactNode
  actions?: React.ReactNode
}) {
  return (
    <div className="page-head">
      <div>
        <h2 className="page-title">
          <span className="page-eyebrow">{eyebrow}</span>
          {title}
        </h2>
        {sub ? <p className="page-sub">{sub}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </div>
  )
}
