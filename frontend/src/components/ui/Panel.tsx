import type { ReactNode } from 'react'

export function Panel({ title, children }: { title: string; children: ReactNode }) {
  return <section className="panel"><h3 className="panel-heading">{title}</h3>{children}</section>
}
