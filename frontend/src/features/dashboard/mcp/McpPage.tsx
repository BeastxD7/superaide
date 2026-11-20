'use client'

import { useState, type FormEvent } from 'react'
import { Notice } from '@/components/site'
import { PageContainer, PageHeader, ResponsiveGrid } from '@/components/layout'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { StepUpDialog } from '@/features/dashboard/approvals/StepUpDialog'
import { errorMessage } from '@/lib/action'
import { call } from '@/lib/api'
import { useResource } from '@/lib/use-resource'
import { useSession } from '@/lib/session-context'

type Risk = 'read' | 'write' | 'destructive'
type Tool = {
  name: string
  description: string
  risk: Risk
  risk_set: boolean
  approval: 'approved' | 'changed' | 'new'
  server_says: { readOnlyHint?: boolean; destructiveHint?: boolean }
}
type Server = {
  id: string
  label: string
  transport: 'stdio' | 'http'
  command: string
  args: string[]
  url: string
  enabled: boolean
  secrets: { env?: Record<string, string>; headers?: Record<string, string> }
  listed_at: string | null
  last_error: string
  calls_per_hour: number
  tools: Tool[]
}
type Listing = { servers: Server[]; max_servers: number; can_run_programs: boolean }
type Pair = { name: string; value: string }

const RISK_HELP: Record<Risk, string> = {
  read: 'runs at once',
  write: 'waits for your OK every time',
  destructive: 'waits for your OK every time, and is refused after untrusted content',
}

export function McpPage() {
  const { me } = useSession()
  const name = me?.agent.name ?? 'Ava'
  const { data, error, reload } = useResource<Listing>('/mcp/servers')
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  async function act(key: string, fn: () => Promise<unknown>) {
    setBusy(key)
    setProblem(null)
    try {
      await fn()
    } catch (e) {
      setProblem(errorMessage(e))
    } finally {
      await reload()
      setBusy(null)
    }
  }

  return (
    <PageContainer width="list">
      <PageHeader
        title="Tools (MCP)"
        description={`Add an MCP server and its tools become ${name}'s tools. Nothing runs until you approve the tools you see here; anything that changes something waits for your OK every time, and what a server returns is treated as information, never as instructions.`}
      />
      {(error || problem) && (
        <div className="mt-4">
          <Notice tone="error">{problem ?? error}</Notice>
        </div>
      )}
      <AddServer canRunPrograms={data?.can_run_programs ?? true} onAdded={reload} setProblem={setProblem} full={!!data && data.servers.length >= data.max_servers} />
      <div className="mt-6" aria-live="polite">
        {!data ? (
          <Skeleton className="h-32 w-full" />
        ) : data.servers.length === 0 ? (
          <p className="rounded-xl border border-dashed border-line p-6 text-center text-sm text-muted">No servers yet.</p>
        ) : (
          <ResponsiveGrid min="26rem" className="gap-4">
            {data.servers.map((s) => (
              <ServerCard key={s.id} server={s} busy={busy} act={act} />
            ))}
          </ResponsiveGrid>
        )}
      </div>
    </PageContainer>
  )
}

function ServerCard({ server: s, busy, act }: { server: Server; busy: string | null; act: (key: string, fn: () => Promise<unknown>) => Promise<void> }) {
  const [testError, setTestError] = useState('')
  const unapproved = s.tools.filter((t) => t.approval !== 'approved').length
  const where = s.transport === 'stdio' ? [s.command, ...s.args].join(' ') : s.url
  const secrets = [...Object.entries(s.secrets.env ?? {}), ...Object.entries(s.secrets.headers ?? {})]
  return (
    <section aria-label={s.label} className="rounded-xl border border-line bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h2 className="truncate text-lg font-medium text-ink">{s.label}</h2>
          <p className="truncate text-xs text-muted">
            {s.transport === 'stdio' ? 'Program on this computer' : 'Remote server'}: {where}
          </p>
        </div>
        <label className="flex min-h-11 items-center gap-2 text-sm text-body">
          <input
            type="checkbox"
            role="switch"
            aria-label={`${s.label} is on`}
            className="size-5 accent-violet-600"
            checked={s.enabled}
            disabled={busy !== null}
            onChange={(e) => void act(`on-${s.id}`, () => call(`/mcp/servers/${s.id}`, { method: 'PATCH', json: { enabled: e.target.checked } }))}
          />
          {s.enabled ? 'On' : 'Off'}
        </label>
      </div>
      {secrets.length > 0 && (
        <p className="mt-2 text-xs text-muted">Stored in the vault: {secrets.map(([k, h]) => `${k} (${h})`).join(', ')}</p>
      )}
      {(testError || s.last_error) && (
        <div className="mt-3">
          <Notice tone="error">{testError || s.last_error}</Notice>
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={busy !== null}
          onClick={() =>
            void act(`test-${s.id}`, async () => {
              const out = await call<{ error: string }>(`/mcp/servers/${s.id}/test`, { method: 'POST' })
              setTestError(out.error)
            })
          }
        >
          {busy === `test-${s.id}` ? 'Testing…' : 'Test connection'}
        </Button>
        {s.tools.length > 0 && (
          <Button
            size="sm"
            disabled={busy !== null || unapproved === 0}
            onClick={() => void act(`approve-${s.id}`, () => call(`/mcp/servers/${s.id}/approve`, { method: 'POST', json: {} }))}
          >
            {unapproved === 0 ? 'All tools approved' : `Approve ${unapproved} tool${unapproved === 1 ? '' : 's'} as listed`}
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          disabled={busy !== null}
          onClick={() => {
            if (window.confirm(`Remove ${s.label}? Its approvals and stored secrets are deleted.`)) void act(`rm-${s.id}`, () => call(`/mcp/servers/${s.id}`, { method: 'DELETE' }))
          }}
        >
          Remove
        </Button>
      </div>
      {s.listed_at && s.tools.length === 0 && <p className="mt-3 text-sm text-muted">The server lists no tools.</p>}
      {!s.listed_at && <p className="mt-3 text-sm text-muted">Press Test connection to see its tools.</p>}
      {s.tools.length > 0 && (
        <ul className="mt-3 divide-y divide-line/70" aria-label={`Tools of ${s.label}`}>
          {s.tools.map((t) => (
            <li key={t.name} className="py-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-mono text-sm text-ink">{t.name}</span>
                <Badge variant={t.approval === 'approved' ? 'secondary' : 'outline'}>
                  {t.approval === 'approved' ? 'Approved' : t.approval === 'changed' ? 'Changed since approved' : 'Not approved'}
                </Badge>
              </div>
              {t.description && <p className="mt-1 line-clamp-3 text-sm text-body">{t.description}</p>}
              <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
                <label htmlFor={`risk-${s.id}-${t.name}`} className="text-muted">
                  Risk
                </label>
                <select
                  id={`risk-${s.id}-${t.name}`}
                  aria-label={`Risk class for ${t.name}`}
                  className="min-h-9 rounded-md border border-line bg-white px-2"
                  value={t.risk}
                  disabled={busy !== null}
                  onChange={(e) => void act(`risk-${s.id}-${t.name}`, () => call(`/mcp/servers/${s.id}/tools/${encodeURIComponent(t.name)}/risk`, { method: 'PUT', json: { risk: e.target.value } }))}
                >
                  {(['read', 'write', 'destructive'] as Risk[]).map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
                <span className="text-xs text-muted">
                  {RISK_HELP[t.risk]}
                  {!t.risk_set && ' (not set by you yet)'}
                  {t.server_says.readOnlyHint ? '. The server says read-only; that is only a hint.' : ''}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function AddServer({ canRunPrograms, onAdded, setProblem, full }: { canRunPrograms: boolean; onAdded: () => Promise<void>; setProblem: (m: string | null) => void; full: boolean }) {
  const [open, setOpen] = useState(false)
  const [label, setLabel] = useState('')
  const [kind, setKind] = useState<'stdio' | 'http'>('http')
  const [command, setCommand] = useState('')
  const [args, setArgs] = useState('')
  const [url, setUrl] = useState('')
  const [pairs, setPairs] = useState<Pair[]>([])
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)

  function body(extra: Record<string, unknown> = {}) {
    const secret = Object.fromEntries(pairs.filter((p) => p.name.trim() && p.value).map((p) => [p.name.trim(), p.value]))
    return {
      label: label.trim(),
      transport: kind,
      ...(kind === 'stdio' ? { command: command.trim(), args: args.split('\n').map((a) => a.trim()).filter(Boolean), env: secret } : { url: url.trim(), headers: secret }),
      ...extra,
    }
  }

  async function send(extra: Record<string, unknown> = {}) {
    setBusy(true)
    setProblem(null)
    try {
      await call('/mcp/servers', { method: 'POST', json: body(extra) })
      setLabel('')
      setCommand('')
      setArgs('')
      setUrl('')
      setPairs([])
      setOpen(false)
      setConfirming(false)
      await onAdded()
    } catch (e) {
      setProblem(errorMessage(e))
      setConfirming(false)
    } finally {
      setBusy(false)
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault()
    if (kind === 'stdio') setConfirming(true)
    else void send()
  }

  if (!open)
    return (
      <div className="mt-6">
        <Button disabled={full} onClick={() => setOpen(true)}>
          {full ? 'Server limit reached' : 'Add a server'}
        </Button>
      </div>
    )
  const secretLabel = kind === 'stdio' ? 'Environment variable' : 'Header'
  return (
    <form onSubmit={submit} aria-label="Add a server" className="mt-6 max-w-2xl space-y-3 rounded-xl border border-line bg-white p-4">
      <div>
        <label htmlFor="mcp-label" className="block text-sm text-muted">
          Name
        </label>
        <Input id="mcp-label" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={80} placeholder="Team notes" required />
      </div>
      <fieldset className="flex flex-wrap gap-4 text-sm">
        <legend className="sr-only">Type of server</legend>
        <label className="flex items-center gap-2">
          <input type="radio" name="mcp-kind" className="accent-violet-600" checked={kind === 'http'} onChange={() => setKind('http')} />
          Remote server (URL)
        </label>
        {canRunPrograms && (
          <label className="flex items-center gap-2">
            <input type="radio" name="mcp-kind" className="accent-violet-600" checked={kind === 'stdio'} onChange={() => setKind('stdio')} />
            Program on this computer
          </label>
        )}
      </fieldset>
      {kind === 'http' ? (
        <div>
          <label htmlFor="mcp-url" className="block text-sm text-muted">
            Address
          </label>
          <Input id="mcp-url" type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://notes.example.com/mcp" required />
        </div>
      ) : (
        <>
          <div>
            <label htmlFor="mcp-command" className="block text-sm text-muted">
              Command
            </label>
            <Input id="mcp-command" value={command} onChange={(e) => setCommand(e.target.value)} placeholder="npx" required />
          </div>
          <div>
            <label htmlFor="mcp-args" className="block text-sm text-muted">
              Arguments, one per line
            </label>
            <textarea id="mcp-args" className="w-full rounded-md border border-line px-2 py-1.5 font-mono text-sm" rows={3} value={args} onChange={(e) => setArgs(e.target.value)} />
          </div>
        </>
      )}
      <div className="space-y-2">
        {pairs.map((p, i) => (
          <div key={i} className="grid gap-2 sm:grid-cols-2">
            <Input aria-label={`${secretLabel} name ${i + 1}`} value={p.name} onChange={(e) => setPairs(pairs.map((q, j) => (j === i ? { ...q, name: e.target.value } : q)))} placeholder={kind === 'stdio' ? 'API_KEY' : 'Authorization'} />
            <Input aria-label={`${secretLabel} value ${i + 1}`} type="password" autoComplete="off" value={p.value} onChange={(e) => setPairs(pairs.map((q, j) => (j === i ? { ...q, value: e.target.value } : q)))} placeholder="Kept in the vault, never shown again" />
          </div>
        ))}
        <Button type="button" size="sm" variant="ghost" onClick={() => setPairs([...pairs, { name: '', value: '' }])}>
          Add a {secretLabel.toLowerCase()}
        </Button>
      </div>
      <div className="flex gap-2">
        <Button type="submit" disabled={busy}>
          Add server
        </Button>
        <Button type="button" variant="outline" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
      {confirming && (
        <StepUpDialog
          action="mcp_stdio_server"
          title="Run a program on this computer?"
          warning="This server is a program that Ava starts on this computer with your permissions, each time she uses one of its tools. Only add programs you trust."
          example="Example: a file server started with npx can read the folders you give it."
          confirmLabel="Add the server"
          onConfirmed={(confirmation, password) => send({ confirmation, confirm_password: password })}
          onCancel={() => setConfirming(false)}
        />
      )}
    </form>
  )
}
