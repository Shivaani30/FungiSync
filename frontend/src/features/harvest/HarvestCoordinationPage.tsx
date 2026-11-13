import { useCallback, useEffect, useRef, useState } from 'react'
import { DataTable } from '../../components/ui/DataTable'
import { EmptyState, ErrorState, LoadingState } from '../../components/ui/States'
import { KpiCard } from '../../components/ui/KpiCard'
import { PageIntro } from '../../components/shell/PageIntro'
import { Panel } from '../../components/ui/Panel'
import { StatusBadge } from '../../components/ui/StatusBadge'
import {
  allocationPreview,
  api,
  harvestCoordination,
  type AllocationPreview,
  type HarvestReplanDemo,
  type Order,
} from '../../lib/api'
import { MatchingWorkspace } from '../matching/MatchingWorkspace'

type View = 'replanning' | 'matching' | 'allocation-preview'
type UnknownRecord = Record<string, unknown>
type PreviewState = { orderId: string; requestId: number; data: AllocationPreview }

const isRecord = (value: unknown): value is UnknownRecord =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const isDecimalString = (value: unknown): value is string =>
  typeof value === 'string' && /^\+?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value)

const isNullableDecimal = (value: unknown): value is string | null => value === null || isDecimalString(value)

function isHarvestReplanDemo(value: unknown): value is HarvestReplanDemo {
  if (!isRecord(value) || !isRecord(value.simulated_cancellation) || !isRecord(value.alternate_proposal)) return false
  const cancellation = value.simulated_cancellation
  const proposal = value.alternate_proposal
  const preserved = value.preserved_commitments

  return value.scenario_id === 'synthetic-cancellation-replan-60kg-v1'
    && value.is_synthetic === true
    && isDecimalString(value.source_supply_kg)
    && cancellation.event_type === 'simulated_order_cancellation'
    && typeof cancellation.buyer_label === 'string'
    && isDecimalString(cancellation.quantity_kg)
    && cancellation.actual_order_mutated === false
    && cancellation.reservation_state_changed === false
    && Array.isArray(preserved)
    && preserved.length > 0
    && preserved.every(item => isRecord(item)
      && typeof item.buyer_label === 'string'
      && isDecimalString(item.quantity_kg)
      && item.unchanged_input_fact === true)
    && typeof proposal.buyer_label === 'string'
    && isDecimalString(proposal.quantity_kg)
    && proposal.status === 'PROPOSED'
    && isDecimalString(value.unassigned_kg)
    && value.unassigned_basis === 'synthetic_reconciliation_remainder'
    && isDecimalString(value.reconciled_total_kg)
    && value.is_reconciled === true
    && value.approval_status === 'PENDING'
    && value.reservation_committed === false
    && value.buyer_commitment_created === false
}

function isExclusion(value: unknown): boolean {
  return isRecord(value) && typeof value.code === 'string' && typeof value.message === 'string'
}

function isAllocationPreview(value: unknown): value is AllocationPreview {
  if (!isRecord(value)) return false
  const lineIsValid = (line: unknown) => isRecord(line)
    && typeof line.batch_id === 'string'
    && isNullableDecimal(line.batch_confirmed_kg)
    && isDecimalString(line.active_reserved_kg)
    && isNullableDecimal(line.remaining_before_preview_kg)
    && isDecimalString(line.proposed_quantity_kg)
    && isNullableDecimal(line.remaining_after_preview_kg)
  const excludedBatchIsValid = (batch: unknown) => isRecord(batch)
    && typeof batch.batch_id === 'string'
    && isNullableDecimal(batch.batch_confirmed_kg)
    && isDecimalString(batch.active_reserved_kg)
    && isNullableDecimal(batch.remaining_before_preview_kg)
    && Array.isArray(batch.reasons)
    && batch.reasons.every(isExclusion)
  const timestamp = typeof value.effective_at === 'string' ? Date.parse(value.effective_at) : Number.NaN

  return typeof value.order_id === 'string'
    && value.is_advisory === true
    && value.is_synthetic === false
    && value.persisted === false
    && value.reservation_committed === false
    && value.buyer_commitment_created === false
    && value.quantity_basis === 'confirmed_batch_and_order_quantities_less_active_reservations'
    && value.reservation_accounting_applied === true
    && Number.isFinite(timestamp)
    && isDecimalString(value.total_requested_kg)
    && isDecimalString(value.active_reserved_order_kg)
    && isDecimalString(value.remaining_order_kg)
    && isDecimalString(value.total_allocated_kg)
    && isDecimalString(value.total_unfulfilled_kg)
    && Array.isArray(value.allocation_lines)
    && value.allocation_lines.every(lineIsValid)
    && Array.isArray(value.excluded_batches)
    && value.excluded_batches.every(excludedBatchIsValid)
    && Array.isArray(value.order_exclusions)
    && value.order_exclusions.every(isExclusion)
    && Array.isArray(value.warnings)
    && value.warnings.every(warning => typeof warning === 'string')
    && value.is_reconciled === true
}

function isOrderList(value: unknown): value is Order[] {
  return Array.isArray(value) && value.every(order => isRecord(order)
    && typeof order.id === 'string'
    && typeof order.species === 'string'
    && typeof order.buyer_id === 'string'
    && typeof order.status === 'string')
}

const errorMessage = (reason: unknown) => reason instanceof Error ? reason.message : 'Request failed.'
const displayKg = (value: string | null) => value === null ? 'Not reported' : `${value} kg`

export function HarvestCoordinationPage() {
  const [view, setView] = useState<View>('replanning')
  const [scenario, setScenario] = useState<HarvestReplanDemo | null>(null)
  const [scenarioLoading, setScenarioLoading] = useState(true)
  const [scenarioError, setScenarioError] = useState('')
  const scenarioRequestInFlight = useRef(false)

  const [orders, setOrders] = useState<Order[]>([])
  const [ordersLoading, setOrdersLoading] = useState(false)
  const [ordersError, setOrdersError] = useState('')
  const [ordersLoaded, setOrdersLoaded] = useState(false)
  const ordersRequestInFlight = useRef(false)
  const ordersRequestAttempted = useRef(false)
  const [selectedOrderId, setSelectedOrderId] = useState('')

  const [preview, setPreview] = useState<PreviewState | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState('')
  const [previewRetry, setPreviewRetry] = useState(0)
  const previewRequestSequence = useRef(0)

  const loadScenario = useCallback(async () => {
    if (scenarioRequestInFlight.current) return
    scenarioRequestInFlight.current = true
    setScenarioLoading(true)
    setScenarioError('')
    setScenario(null)
    try {
      const result: unknown = await harvestCoordination.demoReplan()
      if (!isHarvestReplanDemo(result)) throw new Error('The harvest scenario API returned an invalid response.')
      setScenario(result)
    } catch (reason) {
      setScenarioError(errorMessage(reason))
    } finally {
      scenarioRequestInFlight.current = false
      setScenarioLoading(false)
    }
  }, [])

  const loadOrders = useCallback(async (force = false) => {
    if (ordersRequestInFlight.current || (!force && ordersRequestAttempted.current)) return
    ordersRequestInFlight.current = true
    ordersRequestAttempted.current = true
    setOrdersLoading(true)
    setOrdersError('')
    try {
      const result: unknown = await api.orders.list()
      if (!isOrderList(result)) throw new Error('The orders API returned an invalid response.')
      setOrders(result)
      setOrdersLoaded(true)
      setSelectedOrderId(current => result.some(order => order.id === current) ? current : '')
    } catch (reason) {
      setOrdersError(`Operational orders could not be loaded: ${errorMessage(reason)}`)
      setOrdersLoaded(false)
    } finally {
      ordersRequestInFlight.current = false
      setOrdersLoading(false)
    }
  }, [])

  const selectOrder = (orderId: string) => {
    previewRequestSequence.current += 1
    setPreview(null)
    setPreviewError('')
    setPreviewLoading(Boolean(orderId))
    setSelectedOrderId(orderId)
  }

  const selectView = (nextView: View) => {
    if (nextView !== view) {
      previewRequestSequence.current += 1
      setPreview(null)
      setPreviewError('')
      setPreviewLoading(false)
      setView(nextView)
    }
  }

  useEffect(() => {
    if (view === 'replanning') void loadScenario()
  }, [view, loadScenario])

  useEffect(() => {
    if (view === 'allocation-preview' && !ordersRequestAttempted.current) void loadOrders()
  }, [view, loadOrders])

  useEffect(() => {
    if (view !== 'allocation-preview' || !selectedOrderId) {
      setPreview(null)
      setPreviewError('')
      setPreviewLoading(false)
      return
    }

    const requestId = ++previewRequestSequence.current
    let currentRequest = true
    setPreview(null)
    setPreviewError('')
    setPreviewLoading(true)
    void allocationPreview.forOrder(selectedOrderId).then((result: unknown) => {
      if (!isAllocationPreview(result) || result.order_id !== selectedOrderId) {
        throw new Error('The allocation preview API returned an invalid or mismatched response.')
      }
      if (currentRequest && previewRequestSequence.current === requestId) {
        setPreview({ orderId: selectedOrderId, requestId, data: result })
      }
    }).catch(reason => {
      if (currentRequest && previewRequestSequence.current === requestId) {
        setPreviewError(`The operational allocation preview could not be loaded: ${errorMessage(reason)}`)
      }
    }).finally(() => {
      if (currentRequest && previewRequestSequence.current === requestId) setPreviewLoading(false)
    })

    return () => { currentRequest = false }
  }, [view, selectedOrderId, previewRetry])

  const visiblePreview = preview
    && preview.orderId === selectedOrderId
    && preview.requestId === previewRequestSequence.current
    ? preview.data
    : null

  return <div className="harvest-workspace">
    <PageIntro eyebrow="Harvest & distribution intelligence" title="Harvest coordination">
      <p>Review the synthetic replanning demonstration, an operational advisory preview, or the existing matching workspace.</p>
    </PageIntro>

    <div className="harvest-view-tabs" role="tablist" aria-label="Harvest coordination views">
      <button type="button" role="tab" aria-selected={view === 'replanning'} className={view === 'replanning' ? 'active' : ''} onClick={() => selectView('replanning')}>Replanning Demo</button>
      <button type="button" role="tab" aria-selected={view === 'allocation-preview'} className={view === 'allocation-preview' ? 'active' : ''} onClick={() => selectView('allocation-preview')}>Allocation Preview</button>
      <button type="button" role="tab" aria-selected={view === 'matching'} className={view === 'matching' ? 'active' : ''} onClick={() => selectView('matching')}>Matching Workspace</button>
    </div>

    {view === 'matching' ? <MatchingWorkspace /> : view === 'replanning' ? <section className="harvest-demo-view" role="tabpanel">
      <div className="harvest-demo-banner">
        <StatusBadge status="DEMO SIMULATION" />
        <p>Illustrative synthetic values only. No operational order, inventory, reservation, or buyer commitment is changed.</p>
      </div>
      {scenarioLoading && <LoadingState label="Loading synthetic harvest scenario…" />}
      {!scenarioLoading && scenarioError && <ErrorState message={`The replanning scenario could not be loaded: ${scenarioError}`} onRetry={() => void loadScenario()} />}
      {!scenarioLoading && !scenarioError && scenario && <ScenarioDetails scenario={scenario} />}
    </section> : <AllocationPreviewView
      orders={orders}
      ordersLoading={ordersLoading}
      ordersError={ordersError}
      ordersLoaded={ordersLoaded}
      onRetryOrders={() => void loadOrders(true)}
      selectedOrderId={selectedOrderId}
      onSelectOrder={selectOrder}
      preview={visiblePreview}
      previewLoading={previewLoading}
      previewError={previewError}
      onRetryPreview={() => setPreviewRetry(retry => retry + 1)}
    />}
  </div>
}

function AllocationPreviewView({
  orders, ordersLoading, ordersError, ordersLoaded, onRetryOrders, selectedOrderId, onSelectOrder,
  preview, previewLoading, previewError, onRetryPreview,
}: {
  orders: Order[]; ordersLoading: boolean; ordersError: string; ordersLoaded: boolean; onRetryOrders: () => void
  selectedOrderId: string; onSelectOrder: (id: string) => void; preview: AllocationPreview | null
  previewLoading: boolean; previewError: string; onRetryPreview: () => void
}) {
  return <section className="harvest-demo-view allocation-preview-view" role="tabpanel">
    <div className="allocation-preview-banner">
      <StatusBadge status="OPERATIONAL DATA · ADVISORY ONLY" />
      <p>This is a read-only point-in-time calculation, not a saved allocation. It creates no reservation, buyer commitment, or dispatch. Capacity may change after the snapshot.</p>
    </div>

    <Panel title="Select an order">
      {ordersLoading && <LoadingState label="Loading operational orders…" />}
      {!ordersLoading && ordersError && <ErrorState message={ordersError} onRetry={onRetryOrders} />}
      {!ordersLoading && !ordersError && ordersLoaded && orders.length === 0 && <EmptyState message="No operational orders are available for preview." />}
      {!ordersLoading && !ordersError && ordersLoaded && orders.length > 0 && <label className="allocation-order-select">
        <span>Operational order</span>
        <select aria-label="Select an order for allocation preview" value={selectedOrderId} onChange={event => onSelectOrder(event.target.value)}>
          <option value="">Choose an order</option>
          {orders.map(order => <option value={order.id} key={order.id}>{order.species} · {order.status} · {order.id}</option>)}
        </select>
      </label>}
    </Panel>

    {!ordersLoading && !ordersError && ordersLoaded && orders.length > 0 && !selectedOrderId && <EmptyState message="Select an order to request its operational allocation preview." />}
    {selectedOrderId && previewLoading && <LoadingState label="Loading read-only allocation preview…" />}
    {selectedOrderId && !previewLoading && previewError && <ErrorState message={previewError} onRetry={onRetryPreview} />}
    {selectedOrderId && !previewLoading && !previewError && preview && <AllocationPreviewDetails preview={preview} />}
  </section>
}

function AllocationPreviewDetails({ preview }: { preview: AllocationPreview }) {
  const snapshotTime = new Date(preview.effective_at).toLocaleString()
  return <div className="harvest-scenario-content">
    <div className="harvest-scenario-heading">
      <div><span className="eyebrow">Operational record · not synthetic</span><h3>Advisory allocation preview</h3></div>
      <div className="allocation-preview-identifiers"><code>Order ID: {preview.order_id}</code><StatusBadge status="NOT PERSISTED" /></div>
    </div>
    <p className="allocation-snapshot-note">Database snapshot: <strong>{snapshotTime}</strong>. This result reflects the selected order and confirmed batch quantities less active reservations at that time; capacity may change afterward.</p>

    <div className="kpi-grid harvest-kpis allocation-preview-kpis">
      <KpiCard icon="↗" label="Order requested" value={displayKg(preview.total_requested_kg)} note="Original order quantity" />
      <KpiCard icon="−" label="Active reserved for order" value={displayKg(preview.active_reserved_order_kg)} note="Deducted by backend accounting" />
      <KpiCard icon="✓" label="Allocated in preview" value={displayKg(preview.total_allocated_kg)} note="Advisory calculation only" />
      <KpiCard icon="…" label="Unfulfilled" value={displayKg(preview.total_unfulfilled_kg)} note="Remaining order demand" />
    </div>

    <Panel title="Quantity accounting">
      <div className="harvest-fact"><span>Remaining order quantity</span><strong>{displayKg(preview.remaining_order_kg)}</strong></div>
      <div className="harvest-fact"><span>Accounting basis</span><strong>Confirmed quantities less active reservations</strong></div>
      <div className="harvest-fact"><span>Reservation accounting applied</span><StatusBadge status={String(preview.reservation_accounting_applied)} /></div>
      <div className="harvest-fact"><span>Backend reconciliation</span><StatusBadge status={String(preview.is_reconciled)} /></div>
      <p className="harvest-safety-note">No reservation is created, no buyer commitment is created, and no dispatch is initiated by this preview.</p>
    </Panel>

    <Panel title="Proposed batch allocation">
      {preview.allocation_lines.length === 0 ? <EmptyState message="The backend returned no proposed allocation lines for this order." /> : <DataTable headers={['Batch ID', 'Confirmed quantity', 'Active reserved', 'Remaining before', 'Proposed allocation', 'Remaining after']}>
        {preview.allocation_lines.map(line => <tr key={line.batch_id}>
          <td>{line.batch_id}</td><td>{displayKg(line.batch_confirmed_kg)}</td><td>{displayKg(line.active_reserved_kg)}</td>
          <td>{displayKg(line.remaining_before_preview_kg)}</td><td><strong>{displayKg(line.proposed_quantity_kg)}</strong></td>
          <td>{displayKg(line.remaining_after_preview_kg)}</td>
        </tr>)}
      </DataTable>}
    </Panel>

    {preview.excluded_batches.length > 0 && <Panel title="Excluded batches">
      <DataTable headers={['Batch ID', 'Confirmed quantity', 'Active reserved', 'Remaining before', 'Exclusion reasons']}>
        {preview.excluded_batches.map(batch => <tr key={batch.batch_id}>
          <td>{batch.batch_id}</td><td>{displayKg(batch.batch_confirmed_kg)}</td><td>{displayKg(batch.active_reserved_kg)}</td>
          <td>{displayKg(batch.remaining_before_preview_kg)}</td><td>{batch.reasons.map(reason => <div key={`${reason.code}-${reason.message}`}><StatusBadge status={reason.code} /> <span>{reason.message}</span></div>)}</td>
        </tr>)}
      </DataTable>
    </Panel>}

    {preview.order_exclusions.length > 0 && <Panel title="Order exclusions"><ul className="allocation-message-list">
      {preview.order_exclusions.map((reason, index) => <li key={`${reason.code}-${index}`}><StatusBadge status={reason.code} /> {reason.message}</li>)}
    </ul></Panel>}
    {preview.warnings.length > 0 && <Panel title="Backend warnings"><ul className="allocation-message-list">
      {preview.warnings.map((warning, index) => <li key={`${index}-${warning}`}>{warning}</li>)}
    </ul></Panel>}

    <Panel title="Non-commitment status">
      <div className="harvest-reconciliation-status allocation-safety-status">
        <div className="harvest-fact"><span>Synthetic data</span><StatusBadge status={preview.is_synthetic ? 'Yes' : 'No'} /></div>
        <div className="harvest-fact"><span>Advisory calculation</span><StatusBadge status={String(preview.is_advisory)} /></div>
        <div className="harvest-fact"><span>Persisted</span><StatusBadge status={String(preview.persisted)} /></div>
        <div className="harvest-fact"><span>Reservation committed</span><StatusBadge status={String(preview.reservation_committed)} /></div>
        <div className="harvest-fact"><span>Buyer commitment created</span><StatusBadge status={String(preview.buyer_commitment_created)} /></div>
      </div>
    </Panel>
  </div>
}

function ScenarioDetails({ scenario }: { scenario: HarvestReplanDemo }) {
  const reconciliationParts = scenario.preserved_commitments.map(commitment => `${commitment.quantity_kg} kg ${commitment.buyer_label}`)
  reconciliationParts.push(`${scenario.alternate_proposal.quantity_kg} kg proposed to ${scenario.alternate_proposal.buyer_label}`)
  reconciliationParts.push(`${scenario.unassigned_kg} kg unassigned`)

  return <div className="harvest-scenario-content">
    <div className="harvest-scenario-heading">
      <div><span className="eyebrow">Synthetic scenario</span><h3>Cancellation replan demonstration</h3></div>
      <code>{scenario.scenario_id}</code>
    </div>

    <div className="kpi-grid harvest-kpis">
      <KpiCard icon="Σ" label="Illustrative supply" value={`${scenario.source_supply_kg} kg`} note="Synthetic scenario input; not confirmed stock" />
      <KpiCard icon="↻" label="Simulated cancellation" value={`${scenario.simulated_cancellation.quantity_kg} kg`} note={scenario.simulated_cancellation.buyer_label} />
      <KpiCard icon="+" label="Alternate proposal" value={`${scenario.alternate_proposal.quantity_kg} kg`} note={`${scenario.alternate_proposal.buyer_label} · proposed only`} />
      <KpiCard icon="…" label="Unassigned remainder" value={`${scenario.unassigned_kg} kg`} note="Not confirmed available stock" />
    </div>

    <div className="harvest-detail-grid">
      <Panel title="Simulated cancellation">
        <div className="harvest-fact"><span>Buyer</span><strong>{scenario.simulated_cancellation.buyer_label}</strong></div>
        <div className="harvest-fact"><span>Cancelled quantity</span><strong>{scenario.simulated_cancellation.quantity_kg} kg</strong></div>
        <div className="harvest-fact"><span>Event</span><strong>{scenario.simulated_cancellation.event_type}</strong></div>
        <p className="harvest-safety-note">This is a scenario event only; no actual order was modified.</p>
        <div className="harvest-boolean"><span>Actual order mutated</span><code>{String(scenario.simulated_cancellation.actual_order_mutated)}</code></div>
        <div className="harvest-boolean"><span>Reservation state changed</span><code>{String(scenario.simulated_cancellation.reservation_state_changed)}</code></div>
      </Panel>

      <Panel title="Preserved commitment">
        {scenario.preserved_commitments.map((commitment, index) => <div className="harvest-record" key={`${commitment.buyer_label}-${index}`}>
          <div className="harvest-fact"><span>Buyer</span><strong>{commitment.buyer_label}</strong></div>
          <div className="harvest-fact"><span>Quantity</span><strong>{commitment.quantity_kg} kg</strong></div>
          <StatusBadge status={commitment.unchanged_input_fact ? 'Unchanged input fact' : 'Changed'} />
        </div>)}
      </Panel>

      <Panel title="Alternate buyer proposal">
        <div className="harvest-fact"><span>Buyer</span><strong>{scenario.alternate_proposal.buyer_label}</strong></div>
        <div className="harvest-fact"><span>Proposed quantity</span><strong>{scenario.alternate_proposal.quantity_kg} kg</strong></div>
        <div className="harvest-proposal-status"><StatusBadge status={scenario.alternate_proposal.status} /></div>
        <p className="harvest-safety-note">This is a proposal, not a confirmed order or sale commitment.</p>
      </Panel>

      <Panel title="Unassigned remainder">
        <div className="harvest-fact"><span>Quantity</span><strong>{scenario.unassigned_kg} kg</strong></div>
        <div className="harvest-fact"><span>Basis</span><strong>{scenario.unassigned_basis.split('_').join(' ')}</strong></div>
        <p className="harvest-safety-note">This illustrative remainder is not confirmed available stock.</p>
      </Panel>
    </div>

    <Panel title="Reconciliation">
      <p className="harvest-equation">{scenario.source_supply_kg} kg = {reconciliationParts.join(' + ')}</p>
      <div className="harvest-reconciliation-status">
        <div className="harvest-fact"><span>Reconciled total</span><strong>{scenario.reconciled_total_kg} kg</strong></div>
        <div className="harvest-fact"><span>Backend reconciliation</span><StatusBadge status={scenario.is_reconciled ? 'Reconciled' : 'Not reconciled'} /></div>
      </div>
    </Panel>

    <Panel title="Approval and commitment status">
      <div className="harvest-reconciliation-status">
        <div className="harvest-fact"><span>Approval status</span><StatusBadge status={scenario.approval_status} /></div>
        <div className="harvest-fact harvest-safety-value"><span>Reservation committed</span><code>{String(scenario.reservation_committed)}</code></div>
        <div className="harvest-fact harvest-safety-value"><span>Buyer commitment created</span><code>{String(scenario.buyer_commitment_created)}</code></div>
      </div>
      <p className="harvest-safety-note">No real reservation or buyer commitment has been created.</p>
    </Panel>
  </div>
}
