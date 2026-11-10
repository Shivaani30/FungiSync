import { useState, type FormEvent } from 'react'
import { FormField } from '../../components/ui/FormField'
import type { Grower } from '../../lib/api'

type GrowerPayload = Pick<Grower, 'name' | 'phone' | 'location'>
type Values = GrowerPayload
const emptyValues: Values = { name: '', phone: '', location: '' }

function validate(values: Values) {
  const errors: Partial<Record<keyof Values, string>> = {}
  ;(['name', 'phone', 'location'] as const).forEach(field => { if (!values[field].trim()) errors[field] = 'This field is required.' })
  if (values.name.length > 200) errors.name = 'Maximum length is 200 characters.'
  if (values.phone.length > 30) errors.phone = 'Maximum length is 30 characters.'
  if (values.location.length > 200) errors.location = 'Maximum length is 200 characters.'
  return errors
}

export function GrowerForm({ grower, saving, onCancel, onSave }: { grower?: Grower; saving: boolean; onCancel: () => void; onSave: (payload: GrowerPayload) => Promise<void> }) {
  const [values, setValues] = useState<Values>(grower ? { name: grower.name, phone: grower.phone, location: grower.location } : emptyValues)
  const [errors, setErrors] = useState<Partial<Record<keyof Values, string>>>({})
  const submit = async (event: FormEvent) => { event.preventDefault(); const nextErrors = validate(values); setErrors(nextErrors); if (Object.keys(nextErrors).length) return; await onSave({ name: values.name.trim(), phone: values.phone.trim(), location: values.location.trim() }) }
  const update = (field: keyof Values, value: string) => setValues(current => ({ ...current, [field]: value }))
  return <form className="feature-form" onSubmit={submit}><p className="form-required-note"><em>*</em> Required fields</p><div className="form-grid"><FormField label="Grower name" required error={errors.name}><input autoFocus value={values.name} maxLength={200} onChange={event => update('name', event.target.value)} /></FormField><FormField label="Phone" required error={errors.phone}><input value={values.phone} maxLength={30} onChange={event => update('phone', event.target.value)} /></FormField><FormField label="Location" required error={errors.location}><input value={values.location} maxLength={200} onChange={event => update('location', event.target.value)} /></FormField></div><div className="dialog-actions"><button type="button" onClick={onCancel} disabled={saving}>Cancel</button><button className="primary" disabled={saving}>{saving ? 'Saving…' : 'Save grower'}</button></div></form>
}
