import { NavLink } from 'react-router-dom'

function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <path d={d} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

const ICONS: Record<string, string> = {
  '/': 'M2 2h5v5H2zM9 2h5v3H9zM2 9h5v5H2zM9 7h5v7H9z',
  '/market':
    'M2 13l3-4 2.5 2.5L11 7l3 3M2 13h12M2 13V2M2 13h12M14 13V6',
  '/news': 'M3 2h8v11H3zM3 5h8M3 8h8M11 2v3h2M5 13v2M9 13v2',
  '/timeline': 'M8 4v4l2.5 2M8 14A6 6 0 1 0 8 2a6 6 0 0 0 0 12z',
  '/positions': 'M2 5h12v8H2zM2 5l2-2h8l2 2M5 9h6',
  '/charts': 'M4 3v10M2.5 5.5h3M2.5 8.5h3M8 2v12M6.5 5h3M6.5 9.5h3M12 6v6M10.5 8h3',
  '/portfolio': 'M2 8h12M8 2v12M8 2a6 6 0 0 1 5.6 8H8V2zM8 14A6 6 0 0 1 8 2v12z',
  '/trades': 'M3 5h8l-2-2M13 11H5l2 2',
  '/decisions': 'M6 2h4v2h2v8h-2v2H6v-2H4V4h2zM6 6h4v4H6z',
  '/analytics': 'M2 12l3-4 2 2 3-5 2 3 2-3M2 14h12',
  '/performance': 'M2 13l4-5 3 3 3-5 2 2M2 2h12',
  '/bots': 'M5 5h6v5H5zM8 2v1.5M5 3.5h6M3 8v3M13 8v3M6.5 13v1.5M9.5 13v1.5',
  '/system': 'M3 5h4l1 1.5L9 5h4M3 11h2l1-1.5L7 11h7M13 4v3M5 10v3',
}

const LINKS: { to: string; label: string; group: string }[] = [
  { to: '/', label: 'Dashboard', group: 'Overview' },
  { to: '/market', label: 'Market', group: 'Overview' },
  { to: '/charts', label: 'Charts', group: 'Overview' },
  { to: '/news', label: 'News', group: 'Overview' },
  { to: '/timeline', label: 'Timeline', group: 'Overview' },
  { to: '/positions', label: 'Positions', group: 'Paper Trading' },
  { to: '/portfolio', label: 'Portfolio', group: 'Paper Trading' },
  { to: '/trades', label: 'Trades', group: 'Paper Trading' },
  { to: '/decisions', label: 'Decisions', group: 'AI Intelligence' },
  { to: '/performance', label: 'Performance', group: 'AI Intelligence' },
  { to: '/analytics', label: 'Analytics', group: 'AI Intelligence' },
  { to: '/bots', label: 'Bots', group: 'AI Intelligence' },
  { to: '/system', label: 'System', group: 'Control' },
]

export function Sidebar({
  collapsed,
  onToggle,
}: {
  collapsed: boolean
  onToggle: () => void
}) {
  const groups = [...new Set(LINKS.map((link) => link.group))]
  return (
    <nav className="app-nav" aria-label="Primary">
      {groups.map((group) => (
        <div key={group}>
          <div className="nav-section">{group}</div>
          {LINKS.filter((link) => link.group === group).map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.to === '/'}
              className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
              title={link.label}
            >
              <Icon d={ICONS[link.to] ?? 'M2 2h12v12H2z'} />
              <span>{link.label}</span>
            </NavLink>
          ))}
        </div>
      ))}
      <div className="nav-collapse">
        <button
          type="button"
          onClick={onToggle}
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          title={collapsed ? 'Expand navigation' : 'Collapse navigation'}
        >
          {collapsed ? '»' : '«'}
        </button>
      </div>
    </nav>
  )
}
