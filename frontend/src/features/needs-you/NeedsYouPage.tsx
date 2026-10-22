'use client'

import { BellOff, Check, Clock, Plane, ShieldAlert, ThumbsDown, ThumbsUp } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Notice } from '@/components/site'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { AskRequests } from '@/features/ask/AskRequests'
import { CHANNEL_LABEL } from '@/features/dashboard/data'
import { useNeedsYou } from '@/features/needs-you/useNeedsYou'
import { AppError, call, type NeedsYouItem, type TriageFeedback, type TriageSettings, type TriageVerb } from '@/lib/api'
import { useSession } from '@/lib/session-context'

type Tab = 'needs_you' | 'handled' | 'held'

const TABS: { id: Tab; label: string; empty: string }[] = [
  { id: 'needs_you', label: 'Needs you', empty: 'Nothing needs you right now.' },
  { id: 'handled', label: 'Handled for you', empty: 'Nothing handled for you yet.' },
  { id: 'held', label: 'Held', empty: 'Nothing held for your digest.' },
]

const STATE_LABEL: Record<string, string> = {
  deferred: 'Waiting for a better moment',
  notifying: 'Telling you now',
  acked: 'Acknowledged',
  resolved: 'Done',
  expired: 'Not acknowledged',
}

function when(iso: string | null): string {
  if (!iso) return ''
  return new Date(iso).toLocaleString(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit' })
}

export function NeedsYouPage() {
  const { me } = useSession()
  const name = me?.agent.name ?? 'Ava'
  const { enabled, data, error, reload } = useNeedsYou(30_000)
  const [tab, setTab] = useState<Tab>('needs_you')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  async function act(fn: () => Promise<unknown>) {
    setBusy(true)
    setProblem(null)
    try {
      await fn()
      await reload()
    } catch (e) {
      setProblem(e instanceof AppError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const verb = (item: NeedsYouItem, v: TriageVerb) =>
    act(() => call(`/needs-you/${item.id}/action`, { method: 'POST', json: { verb: v, request_id: crypto.randomUUID() } }))
  const feedback = (item: NeedsYouItem, f: TriageFeedback) =>
    act(() => call(`/needs-you/${item.id}/feedback`, { method: 'POST', json: { feedback: f } }))

  if (enabled === false) {
    return (
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-6 lg:py-8">
          <h1 className="text-3xl font-medium text-ink tight">Needs you</h1>
          <p className="mt-2 text-body">This isn&rsquo;t turned on yet.</p>
        </div>
      </div>
    )
  }

  const items = data?.[tab]

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-4 py-6 sm:px-6 lg:py-8">
        <h1 className="text-3xl font-medium text-ink tight">Needs you</h1>
        <p className="mt-1 text-body">
          What {name} thinks you should see, what she answered, and what she kept for your digest. Opening this page
          never counts as acknowledging anything: press a button.
        </p>

        <AskRequests className="mt-6" />

        <div role="tablist" aria-label="Lists" className="mt-6 flex flex-wrap gap-1.5">
          {TABS.map((t) => (
            <Button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              size="sm"
              variant={tab === t.id ? 'default' : 'outline'}
              onClick={() => setTab(t.id)}
            >
              {t.label}
              {data && <Badge variant="secondary">{data.counts[t.id]}</Badge>}
            </Button>
          ))}
        </div>

        {(error || problem) && (
          <div className="mt-4 space-y-2">
            <Notice tone="error">{problem ?? error}</Notice>
            {error && !data && (
              <Button size="sm" variant="outline" onClick={() => void reload()}>
                Try again
              </Button>
            )}
          </div>
        )}

        <ul className="mt-4 space-y-2" aria-label={TABS.find((t) => t.id === tab)?.label}>
          {!items && !error &&
            [0, 1].map((i) => (
              <li key={i} className="rounded-xl border border-line bg-white p-4">
                <Skeleton className="h-4 w-2/3" />
              </li>
            ))}
          {items?.length === 0 && (
            <li className="rounded-xl border border-dashed border-line p-6 text-center text-sm text-muted">
              {TABS.find((t) => t.id === tab)?.empty}
            </li>
          )}
          {items?.map((item) => (
            <Item key={item.id} item={item} tab={tab} busy={busy} onVerb={verb} onFeedback={feedback} />
          ))}
        </ul>

        <SettingsPanel name={name} />
      </div>
    </div>
  )
}

function Item({
  item,
  tab,
  busy,
  onVerb,
  onFeedback,
}: {
  item: NeedsYouItem
  tab: Tab
  busy: boolean
  onVerb: (item: NeedsYouItem, v: TriageVerb) => void
  onFeedback: (item: NeedsYouItem, f: TriageFeedback) => void
}) {
  const esc = item.escalation
  const open = esc ? esc.state === 'notifying' || esc.state === 'deferred' : item.feedback === null
  return (
    <li className="rounded-xl border border-line bg-white p-4">
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-medium text-ink">{item.sender || 'Someone'}</p>
        {item.decision === 'verify' && (
          <Badge variant="destructive">
            <ShieldAlert aria-hidden /> Unverified request
          </Badge>
        )}
        {item.maybe_urgent && <Badge variant="outline">Maybe urgent</Badge>}
        {esc && <Badge variant="secondary">{STATE_LABEL[esc.state] ?? esc.state}</Badge>}
        <span className="ml-auto text-xs text-muted">{when(item.created_at)}</span>
      </div>
      <p className="mt-1 text-sm [overflow-wrap:anywhere] text-body">{item.reason}</p>
      {item.quotes.map((q) => (
        <blockquote key={q} className="mt-1 border-l-2 border-line pl-2 text-sm [overflow-wrap:anywhere] text-muted">
          {q}
        </blockquote>
      ))}
      {esc && esc.deliveries.length > 0 && (
        <p className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
          <Clock className="size-3.5" aria-hidden />
          {esc.deliveries.map((d) => `${CHANNEL_LABEL[d.provider] ?? d.provider} ${d.status}`).join(' · ')}
          {esc.acked_at && ` · acknowledged via ${CHANNEL_LABEL[esc.acked_via ?? ''] ?? esc.acked_via} ${when(esc.acked_at)}`}
        </p>
      )}

      <div className="mt-3 flex flex-wrap gap-1.5">
        {tab === 'needs_you' && open && (
          <>
            <Button size="sm" disabled={busy} onClick={() => onVerb(item, esc ? 'ack' : 'done')}>
              <Check aria-hidden /> {esc ? 'Acknowledge' : 'Seen it, thanks'}
            </Button>
            {esc && (
              <>
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => onVerb(item, 'snooze_30m')}>
                  Snooze 30 min
                </Button>
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => onVerb(item, 'snooze_2h')}>
                  Snooze 2 h
                </Button>
              </>
            )}
            <Button size="sm" variant="outline" disabled={busy} onClick={() => onVerb(item, 'not_urgent')}>
              <BellOff aria-hidden /> Not urgent
            </Button>
          </>
        )}
        {tab === 'needs_you' && esc?.state === 'acked' && (
          <Button size="sm" variant="outline" disabled={busy} onClick={() => onVerb(item, 'done')}>
            <Check aria-hidden /> Done
          </Button>
        )}
        {tab === 'held' && item.feedback !== 'should_have_told' && (
          <Button size="sm" variant="outline" disabled={busy} onClick={() => onFeedback(item, 'should_have_told')}>
            You should have told me
          </Button>
        )}
        {tab === 'handled' && item.feedback === null && (
          <>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => onFeedback(item, 'good_handle')}>
              <ThumbsUp aria-hidden /> Good
            </Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => onFeedback(item, 'wrong_handle')}>
              <ThumbsDown aria-hidden /> Wrong answer
            </Button>
          </>
        )}
        {item.feedback && <span className="self-center text-xs text-muted">Thanks, noted.</span>}
      </div>
    </li>
  )
}

function SettingsPanel({ name }: { name: string }) {
  const [settings, setSettings] = useState<TriageSettings | null>(null)
  const [topics, setTopics] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [loadedAt, setLoadedAt] = useState(0) // when the settings last came back: away/busy are compared to it

  useEffect(() => {
    call<TriageSettings>('/triage/settings')
      .then((s) => {
        setSettings(s)
        setLoadedAt(Date.now())
        setTopics(s.silenced_topics.join(', '))
      })
      .catch((e) => setProblem(e instanceof AppError ? e.message : String(e)))
  }, [])

  async function save(path: string, json: unknown, method: 'POST' | 'PUT' = 'POST') {
    setProblem(null)
    setSaved(false)
    try {
      const s = await call<TriageSettings>(path, { method, json })
      setSettings(s)
      setLoadedAt(Date.now())
      setSaved(true)
    } catch (e) {
      setProblem(e instanceof AppError ? e.message : String(e))
    }
  }

  if (!settings) return null
  const away = settings.away_until && Date.parse(settings.away_until) > loadedAt
  const busy = settings.busy_until && Date.parse(settings.busy_until) > loadedAt

  return (
    <section className="mt-10 rounded-xl border border-line bg-white p-4" aria-labelledby="triage-settings">
      <h2 id="triage-settings" className="text-lg font-medium text-ink">
        When {name} interrupts you
      </h2>
      {problem && (
        <div className="mt-3">
          <Notice tone="error">{problem}</Notice>
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        {away ? (
          <Button size="sm" variant="outline" onClick={() => void save('/triage/away', { until: null })}>
            <Plane aria-hidden /> Back from away (until {when(settings.away_until)})
          </Button>
        ) : (
          <Button
            size="sm"
            variant="outline"
            onClick={() => void save('/triage/away', { until: new Date(Date.now() + 3 * 864e5).toISOString() })}
          >
            <Plane aria-hidden /> Away for 3 days
          </Button>
        )}
        {busy ? (
          <Button size="sm" variant="outline" onClick={() => void save('/triage/busy', { minutes: 0 })}>
            Not busy any more
          </Button>
        ) : (
          <Button size="sm" variant="outline" onClick={() => void save('/triage/busy', { minutes: 90 })}>
            Busy for 90 min
          </Button>
        )}
      </div>

      <form
        className="mt-4 grid gap-3 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault()
          const form = new FormData(e.currentTarget)
          void save(
            '/triage/settings',
            {
              max_per_hour: Number(form.get('per_hour')),
              max_per_day: Number(form.get('per_day')),
              silenced_topics: topics
                .split(',')
                .map((t) => t.trim())
                .filter(Boolean),
            },
            'PUT',
          )
        }}
      >
        <label className="text-sm text-body">
          Interrupts per hour, at most
          <Input name="per_hour" type="number" min={0} max={60} defaultValue={settings.max_per_hour} />
        </label>
        <label className="text-sm text-body">
          Interrupts per day, at most
          <Input name="per_day" type="number" min={0} max={200} defaultValue={settings.max_per_day} />
        </label>
        <label className="text-sm text-body sm:col-span-2">
          Never interrupt me about (comma separated)
          <Input value={topics} onChange={(e) => setTopics(e.target.value)} placeholder="golf, newsletters" maxLength={2000} />
        </label>
        <div className="flex items-center gap-3 sm:col-span-2">
          <Button type="submit" size="sm">
            Save
          </Button>
          {saved && <span className="text-xs text-muted">Saved.</span>}
        </div>
      </form>
      <p className="mt-3 text-xs text-muted">
        Who may be told you&rsquo;re away: {settings.away_visibility}. These are starting values; change them any time.
      </p>
    </section>
  )
}
