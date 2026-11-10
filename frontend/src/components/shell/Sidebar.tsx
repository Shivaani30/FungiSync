import { NavItem } from './NavItem'
import type { IconName } from './NavigationIcon'

export type NavigationItem<T extends string> = { id: T; label: string; icon: IconName }

export function Sidebar<T extends string>({ items, active, onNavigate }: { items: NavigationItem<T>[]; active: T; onNavigate: (page: T) => void }) {
  return <aside className="sidebar" aria-label="Application navigation"><div className="brand"><span className="brand-mark" aria-hidden="true">✳</span><span>FungiSync <span className="brand-subtle">/ FDI</span></span></div><nav className="sidebar-nav" aria-label="Main navigation">{items.map(item => <NavItem key={item.id} active={active === item.id} icon={item.icon} label={item.label} onClick={() => onNavigate(item.id)} />)}</nav><div className="sidebar-foot"><strong>Distribution intelligence</strong><br />Traceable decisions, from harvest to handover.</div></aside>
}
