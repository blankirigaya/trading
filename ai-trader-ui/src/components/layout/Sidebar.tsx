import { NavLink } from 'react-router-dom'

const LINKS: { to: string; label: string; group: string }[] = [
  { to: '/', label: 'Dashboard', group: 'Overview' },
  { to: '/market', label: 'Market', group: 'Overview' },
  { to: '/news', label: 'News', group: 'Overview' },
  { to: '/timeline', label: 'Timeline', group: 'Overview' },
  { to: '/positions', label: 'Positions', group: 'Paper Trading' },
  { to: '/portfolio', label: 'Portfolio', group: 'Paper Trading' },
  { to: '/trades', label: 'Trades', group: 'Paper Trading' },
  { to: '/decisions', label: 'Decisions', group: 'AI Reasoning' },
  { to: '/performance', label: 'Performance', group: 'AI Reasoning' },
  { to: '/bots', label: 'Bots', group: 'AI Reasoning' },
  { to: '/system', label: 'System', group: 'Control' },
]

export function Sidebar() {
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
            >
              {link.label}
            </NavLink>
          ))}
        </div>
      ))}
    </nav>
  )
}
