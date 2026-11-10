export function StatusBadge({ status }: { status: string }) {
  const tone = /cancelled|excluded|error/i.test(status) ? 'red' : /pending|expected|partial/i.test(status) ? 'amber' : /fulfilled/i.test(status) ? 'gray' : ''
  return <span className={`status-badge ${tone}`}>{status}</span>
}
