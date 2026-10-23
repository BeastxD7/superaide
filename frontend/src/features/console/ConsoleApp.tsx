'use client'

import { useMemo, useState, type FormEvent } from 'react'
import { BrandMark } from '@/components/BrandMark'
import { api, defaultBase, loadConnection, saveConnection, type Access, type Connection, type Overview as OverviewData } from '@/features/console/api'
import { BreakGlass } from '@/features/console/components/BreakGlass'
import { Conversations } from '@/features/console/pages/Conversations'
import { Overview } from '@/features/console/pages/Overview'
import { People } from '@/features/console/pages/People'
import { SafetyLog } from '@/features/console/pages/SafetyLog'
import { Simulator } from '@/features/console/pages/Simulator'
import { SpendPage } from '@/features/console/pages/SpendPage'
import { Badge, Button, Notice, PageHeader } from '@/features/console/ui'
import { useApi } from '@/features/console/useApi'

// `about` is the line under each page's title (Overview writes its own heading).
const TABS = [
  { key: 'overview', label: 'Overview', about: '' },
  { key: 'people', label: 'People', about: 'Everyone Ava knows, and every account that has written in.' },
  { key: 'conversations', label: 'Conversations', about: 'Every chat, and who could see each answer.' },
  { key: 'log', label: 'Safety log', about: 'Identity checks, refusals and controls, in plain words.' },
  { key: 'spend', label: 'Spend', about: 'What Claude has cost, against the cap.' },
  { key: 'simulator', label: 'Try an impostor', about: 'Try to fool Ava without any risk.' },
] as const
type TabKey = (typeof TABS)[number]['key']
// An owner's content: opened only by break-glass (ADR 0009).
const PRIVATE: TabKey[] = ['people', 'conversations', 'log', 'simulator']

export default function ConsoleApp() {
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
        <div className="c-brand">
          <BrandMark />
          <span className="c-brand-name">Superaide</span>
          <span className="c-brand-tag">Console</span>
        </div>
        <h1>Connect to your agent</h1>
        <p className="quiet">The dashboard reads the agent's admin API on your laptop. Nothing leaves your machine.</p>
        <label>
          Agent address
          <input value={base} onChange={(e) => setBase(e.target.value)} />
        </label>
        <label>
          Admin token (ADMIN_TOKEN in backend/.env)
          <input type="password" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" />
        </label>
        <Button type="submit" disabled={!token}>
          Connect
        </Button>
        {error && <Notice tone="danger">{error}</Notice>}
      </form>
    </main>
  )
}

function Dashboard({ conn, onDisconnect }: { conn: Connection; onDisconnect: () => void }) {
  const [tab, setTab] = useState<TabKey>('overview')
  const overview = useApi<OverviewData>(conn, '/api/overview')
  const [switching, setSwitching] = useState(false)
  const [switchError, setSwitchError] = useState<string | null>(null)
  const [access, setAccess] = useState<Access | null>(null)
  const opened = useMemo<Connection>(() => (access ? { ...conn, access } : conn), [conn, access])
  const data = overview.data

  async function togglePause() {
    if (!data) return
    setSwitching(true)
    setSwitchError(null)
    const verb = data.frozen ? 'resume' : 'pause'
    try {
      await api(conn, '/api/kill', { method: 'POST', body: JSON.stringify({ on: !data.frozen }) })
      await overview.reload()
    } catch (err) {
      // Say so, and re-read the server's state, so the button never claims something that isn't true.
      const why = (err instanceof Error ? err.message : String(err)).replace(/[.\s]+$/, '')
      setSwitchError(`Couldn't ${verb} ${data.agent_name}. Nothing changed (${why}). Try again.`)
      await overview.reload()
    } finally {
      setSwitching(false)
    }
  }

  const current = TABS.find((t) => t.key === tab)!

  return (
    <div className="c-app">
      <div className="c-bar-wrap">
        <div className="c-container">
          <div className="c-bar">
            <div className="c-brand">
              <BrandMark />
              <span className="c-brand-name">Superaide</span>
              <span className="c-brand-tag">Console</span>
            </div>
            <nav className="c-nav" aria-label="Sections">
              {TABS.map((t) => (
                <button key={t.key} className="c-tab" aria-current={tab === t.key ? 'page' : undefined} onClick={() => setTab(t.key)}>
                  {t.label}
                </button>
              ))}
            </nav>
            <div className="c-bar-end">
              {data && (
                <Badge tone={data.frozen ? 'danger' : 'ok'} dot>
                  {data.frozen ? 'Paused' : 'Running'}
                </Badge>
              )}
              {data && (
                <Button variant={data.frozen ? 'ok' : 'danger'} onClick={togglePause} disabled={switching}>
                  {data.frozen ? `Resume ${data.agent_name}` : `Pause ${data.agent_name}`}
                </Button>
              )}
              <Button variant="ghost" onClick={onDisconnect}>
                Disconnect
              </Button>
            </div>
          </div>
          {data?.frozen && (
            <p className="c-banner" role="status">
              Paused: no model calls, no replies
            </p>
          )}
        </div>
      </div>

      <main className="c-container c-main">
        {switchError && <Notice tone="danger">{switchError}</Notice>}
        {overview.error && overview.error.status === 401 ? (
          <Notice tone="danger">
            The admin token was refused. <button className="link-button" onClick={onDisconnect}>Enter it again</button>
          </Notice>
        ) : overview.error ? (
          <Notice tone="danger">{overview.error.message}</Notice>
        ) : null}
        <div className="c-page" key={tab}>
          {tab !== 'overview' && <PageHeader title={current.label} description={current.about} />}
          {tab === 'overview' && data && <Overview data={data} />}
          {PRIVATE.includes(tab) && !access && <BreakGlass conn={conn} onOpen={setAccess} />}
          {PRIVATE.includes(tab) && access && (
            <Notice className="break-glass-open">
              <span>
                Open for one owner's Ava ({access.instance ? access.instance.slice(0, 8) : "the server's first"}), recorded with your reason: “{access.reason}”.
              </span>
              <button className="link-button" onClick={() => setAccess(null)}>
                Close
              </button>
            </Notice>
          )}
          {tab === 'people' && access && <People conn={opened} />}
          {tab === 'conversations' && access && <Conversations conn={opened} />}
          {tab === 'log' && access && <SafetyLog conn={opened} />}
          {tab === 'spend' && <SpendPage conn={conn} />}
          {tab === 'simulator' && access && <Simulator conn={opened} enabled={data?.simulator ?? false} agentName={data?.agent_name ?? 'The agent'} />}
        </div>
      </main>
    </div>
  )
}
