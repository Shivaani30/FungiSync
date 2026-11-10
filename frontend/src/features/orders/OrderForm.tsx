import { useState, type FormEvent } from 'react'
import { FormField } from '../../components/ui/FormField'
import type { Buyer, Order, OrderStatus } from '../../lib/api'

export type OrderPayload = Pick<Order, 'buyer_id' | 'species' | 'quantity_kg' | 'price_limit' | 'delivery_date' | 'status'> & Partial<Pick<Order, 'required_quality_grade'>>
type Values = { buyer_id: string; species: string; quantity_kg: string; price_limit: string; delivery_date: string; required_quality_grade: string; status: OrderStatus }
const statuses: OrderStatus[] = ['pending', 'confirmed', 'fulfilled', 'cancelled']
const emptyValues: Values = { buyer_id: '', species: '', quantity_kg: '', price_limit: '', delivery_date: '', required_quality_grade: '', status: 'pending' }

function validate(values: Values) {
  const errors: Partial<Record<keyof Values, string>> = {}
  if (!values.buyer_id) errors.buyer_id = 'Select a buyer.'
  if (!values.species.trim()) errors.species = 'This field is required.'
  if (values.species.length > 100) errors.species = 'Maximum length is 100 characters.'
  if (!Number.isFinite(Number(values.quantity_kg)) || Number(values.quantity_kg) <= 0) errors.quantity_kg = 'Enter a positive quantity.'
  if (values.price_limit === '' || !Number.isFinite(Number(values.price_limit)) || Number(values.price_limit) < 0) errors.price_limit = 'Enter a non-negative price limit.'
  if (!values.delivery_date) errors.delivery_date = 'Select a delivery date.'
  if (values.required_quality_grade.length > 30) errors.required_quality_grade = 'Maximum length is 30 characters.'
  return errors
}

export function OrderForm({ order, buyers, saving, onCancel, onSave }: { order?: Order; buyers: Buyer[]; saving: boolean; onCancel: () => void; onSave: (payload: OrderPayload) => Promise<void> }) {
  const [values, setValues] = useState<Values>(order ? { buyer_id: order.buyer_id, species: order.species, quantity_kg: order.quantity_kg, price_limit: order.price_limit, delivery_date: order.delivery_date, required_quality_grade: order.required_quality_grade ?? '', status: order.status } : emptyValues)
  const [errors, setErrors] = useState<Partial<Record<keyof Values, string>>>({})
  const update = (field: keyof Values, value: string) => setValues(current => ({ ...current, [field]: value }))
  const submit = async (event: FormEvent) => { event.preventDefault(); const nextErrors = validate(values); setErrors(nextErrors); if (Object.keys(nextErrors).length) return; await onSave({ buyer_id: values.buyer_id, species: values.species.trim(), quantity_kg: values.quantity_kg, price_limit: values.price_limit, delivery_date: values.delivery_date, required_quality_grade: values.required_quality_grade.trim() || null, status: values.status }) }
  return <form className="feature-form" onSubmit={submit}><p className="form-required-note"><em>*</em> Required fields. Quantity must be greater than zero.</p><div className="form-grid"><FormField label="Buyer" required error={errors.buyer_id}><select autoFocus value={values.buyer_id} onChange={event => update('buyer_id', event.target.value)}><option value="">Select buyer…</option>{buyers.map(buyer => <option key={buyer.id} value={buyer.id}>{buyer.name} — {buyer.location}</option>)}</select></FormField><FormField label="Species" required error={errors.species}><input value={values.species} maxLength={100} onChange={event => update('species', event.target.value)} /></FormField><FormField label="Quantity (kg)" required error={errors.quantity_kg}><input type="number" min="0.001" step="0.001" value={values.quantity_kg} onChange={event => update('quantity_kg', event.target.value)} /></FormField><FormField label="Maximum price limit" required error={errors.price_limit}><input type="number" min="0" step="0.01" value={values.price_limit} onChange={event => update('price_limit', event.target.value)} /></FormField><FormField label="Delivery date" required error={errors.delivery_date}><input type="date" value={values.delivery_date} onChange={event => update('delivery_date', event.target.value)} /></FormField><FormField label="Required quality grade" error={errors.required_quality_grade}><input value={values.required_quality_grade} maxLength={30} onChange={event => update('required_quality_grade', event.target.value)} /></FormField><FormField label="Status"><select value={values.status} onChange={event => update('status', event.target.value)}>{statuses.map(status => <option key={status} value={status}>{status}</option>)}</select></FormField></div><div className="dialog-actions"><button type="button" onClick={onCancel} disabled={saving}>Cancel</button><button className="primary" disabled={saving}>{saving ? 'Saving…' : 'Save order'}</button></div></form>
}
