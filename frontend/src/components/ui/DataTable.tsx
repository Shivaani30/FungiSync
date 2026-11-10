import type { ReactNode } from 'react'

export function DataTable({ headers, children }: { headers: string[]; children: ReactNode }) {
  return <div className="data-table-wrap"><table className="feature-table"><thead><tr>{headers.map(header => <th key={header}>{header}</th>)}</tr></thead><tbody>{children}</tbody></table></div>
}
