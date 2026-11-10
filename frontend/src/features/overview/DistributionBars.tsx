import { EmptyState } from '../../components/ui/States'

export type DistributionEntry = { label: string; value: number; detail?: string }

export function DistributionBars({ entries, valueLabel = 'records' }: { entries: DistributionEntry[]; valueLabel?: string }) {
  const maximum = Math.max(...entries.map(entry => entry.value), 0)
  if (!entries.length || maximum === 0) return <EmptyState message={`No ${valueLabel} are available yet.`} />
  return <div className="distribution-bars" aria-label={`${valueLabel} distribution`}>{entries.map(entry => <div className="distribution-row" key={entry.label}><div className="distribution-label"><span>{entry.label}</span><strong>{entry.detail ?? entry.value.toLocaleString()}</strong></div><div className="distribution-track"><span style={{ width: `${Math.max(4, entry.value / maximum * 100)}%` }} /></div></div>)}</div>
}

export function QuantityComparison({ supply, demand }: { supply: number; demand: number }) {
  const maximum = Math.max(supply, demand, 0)
  if (maximum === 0) return <EmptyState message="No confirmed harvest or order quantity is available yet." />
  const rows = [{ label: 'Confirmed harvest', value: supply }, { label: 'Total ordered', value: demand }]
  return <div className="quantity-comparison" aria-label="Confirmed harvest and total ordered quantities">{rows.map(row => <div className="distribution-row" key={row.label}><div className="distribution-label"><span>{row.label}</span><strong>{formatKg(row.value)}</strong></div><div className="distribution-track"><span className={row.label === 'Total ordered' ? 'amber-bar' : ''} style={{ width: `${Math.max(4, row.value / maximum * 100)}%` }} /></div></div>)}</div>
}

export const formatKg = (value: number) => `${value.toLocaleString(undefined, { maximumFractionDigits: 3 })} kg`
