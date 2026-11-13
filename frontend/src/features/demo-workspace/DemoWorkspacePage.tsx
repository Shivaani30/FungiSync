import { useMemo, useState } from 'react'
import { PageIntro } from '../../components/shell/PageIntro'
import { DataTable } from '../../components/ui/DataTable'
import { EmptyState } from '../../components/ui/States'
import { Panel } from '../../components/ui/Panel'
import { StatusBadge } from '../../components/ui/StatusBadge'
import { buildDemoAllocationPreview, type DemoAllocationPreview } from './demoAllocation'
import { demoBatches, demoBuyers, demoGrowers, demoOrders } from './demoData'

type Stage = 'growers' | 'batches' | 'orders' | 'matching'

const stages: { id: Stage; label: string }[] = [
  { id: 'growers', label: '1. Growers' },
  { id: 'batches', label: '2. Batches' },
  { id: 'orders', label: '3. Buyers & Orders' },
  { id: 'matching', label: '4. Matching & Allocation' },
]

export function DemoWorkspacePage() {
  const [stage, setStage] = useState<Stage>('growers')
  const [growerFilter, setGrowerFilter] = useState('all')
  const [selectedBatchId, setSelectedBatchId] = useState('')
  const [selectedOrderId, setSelectedOrderId] = useState('')
  const [preview, setPreview] = useState<{ orderId: string; result: DemoAllocationPreview } | null>(null)

  const growerById = useMemo(() => new Map(demoGrowers.map(grower => [grower.id, grower])), [])
  const buyerById = useMemo(() => new Map(demoBuyers.map(buyer => [buyer.id, buyer])), [])
  const visibleBatches = growerFilter === 'all' ? demoBatches : demoBatches.filter(batch => batch.growerId === growerFilter)
  const selectedOrder = demoOrders.find(order => order.id === selectedOrderId)
  const currentPreview = preview?.orderId === selectedOrderId ? preview.result : null

  function generatePreview() {
    if (!selectedOrder) return
    setPreview({ orderId: selectedOrder.id, result: buildDemoAllocationPreview(selectedOrder, demoBatches) })
  }

  function resetWorkspace() {
    if (!window.confirm('Reset this synthetic demo workspace to its starting view? No operational records will be changed.')) return
    setStage('growers')
    setGrowerFilter('all')
    setSelectedBatchId('')
    setSelectedOrderId('')
    setPreview(null)
  }

  return <div className="demo-workspace">
    <PageIntro eyebrow="Isolated presenter workspace" title="Demo Workspace">
      <p>Walk through a small, deterministic example of grower supply and buyer demand. All records and calculations on this page are local synthetic data.</p>
    </PageIntro>

    <div className="demo-workspace-banner" role="note" aria-label="Synthetic data notice">
      <strong>SYNTHETIC DEMO DATA — NOT LIVE OPERATIONAL RECORDS</strong>
      <span>This workspace does not read from or write to the operational API. No reservation, buyer commitment, approval, or dispatch is created.</span>
    </div>

    <div className="demo-workspace-toolbar">
      <div className="demo-workspace-legend" aria-label="Data labels">
        <span className="guided-label synthetic">Synthetic local records</span>
        <span className="guided-label advisory">Demo-only advisory calculation</span>
      </div>
      <button type="button" className="refresh-button" onClick={resetWorkspace}>Reset demo workspace</button>
    </div>

    <nav className="demo-stage-tabs" aria-label="Demo workflow stages" role="tablist">
      {stages.map(item => <button key={item.id} type="button" role="tab" aria-selected={stage === item.id} className={stage === item.id ? 'active' : ''} onClick={() => setStage(item.id)}>{item.label}</button>)}
    </nav>

    {stage === 'growers' && <Panel title="Synthetic grower overview">
      <p className="demo-workspace-copy">Fictional grower profiles and regional locations are included only to make the demonstration journey understandable.</p>
      <DataTable headers={['Synthetic grower', 'Location', 'Demo batches']}>
        {demoGrowers.map(grower => <tr key={grower.id}>
          <td><strong>{grower.name}</strong><small className="demo-secondary">{grower.id}</small></td>
          <td>{grower.location}</td>
          <td>{demoBatches.filter(batch => batch.growerId === grower.id).length}</td>
        </tr>)}
      </DataTable>
    </Panel>}

    {stage === 'batches' && <Panel title="Synthetic batches and harvest quantities">
      <div className="demo-workspace-controls">
        <label className="demo-workspace-select">Filter by synthetic grower
          <select value={growerFilter} onChange={event => { setGrowerFilter(event.target.value); setSelectedBatchId('') }}>
            <option value="all">All demo growers</option>
            {demoGrowers.map(grower => <option key={grower.id} value={grower.id}>{grower.name}</option>)}
          </select>
        </label>
      </div>
      <p className="demo-workspace-copy">Expected harvest, grower-confirmed fixture quantities, and illustrative demo quantities are separate fields. The illustrative quantity is not live stock and has no reservation accounting.</p>
      {visibleBatches.length ? <DataTable headers={['Batch / grower', 'Species', 'Expected kg', 'Confirmed kg', 'Illustrative demo kg', 'Harvest date', 'Status']}>
        {visibleBatches.map(batch => <tr key={batch.id} className={selectedBatchId === batch.id ? 'demo-selected-row' : ''}>
          <td><button type="button" className="demo-row-select" aria-pressed={selectedBatchId === batch.id} onClick={() => setSelectedBatchId(selectedBatchId === batch.id ? '' : batch.id)}>{batch.id}</button><small className="demo-secondary">{growerById.get(batch.growerId)?.name ?? 'Unknown demo grower'}</small></td>
          <td>{batch.species}</td><td>{batch.expectedKg}</td><td>{batch.confirmedKg ?? 'Not confirmed'}</td><td>{batch.demoAvailableKg ?? 'Not listed'}</td><td>{batch.harvestDate}</td><td><StatusBadge status={batch.status} /></td>
        </tr>)}
      </DataTable> : <EmptyState message="No synthetic batches are associated with this grower." />}
      <p className="demo-workspace-footnote">Selecting a row only highlights it. It does not edit or update a batch.</p>
    </Panel>}

    {stage === 'orders' && <Panel title="Synthetic buyers and orders">
      <p className="demo-workspace-copy">Buyer profiles and order quantities are fictional fixture records. Their displayed statuses do not represent real commercial commitments.</p>
      <div className="demo-workspace-order-grid">
        {demoBuyers.map(buyer => <article className="demo-workspace-buyer" key={buyer.id}>
          <span className="guided-label synthetic">Synthetic buyer</span><h4>{buyer.name}</h4><p>{buyer.location}</p>
          {demoOrders.filter(order => order.buyerId === buyer.id).map(order => <div className="demo-order-fact" key={order.id}>
            <strong>{order.id}</strong><span>{order.species}</span><span>{order.quantityKg} kg · window {order.deliveryWindow}</span><StatusBadge status={order.status} />
          </div>)}
        </article>)}
      </div>
    </Panel>}

    {stage === 'matching' && <div className="demo-workspace-matching">
      <Panel title="Demo-only matching and allocation illustration">
        <p className="demo-workspace-copy">This transparent illustration pairs same-species orders with confirmed synthetic batch quantities in harvest-date order. Each order is calculated independently; the same batch can appear in another order's preview.</p>
        <p className="demo-workspace-limitation">It does not use the production matching or allocation engine, model reservations or cross-order consumption, or validate commercial, quality, price, transport, or agronomic rules. The result is not persisted and does not reserve or guarantee stock.</p>
        <label className="demo-workspace-select">Synthetic order
          <select value={selectedOrderId} onChange={event => { setSelectedOrderId(event.target.value); setPreview(null) }}>
            <option value="">Select a demo order</option>
            {demoOrders.map(order => <option key={order.id} value={order.id}>{buyerById.get(order.buyerId)?.name} · {order.species} · {order.quantityKg} kg</option>)}
          </select>
        </label>
        {selectedOrder && <div className="demo-selected-order"><span>Buyer: <strong>{buyerById.get(selectedOrder.buyerId)?.name}</strong></span><span>Requested: <strong>{selectedOrder.quantityKg} kg</strong></span><span>Status: <StatusBadge status={selectedOrder.status} /></span></div>}
        <button type="button" className="primary" disabled={!selectedOrder} onClick={generatePreview}>Generate demo advisory preview</button>
      </Panel>

      {currentPreview && selectedOrder && <>
        <Panel title="Compatible synthetic batches">
          {currentPreview.candidates.length ? <DataTable headers={['Batch', 'Grower', 'Illustrative quantity', 'Demo match basis']}>
            {currentPreview.candidates.map(candidate => {
              const batch = demoBatches.find(item => item.id === candidate.batchId)
              return batch ? <tr key={candidate.batchId}><td>{batch.id}</td><td>{growerById.get(batch.growerId)?.name}</td><td>{batch.demoAvailableKg} kg</td><td>{candidate.reason}</td></tr> : null
            })}
          </DataTable> : <EmptyState message="No compatible confirmed synthetic batches for this demo order." />}
          {currentPreview.excluded.length > 0 && <div className="demo-exclusions"><h4>Other batches excluded from this illustration</h4><ul>{currentPreview.excluded.map(item => <li key={item.batchId}><strong>{item.batchId}</strong> — {item.reason}</li>)}</ul></div>}
        </Panel>

        <Panel title="Non-persisted allocation illustration">
          <div className="demo-allocation-summary"><div><span>Illustrative order request</span><strong>{selectedOrder.quantityKg} kg</strong></div><div><span>Illustratively allocated</span><strong>{currentPreview.allocatedKg} kg</strong></div><div><span>Unfulfilled in this demo calculation</span><strong>{currentPreview.unfulfilledKg} kg</strong></div></div>
          {currentPreview.lines.length ? <DataTable headers={['Synthetic batch', 'Illustrative quantity']}>
            {currentPreview.lines.map(line => <tr key={line.batchId}><td>{line.batchId}</td><td>{line.quantityKg} kg</td></tr>)}
          </DataTable> : <EmptyState message="This demo calculation produced no allocation lines." />}
          <p className="demo-workspace-footnote">These values come from a deterministic local illustration. No operational API was called and no allocation, reservation, buyer commitment, approval, or dispatch record was created.</p>
        </Panel>
      </>}
    </div>}
  </div>
}
