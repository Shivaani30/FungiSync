import { PageIntro } from '../../components/shell/PageIntro'
import { KpiCard } from '../../components/ui/KpiCard'
import { Panel } from '../../components/ui/Panel'
import { StatusBadge } from '../../components/ui/StatusBadge'
import { EmptyState, ErrorState, LoadingState } from '../../components/ui/States'
import { formatKg } from './DistributionBars'
import { useOverviewData, type OverviewResource } from './useOverviewData'

type Destination = 'room' | 'energy' | 'growers-batches' | 'orders' | 'harvest' | 'history' | 'overview'

export function OverviewDashboard({ onNavigate }: { onNavigate: (page: Destination) => void }) {
  const { data, errors, loading, loadingByResource, reload } = useOverviewData()
  const failures = Object.keys(errors) as OverviewResource[]
  const activeGrowers = data.growers.length
  const confirmedKg = data.batches.filter(batch => batch.status === 'confirmed' || batch.status === 'harvested').reduce((sum, batch) => sum + Number(batch.confirmed_kg || 0), 0)
  const pendingOrders = data.orders.filter(order => order.status === 'pending').length
  const recent = [
    ...data.growers.map(item => ({ label: `Grower registered: ${item.name}`, status: 'Registered', date: item.created_at })),
    ...data.batches.map(item => ({ label: `${item.species} batch recorded`, status: item.status, date: item.created_at })),
    ...data.orders.map(item => ({ label: `${item.species} buyer order`, status: item.status, date: item.created_at })),
  ].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 5)

  return <div className="dashboard-grid">
    <PageIntro eyebrow="FungiSync · Jammu & Samba" title="Operational overview">
      <p>Coordinate crop conditions, energy evidence and harvest movement from one regional operations workspace.</p>
    </PageIntro>

    <section className="room-status-banner" aria-label="Simulated grow room status">
      <div><span className="simulation-tag">DEMO SIMULATION</span><h2>Grow room status</h2><p>Illustrative values only · not connected to sensors or farm equipment</p></div>
      <div className="room-status-indicator"><span /> Simulated · monitoring</div>
    </section>

    <div className="kpi-grid room-metrics">
      <KpiCard icon="°" label="Temperature" value="— °C" note="Demo placeholder · no live sensor" />
      <KpiCard icon="%" label="Relative humidity" value="— %" note="Demo placeholder · no live sensor" />
      <KpiCard icon="CO₂" label="Carbon dioxide" value="— ppm" note="Demo placeholder · no live sensor" />
      <KpiCard icon="↗" label="Energy consumption" value="— kWh" note="No meter connected · no savings claim" />
    </div>

    <div className="kpi-grid operations-metrics">
      <KpiCard icon="♧" label="Total growers" value={loadingByResource.growers ? '—' : errors.growers ? 'Unavailable' : activeGrowers} note={errors.growers ? 'Grower records could not be loaded' : 'Live registered grower records'} />
      <KpiCard icon="▤" label="Confirmed harvest" value={loadingByResource.batches ? '—' : errors.batches ? 'Unavailable' : formatKg(confirmedKg)} note={errors.batches ? 'Batch records could not be loaded' : 'Confirmed/harvested batch quantities'} />
      <KpiCard icon="▣" label="Pending buyer orders" value={loadingByResource.orders ? '—' : errors.orders ? 'Unavailable' : pendingOrders} note={errors.orders ? 'Order records could not be loaded' : 'Live orders with pending status'} />
      <KpiCard icon="⇄" label="Allocation summary" value="Not connected" note="Proposal history is not available in this API" />
    </div>

    {failures.length > 0 && <div className="partial-error" role="status">Live record requests unavailable: {failures.join(', ')}. Unavailable values are not replaced with demo records. <button type="button" onClick={() => void reload()}>Retry</button></div>}
    {loading && failures.length === 0 && <LoadingState label="Loading live grower, batch and order records…" />}

    <section className="scenario-section">
      <div className="section-heading"><div><span className="eyebrow">Demonstration areas</span><h2>Explore FungiSync intelligence</h2></div><span className="demo-note">Demo values will be labelled where shown</span></div>
      <div className="scenario-grid">
        <ScenarioCard number="01" title="Grow Room Intelligence" description="Room conditions, crop context and local response demonstration." action="Open grow room" onClick={() => onNavigate('room')} />
        <ScenarioCard number="02" title="Energy Intelligence" description="Metered consumption viewed alongside crop-condition context." action="Open energy" onClick={() => onNavigate('energy')} />
        <ScenarioCard number="03" title="Harvest & Distribution" description="Review supply-to-demand matching results for growers, batches and orders." action="Open matching workspace" onClick={() => onNavigate('harvest')} />
      </div>
    </section>

    <div className="dashboard-columns">
      <Panel title="Recent activity">
        {recent.length ? recent.map((item, index) => <div className="activity-row" key={`${item.label}-${item.date}-${index}`}><div><div className="activity-name">{item.label}</div><div className="activity-date">{Number.isNaN(Date.parse(item.date)) ? 'Date unavailable' : new Date(item.date).toLocaleString()}</div></div><StatusBadge status={item.status} /></div>) : <EmptyState message={loading ? 'Loading activity…' : 'No live activity records are available.'} />}
      </Panel>
      <Panel title="Operational workspace">
        <button className="workspace-link" type="button" onClick={() => onNavigate('growers-batches')}>Manage growers and batches <span>→</span></button>
        <button className="workspace-link" type="button" onClick={() => onNavigate('orders')}>Review buyer orders <span>→</span></button>
        <button className="workspace-link" type="button" onClick={() => onNavigate('history')}>View allocation history <span>→</span></button>
        <p className="chart-note">Live operational records come from the FungiSync API. Room and energy measurements are not connected.</p>
      </Panel>
    </div>
    {Object.values(errors).length === 4 && <ErrorState message="The records API is unavailable. The dashboard keeps live metrics empty and does not substitute synthetic farm data." />}
  </div>
}

function ScenarioCard({ number, title, description, action, onClick }: { number: string; title: string; description: string; action: string; onClick: () => void }) {
  return <article className="scenario-card"><span className="scenario-number">{number}</span><h3>{title}</h3><p>{description}</p><button type="button" onClick={onClick}>{action}<span aria-hidden="true"> →</span></button></article>
}
