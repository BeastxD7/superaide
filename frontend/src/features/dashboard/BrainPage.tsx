'use client'

import { ArrowLeft, Download } from 'lucide-react'
import Link from 'next/link'
import { useMemo, useState } from 'react'
import { ActionError, LoadError } from '@/components/load-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { CATEGORY_LABEL, useAppData } from '@/features/dashboard/data'
import { call } from '@/lib/api'
import { API_URL } from '@/lib/http'
import { errorMessage } from '@/lib/action'
import { useSession } from '@/lib/session-context'

type BrainNode = {
  id: string
  type: 'owner' | 'topic' | 'fact' | 'person' | 'source'
  label: string
  category?: string
  origin?: 'you' | 'file' | 'channel' | 'imported'
  source?: string
  document?: string | null
  confidence?: number
  freshness?: number
  learned?: string
  relationship?: string
  vip?: boolean
  kind?: string
  superseded?: boolean
  replaced_by?: string
  replaced_at?: string
  locked?: boolean
}
type BrainMap = {
  nodes: BrainNode[]
  edges: { from: string; to: string; kind: string }[]
  forgotten: { id: string; text: string; category: string; forgotten_at: string }[]
  origins: Record<string, { label: string; count: number }>
  truncated: boolean
}

const W = 800
const H = 700
const C = { x: W / 2, y: H / 2 }
const COLOUR: Record<string, string> = { you: '#7c5cff', file: '#0ea5e9', channel: '#10b981', imported: '#f59e0b' }

type Placed = BrainNode & { x: number; y: number }

// A fixed, readable layout: topics on a ring around you, their facts in an arc beyond each topic,
// files near the facts they taught Ava, people on the outer ring.
function layout(map: BrainMap): Map<string, Placed> {
  const out = new Map<string, Placed>()
  const owner = map.nodes.find((n) => n.type === 'owner')
  if (owner) out.set(owner.id, { ...owner, x: C.x, y: C.y })
  const topics = map.nodes.filter((n) => n.type === 'topic')
  const facts = map.nodes.filter((n) => n.type === 'fact' && !n.superseded)
  const sector = (2 * Math.PI) / Math.max(1, topics.length)
  const angleOf = new Map<string, number>()
  topics.forEach((t, i) => {
    const a = -Math.PI / 2 + i * sector
    angleOf.set(t.id, a)
    out.set(t.id, { ...t, x: C.x + 120 * Math.cos(a), y: C.y + 120 * Math.sin(a) })
  })
  topics.forEach((t) => {
    const mine = facts.filter((f) => `topic:${f.category}` === t.id)
    const a0 = angleOf.get(t.id) ?? 0
    mine.forEach((f, j) => {
      const spread = sector * 0.78
      const a = a0 + (mine.length === 1 ? 0 : (j / (mine.length - 1) - 0.5) * spread)
      const r = 205 + (j % 2) * 42 + Math.floor(j / 12) * 40
      out.set(f.id, { ...f, x: C.x + r * Math.cos(a), y: C.y + r * Math.sin(a) })
    })
  })
  const sources = map.nodes.filter((n) => n.type === 'source')
  sources.forEach((s) => {
    const mine = map.edges.filter((e) => e.from === s.id).map((e) => out.get(e.to)).filter(Boolean) as Placed[]
    const a = mine.length ? Math.atan2(mine.reduce((v, p) => v + p.y - C.y, 0), mine.reduce((v, p) => v + p.x - C.x, 0)) : 0
    out.set(s.id, { ...s, x: C.x + 318 * Math.cos(a), y: C.y + 318 * Math.sin(a) })
  })
  const people = map.nodes.filter((n) => n.type === 'person')
  people.forEach((p, i) => {
    const a = Math.PI / 2 + (i + 0.5) * ((2 * Math.PI) / Math.max(1, people.length))
    out.set(p.id, { ...p, x: C.x + 330 * Math.cos(a), y: C.y + 330 * Math.sin(a) })
  })
  // A replaced note sits just outside the one that replaced it, so the strike-through reads as "was this, now that".
  map.nodes
    .filter((n) => n.superseded)
    .forEach((old, i) => {
      const next = (old.replaced_by && out.get(old.replaced_by)) || undefined
      const a = next ? Math.atan2(next.y - C.y, next.x - C.x) : 0
      const nudge = 0.1 * (i % 2 === 0 ? 1 : -1)
      const r = (next ? Math.hypot(next.x - C.x, next.y - C.y) : 240) + 26
      out.set(old.id, { ...old, x: C.x + r * Math.cos(a + nudge), y: C.y + r * Math.sin(a + nudge) })
    })
  return out
}

// A free-floating layout: the same nodes pulled together by their links and pushed apart from each other
// (a small force simulation, run once and the same every time). You stay in the middle.
function force(map: BrainMap, start: Map<string, Placed>): Map<string, Placed> {
  const nodes = [...start.values()].map((n, i) => ({ ...n, x: n.x + ((i * 7) % 11) - 5, y: n.y + ((i * 5) % 13) - 6, vx: 0, vy: 0 }))
  const at = new Map(nodes.map((n) => [n.id, n]))
  const links = map.edges.flatMap((e) => {
    const a = at.get(e.from)
    const b = at.get(e.to)
    return a && b ? [{ a, b, rest: e.kind === 'replaced_by' ? 40 : e.kind === 'about' ? 95 : 130 }] : []
  })
  const steps = 260
  for (let t = 0; t < steps; t++) {
    const cool = 1 - t / steps
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i]
        const b = nodes[j]
        let dx = a.x - b.x
        let dy = a.y - b.y
        const d2 = Math.max(dx * dx + dy * dy, 25)
        const d = Math.sqrt(d2)
        const f = 7000 / d2
        dx /= d
        dy /= d
        a.vx += dx * f
        a.vy += dy * f
        b.vx -= dx * f
        b.vy -= dy * f
      }
    }
    for (const { a, b, rest } of links) {
      const dx = b.x - a.x
      const dy = b.y - a.y
      const d = Math.max(Math.hypot(dx, dy), 1)
      const f = (d - rest) * 0.04
      a.vx += (dx / d) * f
      a.vy += (dy / d) * f
      b.vx -= (dx / d) * f
      b.vy -= (dy / d) * f
    }
    for (const n of nodes) {
      n.vx += (C.x - n.x) * 0.003
      n.vy += (C.y - n.y) * 0.003
      n.x += Math.max(-12, Math.min(12, n.vx)) * cool
      n.y += Math.max(-12, Math.min(12, n.vy)) * cool
      n.vx *= 0.6
      n.vy *= 0.6
      n.x = Math.max(24, Math.min(W - 24, n.x))
      n.y = Math.max(24, Math.min(H - 24, n.y))
      if (n.type === 'owner') {
        n.x = C.x
        n.y = C.y
      }
    }
  }
  return new Map(nodes.map(({ vx: _vx, vy: _vy, ...n }) => [n.id, n as Placed]))
}

function Dot({ n, selected, onPick }: { n: Placed; selected: boolean; onPick: () => void }) {
  const r = n.type === 'owner' ? 26 : n.type === 'topic' ? 16 : n.type === 'fact' ? 8 : 11
  const fill =
    n.type === 'owner' ? '#2b2250' : n.type === 'topic' ? '#ede9fe' : n.type === 'person' ? '#fce7f3' : n.type === 'source' ? '#e0f2fe' : COLOUR[n.origin ?? 'you']
  const stroke = n.type === 'topic' ? '#7c5cff' : n.type === 'person' ? '#db2777' : n.type === 'source' ? '#0ea5e9' : 'transparent'
  const text = n.type === 'topic' ? (CATEGORY_LABEL[n.label] ?? n.label) : n.type === 'fact' ? '' : n.label
  const name =
    n.type === 'fact'
      ? n.superseded
        ? `Replaced note: ${n.label}`
        : `${n.locked ? 'Locked note' : 'Note'}: ${n.label}`
      : `${n.type}: ${text}`
  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={name}
      aria-pressed={selected}
      onClick={onPick}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onPick()
        }
      }}
      className="cursor-pointer outline-none [&:focus-visible>circle]:stroke-[#2b2250] [&:focus-visible>circle]:stroke-[3]"
      opacity={n.type === 'fact' ? n.freshness : 1}
    >
      <circle
        cx={n.x}
        cy={n.y}
        r={selected ? r + 4 : r}
        fill={n.superseded ? '#ffffff' : fill}
        stroke={selected || n.locked ? '#2b2250' : n.superseded ? '#9a93c4' : stroke}
        strokeWidth={selected ? 3 : n.locked ? 2.5 : 1.5}
        strokeDasharray={n.superseded ? '3 2' : undefined}
      />
      {n.superseded && <line x1={n.x - r - 2} y1={n.y + r + 2} x2={n.x + r + 2} y2={n.y - r - 2} stroke="#b4235a" strokeWidth="2" />}
      {n.locked && <circle cx={n.x} cy={n.y} r={r + 5} fill="none" stroke="#2b2250" strokeWidth="1" strokeDasharray="2 2" />}
      {text && (
        <text x={n.x} y={n.type === 'owner' ? n.y + 4 : n.y + r + 13} textAnchor="middle" fontSize={n.type === 'owner' ? 10 : 11} fill={n.type === 'owner' ? '#fff' : '#3b3566'}>
          {n.type === 'owner' ? 'You' : text.length > 22 ? `${text.slice(0, 21)}…` : text}
        </text>
      )}
    </g>
  )
}

export function BrainPage() {
  const { me } = useSession()
  const name = me?.agent.name ?? 'Ava'
  const { data, error, reload } = useAppData<BrainMap>('/brain')
  const [picked, setPicked] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [confirmForget, setConfirmForget] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  // The map shows only after the data arrives in the browser, so reading the saved choice here cannot change
  // what the server rendered.
  const [mode, setMode] = useState<'tidy' | 'free'>(() => {
    try {
      return typeof window !== 'undefined' && window.localStorage.getItem('brain-layout') === 'free' ? 'free' : 'tidy'
    } catch {
      return 'tidy' // storage can be blocked; tidy is the default
    }
  })
  function chooseMode(m: 'tidy' | 'free') {
    setMode(m)
    try {
      window.localStorage.setItem('brain-layout', m)
    } catch {
      // not remembered, still applied
    }
  }

  const placed = useMemo(() => {
    if (!data) return new Map<string, Placed>()
    const tidy = layout(data)
    return mode === 'free' ? force(data, tidy) : tidy
  }, [data, mode])
  const selected = picked ? placed.get(picked) : undefined
  const facts = data?.nodes.filter((n) => n.type === 'fact' && !n.superseded).length ?? 0

  function pick(id: string) {
    setPicked(id)
    setEditing(false)
    setConfirmForget(false)
    setActionError(null)
  }

  async function act(path: string, body?: unknown, keep?: string) {
    setActionError(null)
    try {
      await call(path, { method: 'POST', json: body })
      setPicked(keep ?? null)
      setEditing(false)
      setConfirmForget(false)
      await reload()
    } catch (e) {
      setActionError(errorMessage(e))
    }
  }

  const factId = selected?.id.replace('fact:', '')
  const source = selected?.type === 'fact' ? (selected.document ? `from ${selected.document}` : selected.origin === 'you' ? 'You told Ava' : (data?.origins[selected.origin ?? 'you']?.label ?? '')) : ''
  const links = selected ? (data?.edges ?? []).filter((e) => e.from === selected.id || e.to === selected.id).length : 0

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-4 py-6 sm:px-6 lg:py-8">
        <Link href="/dashboard/notebook" className="inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
          <ArrowLeft className="size-4" aria-hidden /> Notebook
        </Link>
        <h1 className="mt-2 text-3xl font-medium text-ink tight">Glass brain</h1>
        <p className="mt-1 text-body">
          What {name} believes about you, and why. Pick anything to see where it came from. Only you can see this.
        </p>

        {error && !data && (
          <div className="mt-4">
            <LoadError what="the map" error={error} onRetry={reload} />
          </div>
        )}
        {!data && !error && <Skeleton className="mt-6 h-96 w-full rounded-xl" />}

        {data && (
          <div className="mt-6 grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
            <div className="rounded-xl border border-line bg-white p-2">
              <div className="flex flex-wrap items-center gap-2 px-2 pt-1 text-xs text-muted" role="group" aria-label="Layout">
                <span>Layout</span>
                <Button size="sm" variant={mode === 'tidy' ? 'default' : 'outline'} aria-pressed={mode === 'tidy'} onClick={() => chooseMode('tidy')}>
                  Tidy
                </Button>
                <Button size="sm" variant={mode === 'free' ? 'default' : 'outline'} aria-pressed={mode === 'free'} onClick={() => chooseMode('free')}>
                  Free-floating
                </Button>
              </div>
              {facts === 0 ? (
                <p className="p-10 text-center text-sm text-muted">
                  Nothing to map yet. Tell {name} &quot;remember that…&quot; in chat and it will appear here.
                </p>
              ) : (
                <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="group" aria-label={`Map of what ${name} knows`}>
                  <g stroke="#d9d3f5" strokeWidth="1">
                    {data.edges.map((e) => {
                      const a = placed.get(e.from)
                      const b = placed.get(e.to)
                      if (!a || !b) return null
                      const hot = selected && (e.from === selected.id || e.to === selected.id)
                      return (
                        <line key={`${e.from}>${e.to}>${e.kind}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={hot ? '#7c5cff' : e.kind === 'replaced_by' ? '#b4235a' : undefined} strokeWidth={hot ? 2 : 1} strokeDasharray={e.kind === 'mentions' || e.kind === 'replaced_by' ? '4 3' : undefined} />
                      )
                    })}
                  </g>
                  {[...placed.values()].map((n) => (
                    <Dot key={n.id} n={n} selected={n.id === picked} onPick={() => pick(n.id)} />
                  ))}
                </svg>
              )}
              <ul className="flex flex-wrap gap-x-4 gap-y-1 px-3 pb-2 text-xs text-muted" aria-label="Colour key">
                {Object.entries(data.origins).map(([k, o]) => (
                  <li key={k} className="flex items-center gap-1.5">
                    <span className="inline-block size-2.5 rounded-full" style={{ background: COLOUR[k] }} aria-hidden />
                    {o.label} ({o.count})
                  </li>
                ))}
                <li>Paler means older.</li>
                <li>
                  <span className="mr-1 inline-block h-0.5 w-3 -rotate-45 bg-[#b4235a] align-middle" aria-hidden />
                  Struck through means replaced.
                </li>
                <li>A dashed ring means locked: {name} will not change it.</li>
              </ul>
            </div>

            <aside aria-label="Details" className="space-y-3">
              <div className="rounded-xl border border-line bg-white p-4 text-sm">
                {!selected && <p className="text-muted">Pick a dot to see what it is, where {name} learned it, and to correct or forget it.</p>}
                {selected && selected.type === 'fact' && selected.superseded && (
                  <>
                    <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
                      <Badge variant="outline">Replaced</Badge>
                      <span>
                        {selected.replaced_at ? new Date(selected.replaced_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : ''}
                      </span>
                    </div>
                    <p className="mt-2 whitespace-pre-wrap text-muted line-through [overflow-wrap:anywhere]">{selected.label}</p>
                    {selected.replaced_by && placed.get(selected.replaced_by) && (
                      <div className="mt-2">
                        <p className="text-xs text-muted">{name} uses this instead:</p>
                        <p className="mt-0.5 whitespace-pre-wrap text-ink [overflow-wrap:anywhere]">{placed.get(selected.replaced_by)?.label}</p>
                        <Button size="sm" variant="outline" className="mt-2" onClick={() => pick(selected.replaced_by ?? '')}>
                          Show the current note
                        </Button>
                      </div>
                    )}
                  </>
                )}
                {selected && selected.type === 'fact' && !selected.superseded && (
                  <>
                    <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
                      <Badge variant="secondary" className="text-violet-700">
                        {CATEGORY_LABEL[selected.category ?? 'other'] ?? 'Note'}
                      </Badge>
                      <span>{source}</span>
                      {selected.locked && <Badge variant="outline">Locked</Badge>}
                    </div>
                    {editing ? (
                      <div className="mt-2">
                        <label htmlFor="brain-fix" className="sr-only">
                          What is true
                        </label>
                        <Textarea id="brain-fix" value={draft} onChange={(e) => setDraft(e.target.value)} rows={3} maxLength={2000} />
                      </div>
                    ) : (
                      <p className="mt-2 whitespace-pre-wrap text-ink [overflow-wrap:anywhere]">{selected.label}</p>
                    )}
                    {selected.locked && !editing && (
                      <p className="mt-1 text-xs text-muted">
                        Locked: {name} won&apos;t merge, retire or fade this. A newer claim waits for you. You can still correct or forget it.
                      </p>
                    )}
                    <dl className="mt-2 text-xs text-muted">
                      <div>Learned {selected.learned ? new Date(selected.learned).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : ''}</div>
                      {(selected.confidence ?? 1) < 1 && <div>Confidence {Math.round((selected.confidence ?? 1) * 100)}%</div>}
                      <div>{links} connection{links === 1 ? '' : 's'}</div>
                    </dl>
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      {editing ? (
                        <>
                          <Button size="sm" disabled={!draft.trim()} onClick={() => void act(`/brain/facts/${factId}/correct`, { text: draft })}>
                            Save correction
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
                            Cancel
                          </Button>
                        </>
                      ) : confirmForget ? (
                        <>
                          <span className="text-xs text-muted">{name} won&apos;t use this again.</span>
                          <Button size="sm" variant="destructive" onClick={() => void act(`/notebook/${factId}/forget`)}>
                            Forget
                          </Button>
                          <Button size="sm" variant="ghost" autoFocus onClick={() => setConfirmForget(false)}>
                            Keep
                          </Button>
                        </>
                      ) : (
                        <>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => {
                              setDraft(selected.label)
                              setEditing(true)
                            }}
                          >
                            Correct this
                          </Button>
                          <Button size="sm" variant="outline" onClick={() => void act(`/brain/facts/${factId}/${selected.locked ? 'unlock' : 'lock'}`, undefined, selected.id)}>
                            {selected.locked ? 'Unlock' : 'Lock this'}
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => setConfirmForget(true)}>
                            Forget this
                          </Button>
                        </>
                      )}
                    </div>
                  </>
                )}
                {selected && selected.type === 'topic' && (
                  <p>
                    <span className="font-medium text-ink">{CATEGORY_LABEL[selected.label] ?? selected.label}</span>
                    <span className="text-muted"> · {links - 1} {links - 1 === 1 ? 'note' : 'notes'}</span>
                  </p>
                )}
                {selected && selected.type === 'person' && (
                  <p>
                    <span className="font-medium text-ink">{selected.label}</span>
                    <span className="text-muted"> · {selected.relationship}{selected.vip ? ' · VIP' : ''}</span>
                  </p>
                )}
                {selected && selected.type === 'source' && (
                  <p>
                    <span className="font-medium text-ink [overflow-wrap:anywhere]">{selected.label}</span>
                    <span className="text-muted"> · a {selected.kind} you shared · {links} {links === 1 ? 'note' : 'notes'} learned from it</span>
                  </p>
                )}
                {selected && selected.type === 'owner' && <p className="text-muted">That is you. Everything here is about you.</p>}
                {actionError && (
                  <div className="mt-2">
                    <ActionError action="change that" error={actionError} />
                  </div>
                )}
              </div>

              <div className="rounded-xl border border-line bg-white p-4 text-sm">
                <h2 className="font-medium text-ink">Forgotten</h2>
                {data.forgotten.length === 0 ? (
                  <p className="mt-1 text-xs text-muted">Nothing forgotten yet.</p>
                ) : (
                  <ul className="mt-2 space-y-1.5 text-xs text-muted">
                    {data.forgotten.slice(0, 8).map((f) => (
                      <li key={f.id} className="line-through [overflow-wrap:anywhere]">
                        {f.text}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <Button asChild size="sm" variant="outline" className="w-full">
                <a href={`${API_URL}/app/brain/share.svg`} download="shape-of-memory.svg">
                  <Download className="size-4" aria-hidden /> Download a shareable picture
                </a>
              </Button>
              <p className="text-xs text-muted">Counts and shapes only. No names, notes or file names.</p>
            </aside>
          </div>
        )}
        {data?.truncated && <p className="mt-3 text-xs text-muted">Showing the newest 200 notes.</p>}
      </div>
    </div>
  )
}
