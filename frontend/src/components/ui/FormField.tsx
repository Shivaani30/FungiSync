import type { ReactNode } from 'react'

export function FormField({ label, required = false, error, children }: { label: string; required?: boolean; error?: string; children: ReactNode }) {
  return <label className="form-field"><span>{label}{required && <em aria-hidden="true"> *</em>}</span>{children}{error && <small className="field-error" role="alert">{error}</small>}</label>
}
