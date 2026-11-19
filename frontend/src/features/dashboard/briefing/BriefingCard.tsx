'use client'

import { Handshake, MailQuestion } from 'lucide-react'
import { useState } from 'react'
import { Notice } from '@/components/site'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'
import { errorMessage } from '@/lib/action'
import { call } from '@/lib/api'
import { useResource } from '@/lib/use-resource'

type Draft = { id: string; text: string; adds: string[]; state: string; origin: string }
type Conversation = {
  who: string
  provider: string
  channel: string
  messages: number
  waited: string
  rank: string
  flags: string[]
  topic: string
  quote: string
  draft: Draft | null
}
type Overdue = { id: string; who: string; text: string; due_text: string }
export type Briefing = { show: boolean; away_minutes: number | null; conversations: Conversation[]; overdue: Overdue[] }

function away(minutes: number | null): string {
  if (minutes === null) return 'a while'
  if (minutes < 90) return `${minutes} minutes`
  const h = Math.round(minutes / 60)
  return h < 48 ? `${h} hours` : `${Math.round(h / 24)} days`
}

// "While you were away": shown on Home when the owner has been away a few hours and there is something to tell.
// Who wrote, what they need and what is overdue come from the server (decided in code). Each reply is a
// suggestion: nothing is sent until the owner taps Send, and she can change the words first.
export function BriefingCard({ name }: { name: string }) {
  const { data, reload } = useResource<Briefing>('/briefing')
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (!data || !data.show) return null

  async function act(fn: () => Promise<unknown>) {
    setBusy(true)
    setProblem(null)
    try {
      await fn()
    } catch (e) {
      setProblem(errorMessage(e))
    } finally {
      await reload()
      setBusy(false)
    }
  }

  const waiting = data.conversations.length
  return (
    <Card className="mt-6" data-testid="briefing-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MailQuestion className="size-5 text-violet-600" aria-hidden />
          While you were away
        </CardTitle>
        <CardDescription>
          You were away for about {away(data.away_minutes)}.{' '}
          {waiting > 0 && `${waiting} ${waiting === 1 ? 'person is' : 'people are'} waiting. `}
          {name} drafted a reply for each. Nothing is sent until you tap Send.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {problem && <Notice tone="error">{problem}</Notice>}
        <ul className="space-y-4" aria-label="People who wrote">
          {data.conversations.map((c, i) => {
            const d = c.draft
            const text = d ? (edits[d.id] ?? d.text) : ''
            return (
              <li key={`${c.who}-${i}`} className="rounded-xl border border-line p-4 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-ink">{c.who}</span>
                  <Badge variant="outline">{c.channel}</Badge>
                  <span className="text-body">
                    {c.messages} {c.messages === 1 ? 'message' : 'messages'}, waiting {c.waited}
                  </span>
                  {c.rank === 'urgent' && <Badge variant="destructive">May be urgent</Badge>}
                </div>
                {c.flags
                  .filter((f) => f !== 'may be urgent')
                  .map((f) => (
                    <p key={f} className="mt-1 text-body">
                      {f}
                    </p>
                  ))}
                {c.topic && <p className="mt-1 text-body">About: {c.topic}</p>}
                {c.quote && (
                  <blockquote className="mt-2 border-l-2 border-line pl-3 text-body [overflow-wrap:anywhere]">
                    <span className="block text-xs text-muted">Written by someone {name} doesn&rsquo;t know. Not verified.</span>
                    &ldquo;{c.quote}&rdquo;
                  </blockquote>
                )}
                {d ? (
                  <div className="mt-3">
                    <label className="text-xs font-medium text-ink" htmlFor={`draft-${d.id}`}>
                      Suggested reply to {c.who}
                    </label>
                    <Textarea
                      id={`draft-${d.id}`}
                      className="mt-1"
                      rows={3}
                      maxLength={400}
                      value={text}
                      onChange={(e) => setEdits({ ...edits, [d.id]: e.target.value })}
                    />
                    {d.adds.length > 0 && (
                      <p className="mt-1 text-xs text-amber-700">
                        This mentions {d.adds.join(', ')}, which they did not say. Check it before you send.
                      </p>
                    )}
                    <p className="mt-1 text-xs text-muted">It goes out as &ldquo;Hi {c.who}, this is {name}, an AI assistant.&rdquo; followed by these words.</p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      <Button
                        size="sm"
                        disabled={busy || !text.trim()}
                        aria-label={`Send the reply to ${c.who}`}
                        onClick={() =>
                          void act(() => call(`/briefing/drafts/${d.id}/send`, { method: 'POST', json: edits[d.id] !== undefined && edits[d.id] !== d.text ? { text: edits[d.id] } : {} }))
                        }
                      >
                        Send
                      </Button>
                      <Button size="sm" variant="ghost" disabled={busy} aria-label={`Dismiss the reply to ${c.who}`} onClick={() => void act(() => call(`/briefing/drafts/${d.id}/dismiss`, { method: 'POST' }))}>
                        Dismiss
                      </Button>
                    </div>
                  </div>
                ) : (
                  <p className="mt-2 text-xs text-muted">No reply drafted. You can write to them from People you don&rsquo;t know in Settings.</p>
                )}
              </li>
            )
          })}
        </ul>
        {data.overdue.length > 0 && (
          <div>
            <h3 className="flex items-center gap-2 text-sm font-medium text-ink">
              <Handshake className="size-4" aria-hidden /> Promises past their time
            </h3>
            <ul className="mt-1 space-y-1 text-sm text-body" aria-label="Overdue promises">
              {data.overdue.map((o) => (
                <li key={o.id}>
                  You told {o.who}: {o.text} <span className="text-muted">(due {o.due_text})</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        <Button size="sm" variant="outline" disabled={busy} onClick={() => void act(() => call('/briefing/seen', { method: 'POST' }))}>
          Got it
        </Button>
      </CardContent>
    </Card>
  )
}
