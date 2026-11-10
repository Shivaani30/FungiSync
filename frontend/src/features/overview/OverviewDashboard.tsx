import { PageIntro } from '../../components/shell/PageIntro'
import { KpiCard } from '../../components/ui/KpiCard'
import { Panel } from '../../components/ui/Panel'
import { StatusBadge } from '../../components/ui/StatusBadge'
import { EmptyState, ErrorState, LoadingState } from '../../components/ui/States'
import { DistributionBars, QuantityComparison, formatKg, type DistributionEntry } from './DistributionBars'
import { useOverviewData, type OverviewResource } from './useOverviewData'

const countBy = <T,>(items: T[], key: (item: T) => string): DistributionEntry[] => {
  const counts = new Map<string, number>()
  items.forEach(item => counts.set(key(item), (counts.get(key(item)) ?? 0) + 1))
  return [...counts.entries()].map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value || a.label.localeCompare(b.label))
}

const quantityBy = <T,>(items: T[], key: (item: T) => string, quantity: (item: T) => string): DistributionEntry[] => {
  const values = new Map<string, number>()
  items.forEach(item => values.set(key(item), (values.get(key(item)) ?? 0) + Number(quantity(item) || 0)))
  return [...values.entries()].map(([label, value]) => ({ label, value, detail: formatKg(value) })).sort((a, b) => b.value - a.value || a.label.localeCompare(b.label))
}

function UnavailablePanel({ resource, error, onRetry }: { resource: string; error: string; onRetry: () => void }) {
  return <Panel title={`${resource} unavailable`}><ErrorState message={`The ${resource.toLowerCase()} request failed: ${error}`} /><button className="retry-button" type="button" onClick={onRetry}>Retry live data</button></Panel>
}

export function OverviewDashboard() {
  const { data, errors, loading, reload, allUnavailable } = useOverviewData()
  const failedResources = Object.keys(errors) as OverviewResource[]
  if (loading && !failedResources.length) return <><OverviewIntro connected={false} /><LoadingState label="Loading live overview data…" /></>
  if (allUnavailable) return <><OverviewIntro connected={false} /><ErrorState message="The FungiSync API is unavailable. Confirm the backend is running, then retry." /><div className="retry-wrap"><button className="retry-button" type="button" onClick={reload}>Retry connection</button></div></>

  const confirmedHarvest = data.batches.filter(batch => batch.status === 'confirmed' || batch.status === 'harvested').reduce((total, batch) => total + Number(batch.confirmed_kg || 0), 0)
  const expectedHarvest = data.batches.reduce((total, batch) => total + Number(batch.expected_kg || 0), 0)
  const totalOrdered = data.orders.reduce((total, order) => total + Number(order.quantity_kg || 0), 0)
  const recent = [
    ...data.growers.map(grower => ({ name: grower.name, type: 'Grower', status: 'Registered', date: grower.created_at })),
    ...data.batches.map(batch => ({ name: `${batch.species} harvest batch`, type: 'Harvest batch', status: batch.status, date: batch.created_at })),
    ...data.orders.map(order => ({ name: `${order.species} order`, type: 'Buyer order', status: order.status, date: order.created_at })),
  ].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 6)

  return <div className="dashboard-grid"><OverviewIntro connected />
    {failedResources.length > 0 && <div className="partial-error" role="status">Some live data could not be loaded: {failedResources.join(', ')}. Values for those resources are unavailable. <button type="button" onClick={reload}>Retry</button></div>}
    <div className="kpi-grid"><KpiCard icon="♧" label="Total growers" value={errors.growers ? '—' : data.growers.length} note={errors.growers ? 'Unavailable from /growers' : 'Live records from /growers'} /><KpiCard icon="◫" label="Total harvest batches" value={errors.batches ? '—' : data.batches.length} note={errors.batches ? 'Unavailable from /batches' : 'Live records from /batches'} /><KpiCard icon="♙" label="Total buyers" value={errors.buyers ? '—' : data.buyers.length} note={errors.buyers ? 'Unavailable from /buyers' : 'Live records from /buyers'} /><KpiCard icon="□" label="Total buyer orders" value={errors.orders ? '—' : data.orders.length} note={errors.orders ? 'Unavailable from /orders' : 'Live records from /orders'} /></div>
    <div className="dashboard-columns">{errors.batches ? <UnavailablePanel resource="Harvest batches" error={errors.batches} onRetry={reload} /> : <Panel title="Supply overview"><div className="metric-pair"><div><span>Confirmed harvest</span><strong>{formatKg(confirmedHarvest)}</strong><small>Confirmed or harvested batches only</small></div><div><span>Expected harvest</span><strong>{formatKg(expectedHarvest)}</strong><small>All registered batch forecasts</small></div></div><h4>Batch status distribution</h4><DistributionBars entries={countBy(data.batches, batch => batch.status)} valueLabel="batch statuses" /></Panel>}{errors.orders ? <UnavailablePanel resource="Buyer orders" error={errors.orders} onRetry={reload} /> : <Panel title="Demand overview"><div className="metric-pair"><div><span>Total ordered quantity</span><strong>{formatKg(totalOrdered)}</strong><small>All order records; not a fulfilment measure</small></div></div><h4>Order status distribution</h4><DistributionBars entries={countBy(data.orders, order => order.status)} valueLabel="order statuses" /></Panel>}</div>
    <div className="dashboard-columns"><Panel title="Harvest and order quantities">{errors.batches || errors.orders ? <EmptyState message="Both batch and order data are required for this comparison." /> : <><QuantityComparison supply={confirmedHarvest} demand={totalOrdered} /><p className="chart-note">This compares recorded quantities only. No stock reservation, allocation, or fulfilment rate is available.</p></>}</Panel><Panel title="Species demand">{errors.orders ? <EmptyState message="Order data is unavailable." /> : <DistributionBars entries={quantityBy(data.orders, order => order.species, order => order.quantity_kg)} valueLabel="species demand" />}</Panel></div>
    <div className="dashboard-columns"><Panel title="Confirmed harvest by species">{errors.batches ? <EmptyState message="Batch data is unavailable." /> : <DistributionBars entries={quantityBy(data.batches.filter(batch => batch.status === 'confirmed' || batch.status === 'harvested'), batch => batch.species, batch => batch.confirmed_kg ?? '0')} valueLabel="confirmed harvest quantities" />}</Panel><Panel title="Recent records">{recent.length ? recent.map(item => <div className="activity-row" key={`${item.type}-${item.name}-${item.date}`}><div><div className="activity-name">{item.name}</div><div className="muted">{item.type}</div></div><div><StatusBadge status={item.status} /><div className="activity-date">{new Date(item.date).toLocaleDateString()}</div></div></div>) : <EmptyState message="No growers, harvest batches, or orders have been created yet." />}</Panel></div>
  </div>
}

function OverviewIntro({ connected }: { connected: boolean }) {
  return <PageIntro eyebrow="Distribution overview" title="Your connected workspace" actions={<div className={`dashboard-connection ${connected ? 'connected' : ''}`}><span aria-hidden="true" />{connected ? 'Live API records' : 'Connecting to API'}</div>}><p>Review registered supply and buyer demand from the FungiSync API. Matching remains read-only and does not reserve inventory or fulfil orders.</p></PageIntro>
}
