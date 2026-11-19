'use client'

import { Check, Clock, Pencil, Trash2, X } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Notice } from '@/components/site'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { errorMessage } from '@/lib/action'
import { call } from '@/lib/api'
import { useResource } from '@/lib/use-resource'
import { useSession } from '@/lib/session-context'
import { instant, localValue } from '@/features/dashboard/promises/time'

type PromiseRow = {
  id: string
  debtor: 'owner' | 'ava'
  who: string
  provider: string
  text: string
  due_at: string
  due_text: string
  state: 'proposed' | 'open' | 'overdue' | 'escalated' | 'done' | 'cancelled'
  source: string
  closed_by: string
}
type Page = { items: PromiseRow[]; next_cursor: string | null; has_more: boolean }

const POLL_MS = 20_000
const LATE = ['overdue', 'escalated']

export function PromisesPage() {
  const { me } = useSession()
  const name = me?.agent.name ?? 'Ava'
  const [view, setView] = useState<'active' | 'closed'>('active')
  const { data, error, reload } = useResource<Page>(`/commitments?view=${view}`, { poll: POLL_MS })
  const [text, setText] = useState('')
  const [who, setWho] = useState('')
  const [due, setDue] = useState('')
  const [editing, setEditing] = useState<string | null>(null)
  const [editText, setEditText] = useState('')
  const [editDue, setEditDue] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  async function act(fn: () => Promise<unknown>) {
    setBusy(true)
    setProblem(null)
    try {
      await fn()
    } catch (e) {
      setProblem(errorMessage(e))
    } finally {
      await reload() // also after a failure: "already closed" means this list was stale
      setBusy(false)
    }
  }

  function add(e: FormEvent) {
    e.preventDefault()
    if (!text.trim() || !due) return
    void act(async () => {
      await call('/commitments', { method: 'POST', json: { text: text.trim(), who: who.trim(), due_at: instant(due) } })
      setText('')
      setWho('')
      setDue('')
    })
  }

  function save(p: PromiseRow) {
    void act(async () => {
      await call(`/commitments/${p.id}`, { method: 'PATCH', json: { text: editText.trim(), due_at: instant(editDue) } })
      setEditing(null)
    })
  }

  const items = data?.items

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-6 lg:py-8">
        <h1 className="text-3xl font-medium text-ink tight">Promises</h1>
        <p className="mt-1 text-body">
          What you or {name} told someone would happen, and by when. When you approve a message that says &ldquo;I&rsquo;ll
          reply in an hour&rdquo;, it lands here, {name} reminds you shortly before it is due, and it closes when you write
          to them or mark it done. Nothing here is ever shared with the people it is about.
        </p>

        <form onSubmit={add} className="mt-6 grid gap-2 sm:grid-cols-[1fr_9rem_13rem_auto]">
          <label htmlFor="new-promise" className="sr-only">
            What was promised
          </label>
          <Input id="new-promise" value={text} onChange={(e) => setText(e.target.value)} placeholder="Send Rahul the deck" maxLength={300} />
          <label htmlFor="new-promise-who" className="sr-only">
            To whom
          </label>
          <Input id="new-promise-who" value={who} onChange={(e) => setWho(e.target.value)} placeholder="To whom" maxLength={60} />
          <label htmlFor="new-promise-due" className="sr-only">
            Due
          </label>
          <Input id="new-promise-due" type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} />
          <Button type="submit" disabled={busy || !text.trim() || !due}>
            Add promise
          </Button>
        </form>

        <div className="mt-6 flex gap-1.5" role="tablist" aria-label="Which promises">
          {(['active', 'closed'] as const).map((v) => (
            <Button key={v} role="tab" aria-selected={view === v} size="sm" variant={view === v ? 'default' : 'ghost'} onClick={() => setView(v)}>
              {v === 'active' ? 'To do' : 'Kept or cancelled'}
            </Button>
          ))}
        </div>

        {(error || problem) && (
          <div className="mt-4">
            <Notice tone="error">{problem ?? error}</Notice>
          </div>
        )}

        <ul className="mt-4 space-y-2" aria-label={view === 'active' ? 'Promises still to do' : 'Closed promises'}>
          {!items &&
            [0, 1].map((i) => (
              <li key={i} className="rounded-xl border border-line bg-white p-4">
                <Skeleton className="h-4 w-2/3" />
              </li>
            ))}
          {items?.length === 0 && (
            <li className="rounded-xl border border-dashed border-line p-6 text-center text-sm text-muted">
              {view === 'active' ? 'No open promises.' : 'Nothing closed yet.'}
            </li>
          )}
          {items?.map((p) => (
            <li key={p.id} className="flex flex-col gap-3 rounded-xl border border-line bg-white p-4 sm:flex-row sm:items-center">
              <div className="min-w-0 flex-1">
                {editing === p.id ? (
                  <div className="grid gap-2 sm:grid-cols-[1fr_13rem]">
                    <Input aria-label="What was promised" value={editText} onChange={(e) => setEditText(e.target.value)} maxLength={300} />
                    <Input aria-label="Due" type="datetime-local" value={editDue} onChange={(e) => setEditDue(e.target.value)} />
                  </div>
                ) : (
                  <>
                    <p className="font-medium [overflow-wrap:anywhere] text-ink">
                      {p.debtor === 'ava' ? `${name} owes ` : 'You told '}
                      {p.who || 'someone'}
                      {': '}
                      <span className="font-normal">{p.text}</span>
                    </p>
                    <p className="mt-0.5 flex flex-wrap items-center gap-2 text-sm text-muted">
                      <Clock className="size-3.5" aria-hidden />
                      {p.due_text}
                      {LATE.includes(p.state) && <Badge variant="destructive">Overdue</Badge>}
                      {p.state === 'done' && <Badge variant="secondary">{p.closed_by === 'reply' ? 'You replied' : 'Done'}</Badge>}
                      {p.state === 'cancelled' && <Badge variant="outline">Cancelled</Badge>}
                      {p.source === 'relay' && <Badge variant="outline">From a message you approved</Badge>}
                    </p>
                  </>
                )}
              </div>
              <div className="flex shrink-0 flex-wrap gap-1.5">
                {view === 'active' && editing !== p.id && (
                  <>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      aria-label={`Edit "${p.text}"`}
                      onClick={() => {
                        setEditing(p.id)
                        setEditText(p.text)
                        setEditDue(localValue(p.due_at))
                      }}
                    >
                      <Pencil aria-hidden />
                    </Button>
                    <Button size="sm" variant="outline" disabled={busy} aria-label={`Cancel "${p.text}"`} onClick={() => void act(() => call(`/commitments/${p.id}/cancel`, { method: 'POST' }))}>
                      <X aria-hidden />
                    </Button>
                    <Button size="sm" disabled={busy} aria-label={`Mark "${p.text}" done`} onClick={() => void act(() => call(`/commitments/${p.id}/done`, { method: 'POST' }))}>
                      <Check aria-hidden /> Done
                    </Button>
                  </>
                )}
                {editing === p.id && (
                  <>
                    <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
                      Not now
                    </Button>
                    <Button size="sm" disabled={busy || !editText.trim() || !editDue} onClick={() => save(p)}>
                      Save
                    </Button>
                  </>
                )}
                {view === 'closed' && (
                  <Button size="sm" variant="ghost" disabled={busy} aria-label={`Remove "${p.text}" from the list`} onClick={() => void act(() => call(`/commitments/${p.id}`, { method: 'DELETE' }))}>
                    <Trash2 aria-hidden /> Remove
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
        {view === 'closed' && <p className="mt-3 text-xs text-muted">Removing hides a promise from this list; the record is kept.</p>}
      </div>
    </div>
  )
}
