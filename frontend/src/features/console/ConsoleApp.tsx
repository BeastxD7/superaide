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
import { Breadcrumb, BreadcrumbItem, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from '@/components/ui/breadcrumb'
import { Separator } from '@/components/ui/separator'
import { SidebarInset, SidebarProvider, SidebarTrigger } from '@/components/ui/sidebar'
import { TooltipProvider } from '@/components/ui/tooltip'
import { ConsoleSidebar, TABS, type TabKey } from '@/features/console/ConsoleSidebar'
import { Badge, Button, Notice, PageHeader } from '@/features/console/ui'
import { useApi } from '@/features/console/useApi'

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
  // The sidebar's open state is shared with the app's (same cookie), so both remember it.
  const defaultOpen = !document.cookie.split('; ').includes('sidebar_state=false')

  return (
    <TooltipProvider delayDuration={300}>
      <SidebarProvider defaultOpen={defaultOpen}>
        <ConsoleSidebar tab={tab} onTab={setTab} data={data ?? null} onDisconnect={onDisconnect} />
        <SidebarInset>
          <header className="c-topbar">
            <SidebarTrigger className="-ml-1" />
            <Separator orientation="vertical" className="mr-1 data-[orientation=vertical]:h-4" />
            <Breadcrumb className="min-w-0 flex-1">
              <BreadcrumbList className="flex-nowrap">
                <BreadcrumbItem className="hidden sm:inline-flex">Console</BreadcrumbItem>
                <BreadcrumbSeparator className="hidden sm:block" />
                <BreadcrumbItem className="min-w-0">
                  <BreadcrumbPage className="truncate" aria-current={undefined}>
                    {current.label}
                  </BreadcrumbPage>
                </BreadcrumbItem>
              </BreadcrumbList>
            </Breadcrumb>
            {data && (
              <Badge tone={data.frozen ? 'danger' : 'ok'} dot className="c-topbar-status">
                {data.frozen ? 'Paused' : 'Running'}
              </Badge>
            )}
            {data && (
              <Button variant={data.frozen ? 'ok' : 'danger'} size="sm" onClick={togglePause} disabled={switching}>
                {data.frozen ? `Resume ${data.agent_name}` : `Pause ${data.agent_name}`}
              </Button>
            )}
          </header>
          {data?.frozen && (
            <p className="c-banner" role="status">
              Paused: no model calls, no replies
            </p>
          )}
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
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  )
}
