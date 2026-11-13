type IconName = 'overview' | 'guide' | 'demo' | 'growers' | 'batches' | 'buyers' | 'orders' | 'matching' | 'room' | 'energy' | 'harvest' | 'history'

const paths: Record<IconName, React.ReactNode> = {
  overview: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
  guide: <><path d="M5 4.5A2.5 2.5 0 0 1 7.5 2H20v17H7.5A2.5 2.5 0 0 0 5 21.5z" /><path d="M5 4.5v17M9 7h7M9 11h7" /><path d="m2 8 2-2 2 2" /></>,
  demo: <><rect x="3" y="4" width="8" height="7" rx="1" /><rect x="13" y="4" width="8" height="7" rx="1" /><rect x="3" y="13" width="8" height="7" rx="1" /><path d="M15 16h4m-2-2v4" /></>,
  growers: <><path d="M12 21V11" /><path d="M12 15c-5 0-7-3-7-7 5 0 7 3 7 7Z" /><path d="M12 11c0-4 2-7 7-8 0 5-2 8-7 8Z" /></>,
  batches: <><path d="M4 7h16v13H4z" /><path d="M8 7V4h8v3M8 12h8M8 16h5" /></>,
  buyers: <><circle cx="9" cy="8" r="3" /><path d="M3 20c.6-4 2.6-6 6-6s5.4 2 6 6M16 11c2.8.2 4.4 1.7 5 4M16 5.5a2.5 2.5 0 0 1 0 5" /></>,
  orders: <><path d="M6 3h9l3 3v15H6z" /><path d="M15 3v4h4M9 12h6M9 16h6" /></>,
  matching: <><path d="m7 7 5 5 5-5M7 17l5-5 5 5" /><path d="M3 7h4M17 7h4M3 17h4M17 17h4" /></>,
  room: <><path d="M4 20V5l8-2 8 2v15" /><path d="M8 20v-6h8v6M8 8h.01M12 7h.01M16 8h.01" /></>,
  energy: <><path d="m13 2-3 8h7l-6 12 1-9H6l7-11Z" /></>,
  harvest: <><path d="M4 20h16M6 17l3-5 3 3 5-8 2 3" /><path d="M5 5c4 0 6 2 6 6-4 0-6-2-6-6Z" /></>,
  history: <><path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5M12 7v5l3 2" /></>,
}

export function NavigationIcon({ name }: { name: IconName }) {
  return <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

export type { IconName }
