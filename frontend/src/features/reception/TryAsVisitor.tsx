'use client'

import { useState, type FormEvent } from 'react'
import { Notice } from '@/components/site'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { call, type PreviewResult, type PreviewScript, type PreviewTurn } from '@/lib/api'
import { errorMessage } from '@/lib/action'

type Line = { role: 'visitor' | 'ava'; text: string }
type Channel = 'telegram' | 'slack'

const STANDARD: PreviewScript = {
  id: 'meera',
  title: 'Meera, a first-time visitor',
  lines: [],
}

function replyText(t: PreviewTurn): string {
  if (t.reply) return t.reply
  return t.mode === 'error' ? '(the turn failed: a visitor would get a short apology)' : '(no reply: over the visitor’s limit, nothing more is sent)'
}

// Settings → "Try as a visitor": chat with your own receptionist the way someone Ava doesn’t know would see it. It runs
// the real receptionist in a sandbox (backend reception/sandbox.py): nothing is sent, nothing is stored as a visitor’s
// message, no notification reaches you (it is shown as text), and the model’s cost is counted and shown.
export function TryAsVisitor({ agentName }: { agentName: string }) {
  const [channel, setChannel] = useState<Channel>('telegram')
  const [chat, setChat] = useState<Line[]>([])
  const [draft, setDraft] = useState('')
  const [last, setLast] = useState<PreviewResult | null>(null)
  const [spend, setSpend] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [own, setOwn] = useState('')
  const [runs, setRuns] = useState<{ label: string; replies: string[]; lines: string[] }[]>([])

  async function run(messages: string[], history: Line[]): Promise<PreviewResult | null> {
    setBusy(true)
    setError(null)
    try {
      const out = await call<PreviewResult>('/receptionist/preview', { method: 'POST', json: { channel, history, messages } })
      setSpend((n) => n + Number(out.spend_usd))
      setLast(out)
      return out
    } catch (e) {
      setError(errorMessage(e))
      return null
    } finally {
      setBusy(false)
    }
  }

  async function send(e: FormEvent) {
    e.preventDefault()
    const text = draft.trim()
    if (!text || busy) return
    const out = await run([text], chat)
    if (!out) return
    setDraft('')
    setChat([...chat, { role: 'visitor', text }, ...out.turns.map((t) => ({ role: 'ava' as const, text: replyText(t) }))])
  }

  async function replay(label: string, lines: string[]) {
    if (!lines.length || busy) return
    const out = await run(lines, [])
    if (!out) return
    setChat(out.turns.flatMap((t) => [{ role: 'visitor' as const, text: t.said }, { role: 'ava' as const, text: replyText(t) }]))
    setRuns((r) => [...r.slice(-1), { label, lines, replies: out.turns.map(replyText) }])
  }

  async function replayStandard() {
    try {
      const { scripts } = await call<{ scripts: PreviewScript[] }>('/receptionist/preview/scripts')
      const script = scripts[0] ?? STANDARD
      await replay(script.title, script.lines)
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  const turn = last?.turns[last.turns.length - 1]
  const [before, after] = runs.length === 2 ? runs : [null, null]

  return (
    <Card id="try-as-visitor" data-testid="try-as-visitor">
      <CardHeader>
        <CardTitle>Try as a visitor</CardTitle>
        <CardDescription>
          Write to {agentName} as someone she doesn’t know would, and see exactly how she answers, what she would pass on and
          what you would be told. Change a setting above and replay the same lines to compare.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div role="status" data-testid="preview-banner" className="rounded-md bg-lilac-100 px-3 py-2 text-sm font-medium text-ink">
          Preview. Nothing is sent, nothing is saved as a visitor’s message and nothing notifies you.
        </div>
        {error && <Notice tone="error">{error}</Notice>}

        <div className="grid gap-4 md:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-sm">
              <Label htmlFor="preview-channel">As a visitor on</Label>
              <select
                id="preview-channel"
                className="rounded-md border bg-white px-2 py-1"
                value={channel}
                disabled={busy}
                onChange={(e) => {
                  setChannel(e.target.value as Channel)
                  setChat([])
                  setLast(null)
                }}
              >
                <option value="telegram">Telegram</option>
                <option value="slack">Slack</option>
              </select>
              <Button type="button" variant="ghost" size="sm" disabled={busy || !chat.length} onClick={() => { setChat([]); setLast(null) }}>
                Start over
              </Button>
            </div>
            <ol aria-label="Preview conversation" data-testid="preview-chat" className="min-h-24 space-y-2 rounded-lg border border-line/70 p-3 text-sm">
              {chat.length === 0 && <li className="text-muted">Say hello as a visitor would.</li>}
              {chat.map((l, i) => (
                <li
                  key={i}
                  data-role={l.role}
                  className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-3 py-2 [overflow-wrap:anywhere] ${
                    l.role === 'visitor' ? 'ml-auto bg-violet-100 text-ink' : 'bg-mist text-ink'
                  }`}
                >
                  {l.text}
                </li>
              ))}
            </ol>
            <form onSubmit={send} className="flex gap-2">
              <Label htmlFor="preview-text" className="sr-only">
                What the visitor writes
              </Label>
              <input
                id="preview-text"
                className="min-w-0 flex-1 rounded-md border bg-white px-3 py-2 text-sm"
                value={draft}
                maxLength={1000}
                placeholder="Hi, who is this?"
                onChange={(e) => setDraft(e.target.value)}
              />
              <Button type="submit" size="sm" disabled={busy || !draft.trim()}>
                {busy ? 'Sending…' : 'Send'}
              </Button>
            </form>
          </div>

          <aside data-testid="preview-panel" aria-label="What would happen" className="space-y-3 rounded-lg bg-mist/60 p-3 text-sm text-ink">
            <h3 className="font-medium">What would happen</h3>
            {!last && <p className="text-muted">Send a line to see what she would store and what you would be told.</p>}
            {last && turn && (
              <>
                <section>
                  <h4 className="text-xs font-medium uppercase text-muted">Passed on to you</h4>
                  {turn.stored ? (
                    <p data-testid="preview-stored">
                      A message{turn.stored.topic ? ` about “${turn.stored.topic}”` : ''}
                      {turn.stored.name ? ` from ${turn.stored.name}` : ''}, ranked {turn.stored.rank}
                      {turn.stored.source === 'code' ? ' (kept as typed, no model)' : ''}.
                    </p>
                  ) : (
                    <p className="text-muted">Nothing yet.</p>
                  )}
                </section>
                <section>
                  <h4 className="text-xs font-medium uppercase text-muted">You would be told</h4>
                  {turn.notifications.length ? (
                    <ul data-testid="preview-notifications" className="list-disc pl-4">
                      {turn.notifications.map((n, i) => (
                        <li key={i}>{n.text}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-muted">No notification for this line.</p>
                  )}
                </section>
                <section>
                  <h4 className="text-xs font-medium uppercase text-muted">Settings used</h4>
                  <ul className="list-disc pl-4">
                    <li>Opening: “{last.used.greeting}”</li>
                    <li>Public profile: {last.used.profile_used ? 'used' : 'empty'}</li>
                    <li>Share card: {last.used.card_lines.length ? last.used.card_lines.join('; ') : 'nothing switched on'}</li>
                    <li>Public notes: {last.used.public_facts.length}</li>
                    <li>
                      Limits: {last.used.per_hour} an hour, {last.used.per_day} a day
                    </li>
                  </ul>
                </section>
                {last.skipped.length > 0 && (
                  <section>
                    <h4 className="text-xs font-medium uppercase text-muted">Not done in a preview</h4>
                    <ul className="list-disc pl-4">
                      {last.skipped.map((s) => (
                        <li key={s}>{s}</li>
                      ))}
                    </ul>
                  </section>
                )}
              </>
            )}
            <p data-testid="preview-spend" className="text-xs text-muted">
              Spent in previews this visit: US${spend.toFixed(4)}. It counts toward your limit.
            </p>
          </aside>
        </div>

        <div className="space-y-2 border-t border-line/70 pt-3">
          <h3 className="text-sm font-medium text-ink">Replay a script</h3>
          <div className="flex flex-wrap gap-2">
            <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void replayStandard()}>
              Replay Meera’s first visit
            </Button>
          </div>
          <Label htmlFor="preview-own">Or your own lines, one per line (up to 8)</Label>
          <Textarea id="preview-own" rows={3} value={own} onChange={(e) => setOwn(e.target.value)} placeholder="Is he free on Friday?" />
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={busy || !own.trim()}
            onClick={() => void replay('Your lines', own.split('\n').map((l) => l.trim()).filter(Boolean).slice(0, 8))}
          >
            Replay my lines
          </Button>
          {before && after && (
            <div data-testid="preview-compare" className="overflow-x-auto">
              <p className="mb-1 text-xs text-muted">Last two replays side by side. Changed answers are marked.</p>
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="text-xs text-muted">
                    <th className="w-1/3 pr-2">Visitor</th>
                    <th className="w-1/3 pr-2">Before</th>
                    <th className="w-1/3">Now</th>
                  </tr>
                </thead>
                <tbody>
                  {after.lines.map((line, i) => {
                    const changed = before.replies[i] !== after.replies[i]
                    return (
                      <tr key={i} data-changed={changed} className={`align-top ${changed ? 'bg-amber-50' : ''}`}>
                        <td className="pr-2 [overflow-wrap:anywhere]">{line}</td>
                        <td className="pr-2 whitespace-pre-wrap [overflow-wrap:anywhere]">{before.replies[i] ?? ''}</td>
                        <td className="whitespace-pre-wrap [overflow-wrap:anywhere]">
                          {changed && <span className="mr-1 font-medium">Changed.</span>}
                          {after.replies[i]}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
