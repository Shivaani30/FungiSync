import { useEffect, useState } from 'react'
import { api } from '../../lib/api'

export function Topbar({ pageTitle }: { pageTitle: string }) {
  const [connection, setConnection] = useState<'checking' | 'connected' | 'unavailable'>('checking')
  const checkConnection = async () => { setConnection('checking'); try { await api.health(); setConnection('connected') } catch { setConnection('unavailable') } }
  useEffect(() => { void checkConnection() }, [])
  const label = connection === 'connected' ? 'API connected' : connection === 'unavailable' ? 'API unavailable — retry' : 'Checking API…'
  return <header className="topbar"><div><div className="breadcrumb">Operations / {pageTitle}</div><h1 className="topbar-title">{pageTitle}</h1></div><button type="button" className={`topbar-meta connection-status ${connection}`} onClick={() => void checkConnection()} aria-live="polite" title="Check FungiSync API connection"><span className="connection-dot" aria-hidden="true" />{label}</button></header>
}
