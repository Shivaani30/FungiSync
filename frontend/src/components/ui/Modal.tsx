import { useEffect, useRef, type ReactNode } from 'react'

export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const dialogRef = useRef<HTMLElement>(null)
  const previousFocus = useRef<HTMLElement | null>(null)
  useEffect(() => {
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const focusable = () => [...(dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), [href], [tabindex]:not([tabindex="-1"])') ?? [])]
    const initial = dialogRef.current?.querySelector<HTMLElement>('[autofocus]') ?? focusable()[0]
    initial?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); return }
      if (event.key !== 'Tab') return
      const nodes = focusable(); if (!nodes.length) return
      const first = nodes[0]; const last = nodes[nodes.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => { document.removeEventListener('keydown', onKeyDown); previousFocus.current?.focus() }
  }, [onClose])
  return <div className="dialog-layer" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}><section ref={dialogRef} className="dialog" role="dialog" aria-modal="true" aria-labelledby="dialog-title"><div className="dialog-heading"><h2 id="dialog-title">{title}</h2><button type="button" className="dialog-close" aria-label="Close dialog" onClick={onClose}>×</button></div>{children}</section></div>
}

export function ConfirmDialog({ title, message, confirmLabel, busy = false, onCancel, onConfirm }: { title: string; message: string; confirmLabel: string; busy?: boolean; onCancel: () => void; onConfirm: () => void }) {
  return <Modal title={title} onClose={onCancel}><p className="dialog-copy">{message}</p><div className="dialog-actions"><button type="button" onClick={onCancel} disabled={busy}>Cancel</button><button type="button" className="danger-button" onClick={onConfirm} disabled={busy}>{busy ? 'Deleting…' : confirmLabel}</button></div></Modal>
}
