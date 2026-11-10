import { useState, type FormEvent } from 'react'
import { FormField } from '../../components/ui/FormField'
import type { Batch, BatchStatus, Grower } from '../../lib/api'

export type BatchPayload = Pick<Batch, 'grower_id' | 'species' | 'expected_kg' | 'harvest_date' | 'status'> & Partial<Pick<Batch, 'confirmed_kg' | 'asking_price_per_kg' | 'quality_grade' | 'best_before_date'>>
type Values = { grower_id: string; species: string; expected_kg: string; confirmed_kg: string; harvest_date: string; asking_price_per_kg: string; quality_grade: string; best_before_date: string; status: BatchStatus }
const statuses: BatchStatus[] = ['expected', 'confirmed', 'harvested', 'cancelled']
const emptyValues: Values = { grower_id: '', species: '', expected_kg: '', confirmed_kg: '', harvest_date: '', asking_price_per_kg: '', quality_grade: '', best_before_date: '', status: 'expected' }
const nonnegative = (value: string) => value === '' || (Number.isFinite(Number(value)) && Number(value) >= 0)

function validate(values: Values) {
  const errors: Partial<Record<keyof Values, string>> = {}
  if (!values.grower_id) errors.grower_id = 'Select a grower.'
  if (!values.species.trim()) errors.species = 'This field is required.'
  if (values.species.length > 100) errors.species = 'Maximum length is 100 characters.'
  if (values.expected_kg === '' || !nonnegative(values.expected_kg)) errors.expected_kg = 'Enter a non-negative quantity.'
  if (!nonnegative(values.confirmed_kg)) errors.confirmed_kg = 'Enter a non-negative quantity.'
  if (!nonnegative(values.asking_price_per_kg)) errors.asking_price_per_kg = 'Enter a non-negative price.'
  if (!values.harvest_date) errors.harvest_date = 'Select a harvest date.'
  if (values.quality_grade && !values.quality_grade.trim()) errors.quality_grade = 'Quality grade cannot be blank.'
  if (values.quality_grade.length > 30) errors.quality_grade = 'Maximum length is 30 characters.'
  if (values.best_before_date && values.harvest_date && values.best_before_date < values.harvest_date) errors.best_before_date = 'Must be on or after the harvest date.'
  return errors
}

export function BatchForm({ batch, growers, saving, onCancel, onSave }: { batch?: Batch; growers: Grower[]; saving: boolean; onCancel: () => void; onSave: (payload: BatchPayload) => Promise<void> }) {
  const [values, setValues] = useState<Values>(batch ? { grower_id: batch.grower_id, species: batch.species, expected_kg: batch.expected_kg, confirmed_kg: batch.confirmed_kg ?? '', harvest_date: batch.harvest_date, asking_price_per_kg: batch.asking_price_per_kg ?? '', quality_grade: batch.quality_grade ?? '', best_before_date: batch.best_before_date ?? '', status: batch.status } : emptyValues)
  const [errors, setErrors] = useState<Partial<Record<keyof Values, string>>>({})
  const update = (field: keyof Values, value: string) => setValues(current => ({ ...current, [field]: value }))
  const submit = async (event: FormEvent) => { event.preventDefault(); const nextErrors = validate(values); setErrors(nextErrors); if (Object.keys(nextErrors).length) return; await onSave({ grower_id: values.grower_id, species: values.species.trim(), expected_kg: values.expected_kg, confirmed_kg: values.confirmed_kg || null, harvest_date: values.harvest_date, asking_price_per_kg: values.asking_price_per_kg || null, quality_grade: values.quality_grade.trim() || null, best_before_date: values.best_before_date || null, status: values.status }) }
  return <form className="feature-form" onSubmit={submit}><p className="form-required-note"><em>*</em> Required fields. Quantity and price fields accept zero because the API permits non-negative values.</p><div className="form-grid"><FormField label="Grower" required error={errors.grower_id}><select autoFocus value={values.grower_id} onChange={event => update('grower_id', event.target.value)}><option value="">Select grower…</option>{growers.map(grower => <option value={grower.id} key={grower.id}>{grower.name} — {grower.location}</option>)}</select></FormField><FormField label="Species" required error={errors.species}><input value={values.species} maxLength={100} onChange={event => update('species', event.target.value)} /></FormField><FormField label="Expected quantity (kg)" required error={errors.expected_kg}><input type="number" min="0" step="0.001" value={values.expected_kg} onChange={event => update('expected_kg', event.target.value)} /></FormField><FormField label="Confirmed quantity (kg)" error={errors.confirmed_kg}><input type="number" min="0" step="0.001" value={values.confirmed_kg} onChange={event => update('confirmed_kg', event.target.value)} /></FormField><FormField label="Harvest date" required error={errors.harvest_date}><input type="date" value={values.harvest_date} onChange={event => update('harvest_date', event.target.value)} /></FormField><FormField label="Best-before date" error={errors.best_before_date}><input type="date" value={values.best_before_date} onChange={event => update('best_before_date', event.target.value)} /></FormField><FormField label="Asking price per kg" error={errors.asking_price_per_kg}><input type="number" min="0" step="0.01" value={values.asking_price_per_kg} onChange={event => update('asking_price_per_kg', event.target.value)} /></FormField><FormField label="Quality grade" error={errors.quality_grade}><input value={values.quality_grade} maxLength={30} onChange={event => update('quality_grade', event.target.value)} /></FormField><FormField label="Status"><select value={values.status} onChange={event => update('status', event.target.value)}>{statuses.map(status => <option key={status} value={status}>{status}</option>)}</select></FormField></div><div className="dialog-actions"><button type="button" onClick={onCancel} disabled={saving}>Cancel</button><button className="primary" disabled={saving}>{saving ? 'Saving…' : 'Save batch'}</button></div></form>
}
