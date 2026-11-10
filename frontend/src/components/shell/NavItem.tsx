import { NavigationIcon, type IconName } from './NavigationIcon'

export function NavItem({ active, icon, label, onClick }: { active: boolean; icon: IconName; label: string; onClick: () => void }) {
  return <button type="button" className={`nav-item ${active ? 'active' : ''}`} aria-current={active ? 'page' : undefined} onClick={onClick}><span className="nav-symbol"><NavigationIcon name={icon} /></span>{label}</button>
}
