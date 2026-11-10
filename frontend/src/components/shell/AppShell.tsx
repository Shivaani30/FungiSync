import type { ReactNode } from 'react'
import { Sidebar, type NavigationItem } from './Sidebar'
import { Topbar } from './Topbar'

export function AppShell<T extends string>({ items, active, onNavigate, children }: { items: NavigationItem<T>[]; active: T; onNavigate: (page: T) => void; children: ReactNode }) {
  const pageTitle = items.find(item => item.id === active)?.label ?? 'FungiSync'
  return <div className="app-shell"><Sidebar items={items} active={active} onNavigate={onNavigate} /><div className="workspace"><Topbar pageTitle={pageTitle} /><main className="main-content">{children}</main></div></div>
}
