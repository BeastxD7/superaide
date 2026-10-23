'use client'

import { AlarmClock, Check, Clock, X } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Notice } from '@/components/site'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { useAppData } from '@/features/dashboard/data'
import { call, type Reminder } from '@/lib/api'
import { useSession } from '@/lib/session-context'
import { errorMessage } from '@/lib/action'

const SNOOZES = [
  { label: '1 hour', minutes: 60 },
  { label: 'Tomorrow', minutes: 60 * 24 },
]

export function RemindersPage() {
  const { me } = useSession()
  const name = me?.agent.name ?? 'Ava'
  const { data, error, reload } = useAppData<{ timezone: string; reminders: Reminder[] }>('/reminders')
  const [text, setText] = useState('')
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
      // Read again even when it failed: "already cancelled" means this list was stale, and the reminder it
      // still shows can never be acted on (#297).
      await reload()
      setBusy(false)
    }
  }

  function add(e: FormEvent) {
    e.preventDefault()
    const t = text.trim()
    if (!t) return
    void act(async () => {
      await call('/reminders', { method: 'POST', json: { text: t } })
      setText('')
    })
  }

  const reminders = data?.reminders

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-6 lg:py-8">
        <h1 className="text-3xl font-medium text-ink tight">Reminders</h1>
        <p className="mt-1 text-body">
          {name} brings these back at the right time, here in the app and on Telegram if you chose it. You can also just
          tell her in chat: &ldquo;remind me to call Priya tomorrow at 5&rdquo;.
        </p>

        <form onSubmit={add} className="mt-6 flex flex-col gap-2 sm:flex-row">
          <label htmlFor="new-reminder" className="sr-only">
            New reminder
          </label>
          <Input
            id="new-reminder"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Call the bank tomorrow at 10am"
            maxLength={500}
            className="flex-1"
          />
          <Button type="submit" disabled={busy || !text.trim()}>
            <AlarmClock aria-hidden /> Add reminder
          </Button>
        </form>
        {data?.timezone && <p className="mt-1.5 text-xs text-muted">Times are in {data.timezone}.</p>}

        {(error || problem) && (
          <div className="mt-4">
            <Notice tone="error">{problem ?? error}</Notice>
          </div>
        )}

        <ul className="mt-6 space-y-2" aria-label="Upcoming reminders">
          {!reminders &&
            [0, 1].map((i) => (
              <li key={i} className="rounded-xl border border-line bg-white p-4">
                <Skeleton className="h-4 w-2/3" />
              </li>
            ))}
          {reminders?.length === 0 && (
            <li className="rounded-xl border border-dashed border-line p-6 text-center text-sm text-muted">
              Nothing coming up. Add one above, or ask {name} in chat.
            </li>
          )}
          {reminders?.map((r) => (
            <li key={r.id} className="flex flex-col gap-3 rounded-xl border border-line bg-white p-4 sm:flex-row sm:items-center">
              <div className="min-w-0 flex-1">
                <p className="font-medium [overflow-wrap:anywhere] text-ink">{r.text}</p>
                <p className="mt-0.5 flex flex-wrap items-center gap-2 text-sm text-muted">
                  <Clock className="size-3.5" aria-hidden />
                  {r.due_text}
                  {r.delivered_at && <Badge variant="secondary">Sent</Badge>}
                  {r.kind === 'follow_up' && <Badge variant="outline">Follow-up</Badge>}
                </p>
              </div>
              <div className="flex shrink-0 flex-wrap gap-1.5">
                {SNOOZES.map((s) => (
                  <Button
                    key={s.minutes}
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    aria-label={`Snooze "${r.text}" for ${s.label.toLowerCase()}`}
                    onClick={() => void act(() => call(`/reminders/${r.id}/snooze`, { method: 'POST', json: { minutes: s.minutes } }))}
                  >
                    {s.label}
                  </Button>
                ))}
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  aria-label={`Cancel "${r.text}"`}
                  onClick={() => void act(() => call(`/reminders/${r.id}/cancel`, { method: 'POST' }))}
                >
                  <X aria-hidden />
                </Button>
                <Button
                  size="sm"
                  disabled={busy}
                  aria-label={`Mark "${r.text}" done`}
                  onClick={() => void act(() => call(`/reminders/${r.id}/done`, { method: 'POST' }))}
                >
                  <Check aria-hidden /> Done
                </Button>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
