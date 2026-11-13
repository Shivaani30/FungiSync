import { PageIntro } from '../../components/shell/PageIntro'
import { Panel } from '../../components/ui/Panel'

type DemoDestination = 'overview' | 'growers-batches' | 'orders' | 'harvest' | 'demo-workspace' | 'room' | 'energy' | 'history'

const steps = [
  { title: 'FungiSync overview', duration: '0:45', type: 'Product overview' },
  { title: 'Growers, batches & orders', duration: '1:30', type: 'Operational API-backed' },
  { title: 'Matching & allocation', duration: '2:00', type: 'Read-only advisory' },
  { title: 'Harvest disruption & replanning', duration: '1:30', type: 'Synthetic demonstration' },
  { title: 'Future capabilities & close', duration: '0:45', type: 'Future previews' },
] as const

export function GuidedDemoPage({ activeStep, onStepChange, onNavigate }: {
  activeStep: number
  onStepChange: (step: number) => void
  onNavigate: (destination: DemoDestination) => void
}) {
  const step = steps[activeStep]

  return <div className="guided-demo-page">
    <PageIntro eyebrow="Presenter mode · about 6½ minutes" title="Guided FungiSync demo">
      <p>A short route through the current product, with operational records, advisory results, synthetic scenarios, and future previews clearly separated.</p>
    </PageIntro>

    <div className="guided-demo-notice" role="note">
      <strong>GUIDED WALKTHROUGH</strong>
      <span>This page only navigates and explains. It does not create or change records, allocations, reservations, or commitments.</span>
    </div>

    <div className="guided-demo-legend" aria-label="Capability labels">
      <span className="guided-label operational">Operational API-backed</span>
      <span className="guided-label advisory">Read-only advisory</span>
      <span className="guided-label synthetic">Synthetic demonstration</span>
      <span className="guided-label future">Future preview</span>
    </div>

    <nav className="guided-step-list" aria-label="Demo steps">
      {steps.map((item, index) => <button
        key={item.title}
        type="button"
        className={`guided-step-button${activeStep === index ? ' active' : ''}`}
        aria-current={activeStep === index ? 'step' : undefined}
        onClick={() => onStepChange(index)}
      >
        <span className="guided-step-number">{String(index + 1).padStart(2, '0')}</span>
        <span className="guided-step-copy"><strong>{item.title}</strong><small>{item.duration} · {item.type}</small></span>
      </button>)}</nav>

    <Panel title={`Step ${activeStep + 1} · ${step.title}`}>
      <div className="guided-step-content">
        <div className="guided-step-heading">
          <span className={`guided-label ${activeStep === 3 ? 'synthetic' : activeStep === 2 ? 'advisory' : activeStep === 4 ? 'future' : 'operational'}`}>{step.type}</span>
          <span className="guided-duration">Presenter time · {step.duration}</span>
        </div>
        {activeStep === 0 && <OverviewStep onNavigate={onNavigate} />}
        {activeStep === 1 && <OperationalRecordsStep onNavigate={onNavigate} />}
        {activeStep === 2 && <MatchingStep onNavigate={onNavigate} />}
        {activeStep === 3 && <ReplanningStep onNavigate={onNavigate} />}
        {activeStep === 4 && <FutureStep onNavigate={onNavigate} />}
      </div>
    </Panel>

    <div className="guided-demo-controls">
      <button type="button" className="refresh-button" disabled={activeStep === 0} onClick={() => onStepChange(Math.max(0, activeStep - 1))}>Previous step</button>
      <span aria-live="polite">Step {activeStep + 1} of {steps.length}</span>
      <button type="button" className="primary" disabled={activeStep === steps.length - 1} onClick={() => onStepChange(Math.min(steps.length - 1, activeStep + 1))}>Next step</button>
    </div>
  </div>
}

function OverviewStep({ onNavigate }: { onNavigate: (destination: DemoDestination) => void }) {
  return <>
    <p className="guided-lead">FungiSync is designed to help mushroom growers and coordinators connect cultivation, harvest supply, and buyer demand across the Samba and Jammu region.</p>
    <div className="guided-information-grid">
      <div><h4>The coordination problem</h4><p>Harvest quantities and timing can change while buyers have different requirements. Teams need a clear way to inspect supply, compare demand, and identify what remains unassigned.</p></div>
      <div><h4>What this prototype demonstrates</h4><p>Operational record screens, deterministic matching, a reservation-aware advisory preview, and a separately labelled synthetic replan.</p></div>
      <div><h4>What remains a preview</h4><p>Energy intelligence is a future integrated capability. Room telemetry, hardware response, and measured energy evidence are not connected here.</p></div>
    </div>
    <p className="guided-presenter-note">Start with the existing Overview. Its record counts depend on the operational API; room and energy placeholders are explicitly labelled.</p>
    <button type="button" className="primary guided-action" onClick={() => onNavigate('overview')}>Open Overview</button>
  </>
}

function OperationalRecordsStep({ onNavigate }: { onNavigate: (destination: DemoDestination) => void }) {
  return <>
    <p className="guided-lead">Grower, batch, buyer, and order screens use the operational API. Browse existing records only during this presentation.</p>
    <ul className="guided-bullet-list">
      <li>Growers and batches contain registered records and confirmed quantities.</li>
      <li>Orders represent buyer demand held by the backend.</li>
      <li>If an API request fails, present its unavailable state. Do not substitute sample operational records.</li>
    </ul>
    <p className="guided-presenter-note">These screens include create, edit, delete, and order-status controls. This walkthrough does not use them.</p>
    <div className="guided-actions">
      <button type="button" className="primary" onClick={() => onNavigate('growers-batches')}>Open Growers &amp; Batches</button>
      <button type="button" className="refresh-button" onClick={() => onNavigate('orders')}>Open Buyers &amp; Orders</button>
      <button type="button" className="refresh-button" onClick={() => onNavigate('demo-workspace')}>Open synthetic Demo Workspace</button>
    </div>
  </>
}

function MatchingStep({ onNavigate }: { onNavigate: (destination: DemoDestination) => void }) {
  return <>
    <p className="guided-lead">Use the two read-only views to explain the difference between compatibility and reservation-aware allocation.</p>
    <div className="guided-information-grid guided-two-columns">
      <div><span className="guided-label advisory">Matching · read-only</span><h4>Find compatible candidates</h4><p>Matching ranks batch/order compatibility and explains its criteria. It does not reserve quantities or create an allocation.</p></div>
      <div><span className="guided-label advisory">Allocation Preview · read-only</span><h4>Inspect a point-in-time proposal</h4><p>The preview uses confirmed quantities less active reservations. It is not saved, does not reserve stock, and does not commit a buyer.</p></div>
    </div>
    <p className="guided-presenter-note">Both views are inside Harvest Coordination. Open that workspace, then choose the Matching Workspace or Allocation Preview tab. Their operational requests may be unavailable if the database is not ready.</p>
    <button type="button" className="primary guided-action" onClick={() => onNavigate('harvest')}>Open Harvest Coordination</button>
    <p className="guided-presenter-note">For a database-independent walkthrough of sample growers, batches, orders, and a simple demo-only allocation illustration, use the isolated Demo Workspace.</p>
    <button type="button" className="refresh-button guided-action" onClick={() => onNavigate('demo-workspace')}>Open synthetic Demo Workspace</button>
  </>
}

function ReplanningStep({ onNavigate }: { onNavigate: (destination: DemoDestination) => void }) {
  return <>
    <div className="guided-synthetic-callout">
      <span className="guided-label synthetic">Synthetic demonstration</span>
      <p>This scenario simulates a buyer cancellation, preserves the existing commitment, proposes an alternate allocation, and shows the unassigned remainder.</p>
      <p>The exact quantities and reconciliation are supplied by the existing Replanning Demo API response. This guide does not duplicate or calculate those values.</p>
    </div>
    <p className="guided-presenter-note">Open Replanning Demo and read its returned values. If its request fails, state that the scenario endpoint is unavailable; do not present the scenario as executed. No real order, reservation, or buyer commitment changes.</p>
    <button type="button" className="primary guided-action" onClick={() => onNavigate('harvest')}>Open Replanning Demo</button>
  </>
}

function FutureStep({ onNavigate }: { onNavigate: (destination: DemoDestination) => void }) {
  return <>
    <p className="guided-lead">Close by separating the current prototype from planned cultivation and energy capabilities.</p>
    <ul className="guided-bullet-list guided-safety-list">
      <li>No live grow-room telemetry is currently integrated.</li>
      <li>No measured energy savings are claimed.</li>
      <li>No hardware actuation is demonstrated.</li>
      <li>No real dispatch or buyer commitment is performed by this walkthrough.</li>
    </ul>
    <div className="guided-actions guided-preview-actions">
      <button type="button" className="refresh-button" onClick={() => onNavigate('room')}>Grow Room preview</button>
      <button type="button" className="refresh-button" onClick={() => onNavigate('energy')}>Energy Intelligence preview</button>
      <button type="button" className="refresh-button" onClick={() => onNavigate('history')}>Allocation History preview</button>
    </div>
    <p className="guided-presenter-note">The future pages are static previews, not connected monitoring or history workflows.</p>
  </>
}
