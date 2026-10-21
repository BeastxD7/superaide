import { useState, type FormEvent } from 'react'
import { api, defaultBase, loadConnection, saveConnection, type Connection, type Overview as OverviewData } from './api'
import { Conversations } from './pages/Conversations'
import { Overview } from './pages/Overview'
import { People } from './pages/People'
import { SafetyLog } from './pages/SafetyLog'
import { Simulator } from './pages/Simulator'
import { SpendPage } from './pages/SpendPage'
import { useApi } from './useApi'

const TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'people', label: 'People' },
  { key: 'conversations', label: 'Conversations' },
  { key: 'log', label: 'Safety log' },
  { key: 'spend', label: 'Spend' },
  { key: 'simulator', label: 'Try an impostor' },
] as const
type TabKey = (typeof TABS)[number]['key']

export default function App() {
  const [conn, setConn] = useState<Connection | null>(() => loadConnection())
  if (!conn)
    return (
      <Connect
        onConnect={(c) => {
          saveConnection(c)
          setConn(c)
        }}
      />
    )
  return (
    <Dashboard
      conn={conn}
      onDisconnect={() => {
        saveConnection(null)
        setConn(null)
      }}
    />
  )
}

function Connect({ onConnect }: { onConnect: (c: Connection) => void }) {
  const [base, setBase] = useState(defaultBase)
  const [token, setToken] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    const c = { base, token }
    try {
      await api(c, '/api/overview')
      onConnect(c)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <main className="connect">
      <form onSubmit={submit}>
        <h1>Connect to your agent</h1>
        <p className="quiet">The dashboard reads the agent's admin API on your laptop. Nothing leaves your machine.</p>
        <label>
          Agent address
          <input value={base} onChange={(e) => setBase(e.target.value)} />
        </label>
        <label>
          Admin token (ADMIN_TOKEN in .env)
          <input type="password" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" />
        </label>
        <button className="primary" type="submit" disabled={!token}>
          Connect
        </button>
        {error && <p className="error">{error}</p>}
      </form>
    </main>
  )
}

function Dashboard({ conn, onDisconnect }: { conn: Connection; onDisconnect: () => void }) {
  const [tab, setTab] = useState<TabKey>('overview')
  const overview = useApi<OverviewData>(conn, '/api/overview')
  const [switching, setSwitching] = useState(false)
  const data = overview.data

  async function togglePause() {
    if (!data) return
    setSwitching(true)
    try {
      await api(conn, '/api/kill', { method: 'POST', body: JSON.stringify({ on: !data.frozen }) })
      await overview.reload()
    } finally {
      setSwitching(false)
    }
  }

  return (
    <div className={data?.frozen ? 'app paused' : 'app'}>
      <header className="topbar">
        <div className="identity">
          <span className="agent-name">{data?.agent_name ?? 'Agent'}</span>
          {data && <span className="stage-pill">{data.stage}</span>}
          {data?.frozen && <span className="paused-note">Paused: no model calls, no replies</span>}
        </div>
        <div className="controls">
          {data && (
            <button className={data.frozen ? 'pause resume' : 'pause'} onClick={togglePause} disabled={switching}>
              {data.frozen ? `Resume ${data.agent_name}` : `Pause ${data.agent_name}`}
            </button>
          )}
          <button className="link-button" onClick={onDisconnect}>
            Disconnect
          </button>
        </div>
      </header>

      <nav className="tabs" aria-label="Sections">
        {TABS.map((t) => (
          <button key={t.key} className={tab === t.key ? 'tab on' : 'tab'} aria-current={tab === t.key ? 'page' : undefined} onClick={() => setTab(t.key)}>
            {t.label}
          </button>
        ))}
      </nav>

      <main className="content">
        {overview.error && overview.error.status === 401 ? (
          <p className="error">
            The admin token was refused. <button className="link-button" onClick={onDisconnect}>Enter it again</button>
          </p>
        ) : overview.error ? (
          <p className="error">{overview.error.message}</p>
        ) : null}
        {tab === 'overview' && data && <Overview data={data} />}
        {tab === 'people' && <People conn={conn} />}
        {tab === 'conversations' && <Conversations conn={conn} />}
        {tab === 'log' && <SafetyLog conn={conn} />}
        {tab === 'spend' && <SpendPage conn={conn} />}
        {tab === 'simulator' && <Simulator conn={conn} enabled={data?.simulator ?? false} agentName={data?.agent_name ?? 'The agent'} />}
      </main>
    </div>
  )
}
