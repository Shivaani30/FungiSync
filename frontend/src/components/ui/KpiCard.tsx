export function KpiCard({ icon, label, value, note }: { icon: string; label: string; value: string | number; note: string }) {
  return <article className="kpi-card"><span className="kpi-icon" aria-hidden="true">{icon}</span><div className="kpi-label">{label}</div><div className="kpi-value">{value}</div><div className="kpi-note">{note}</div></article>
}
