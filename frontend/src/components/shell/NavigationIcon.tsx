type IconName = 'overview' | 'growers' | 'batches' | 'buyers' | 'orders' | 'matching'

const paths: Record<IconName, React.ReactNode> = {
  overview: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
  growers: <><path d="M12 21V11" /><path d="M12 15c-5 0-7-3-7-7 5 0 7 3 7 7Z" /><path d="M12 11c0-4 2-7 7-8 0 5-2 8-7 8Z" /></>,
  batches: <><path d="M4 7h16v13H4z" /><path d="M8 7V4h8v3M8 12h8M8 16h5" /></>,
  buyers: <><circle cx="9" cy="8" r="3" /><path d="M3 20c.6-4 2.6-6 6-6s5.4 2 6 6M16 11c2.8.2 4.4 1.7 5 4M16 5.5a2.5 2.5 0 0 1 0 5" /></>,
  orders: <><path d="M6 3h9l3 3v15H6z" /><path d="M15 3v4h4M9 12h6M9 16h6" /></>,
  matching: <><path d="m7 7 5 5 5-5M7 17l5-5 5 5" /><path d="M3 7h4M17 7h4M3 17h4M17 17h4" /></>,
}

export function NavigationIcon({ name }: { name: IconName }) {
  return <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

export type { IconName }
