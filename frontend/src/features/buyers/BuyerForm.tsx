import { useState, type FormEvent } from 'react'
import { FormField } from '../../components/ui/FormField'
import type { Buyer } from '../../lib/api'

type BuyerPayload = Pick<Buyer, 'name' | 'location' | 'contact'>
const emptyValues: BuyerPayload = { name: '', location: '', contact: '' }

function validate(values: BuyerPayload) {
  const errors: Partial<Record<keyof BuyerPayload, string>> = {}
  ;(['name', 'location', 'contact'] as const).forEach(field => { if (!values[field].trim()) errors[field] = 'This field is required.' })
  if (values.name.length > 200) errors.name = 'Maximum length is 200 characters.'
  if (values.location.length > 200) errors.location = 'Maximum length is 200 characters.'
  if (values.contact.length > 200) errors.contact = 'Maximum length is 200 characters.'
  return errors
}

export function BuyerForm({ buyer, saving, onCancel, onSave }: { buyer?: Buyer; saving: boolean; onCancel: () => void; onSave: (payload: BuyerPayload) => Promise<void> }) {
  const [values, setValues] = useState<BuyerPayload>(buyer ? { name: buyer.name, location: buyer.location, contact: buyer.contact } : emptyValues)
  const [errors, setErrors] = useState<Partial<Record<keyof BuyerPayload, string>>>({})
  const update = (field: keyof BuyerPayload, value: string) => setValues(current => ({ ...current, [field]: value }))
  const submit = async (event: FormEvent) => { event.preventDefault(); const nextErrors = validate(values); setErrors(nextErrors); if (Object.keys(nextErrors).length) return; await onSave({ name: values.name.trim(), location: values.location.trim(), contact: values.contact.trim() }) }
  return <form className="feature-form" onSubmit={submit}><p className="form-required-note"><em>*</em> Required fields</p><div className="form-grid"><FormField label="Buyer name" required error={errors.name}><input autoFocus value={values.name} maxLength={200} onChange={event => update('name', event.target.value)} /></FormField><FormField label="Location" required error={errors.location}><input value={values.location} maxLength={200} onChange={event => update('location', event.target.value)} /></FormField><FormField label="Contact" required error={errors.contact}><input value={values.contact} maxLength={200} onChange={event => update('contact', event.target.value)} /></FormField></div><div className="dialog-actions"><button type="button" onClick={onCancel} disabled={saving}>Cancel</button><button className="primary" disabled={saving}>{saving ? 'Saving…' : 'Save buyer'}</button></div></form>
}
