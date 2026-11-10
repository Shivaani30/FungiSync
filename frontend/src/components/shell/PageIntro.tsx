import type { ReactNode } from 'react'

export function PageIntro({ eyebrow, title, children, actions }: { eyebrow?: string; title: string; children?: ReactNode; actions?: ReactNode }) {
  return <div className="page-intro"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h2>{title}</h2>{children}</div>{actions}</div>
}
