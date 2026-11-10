import { StatusBadge } from '../../components/ui/StatusBadge'
import type { Batch, Buyer, Grower, MatchCandidate, Order } from '../../lib/api'

type Direction = 'order' | 'batch'

const value = (input: string | number | null | undefined, fallback = 'Unknown') => input === null || input === undefined || input === '' ? fallback : String(input)

function Explanation({ title, values, tone = '' }: { title: string; values: string[]; tone?: string }) {
  if (!values.length) return null
  return <div className={`match-explanation ${tone}`}><h4>{title}</h4><ul>{values.map(item => <li key={item}>{item}</li>)}</ul></div>
}

export function MatchingCandidateCard({ candidate, direction, batch, order, grower, buyer }: { candidate: MatchCandidate; direction: Direction; batch?: Batch; order?: Order; grower?: Grower; buyer?: Buyer }) {
  const isBatch = direction === 'order'
  const title = isBatch ? `${grower?.name ?? candidate.grower_id} · ${batch?.id ?? candidate.batch_id}` : `${buyer?.name ?? candidate.buyer_id ?? 'Unknown buyer'} · ${order?.id ?? candidate.order_id ?? 'Unknown order'}`
  const facts = isBatch ? [
    ['Species', batch?.species], ['Confirmed quantity', batch?.confirmed_kg ? `${batch.confirmed_kg} kg` : null], ['Can supply', `${candidate.quantity_that_could_be_supplied_kg} kg`], ['Harvest date', batch?.harvest_date], ['Best-before date', batch?.best_before_date], ['Asking price', batch?.asking_price_per_kg], ['Quality grade', batch?.quality_grade],
  ] : [
    ['Species', order?.species], ['Requested quantity', order?.quantity_kg ? `${order.quantity_kg} kg` : null], ['Can supply', `${candidate.quantity_that_could_be_supplied_kg} kg`], ['Delivery date', order?.delivery_date], ['Price limit', order?.price_limit], ['Required quality', order?.required_quality_grade], ['Buyer location', buyer?.location],
  ]
  return <article className="match-card"><div className="match-card-head"><div><div className="match-title">{title}</div><div className="match-subtitle">{isBatch ? `Grower location: ${value(grower?.location)}` : `Batch: ${candidate.batch_id}`}</div></div><div className="match-score"><StatusBadge status={candidate.eligible ? 'Eligible' : 'Ineligible'} /><strong>{candidate.compatibility_score}%</strong><small>Score · data completeness {candidate.data_completeness_percentage}%</small></div></div><div className="match-facts">{facts.map(([label, item]) => <div key={label}><span>{label}</span><strong>{value(item)}</strong></div>)}</div><details className="match-details"><summary>View score breakdown and evidence</summary><div className="criteria-grid">{Object.entries(candidate.criteria).map(([name, criterion]) => <div className={`criterion ${criterion.status === 'unknown' ? 'unknown' : criterion.status === 'incompatible' ? 'incompatible' : ''}`} key={name}><span>{name}</span><strong>{criterion.status === 'unknown' ? 'Unknown' : `${criterion.points} / ${criterion.weight}`}</strong><small>{criterion.reason}</small></div>)}</div><Explanation title="Why this candidate matches" values={candidate.positive_reasons} /><Explanation title="Unknown or missing information" values={candidate.unknown_criteria} tone="unknown" /><Explanation title="Warnings" values={candidate.warnings} tone="warning" /><Explanation title="Exclusion reasons" values={candidate.exclusion_reasons} tone="incompatible" /></details></article>
}
