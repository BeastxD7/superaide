'use client'

import { useState } from 'react'
import { Notice } from '@/components/site'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useAppData } from '@/features/dashboard/data'
import { errorMessage } from '@/lib/action'
import { call, type VisitorConversationView } from '@/lib/api'
import { resumesIn } from '@/features/reception/resumes-in'

const WHERE: Record<string, string> = { telegram: 'Telegram', slack: 'Slack' }

// "Step into a conversation" (#493): write to someone yourself and Ava stays out of that conversation (she still reads what
// you wrote). She takes it back when you press the button, tell her to, or stay silent for the idle window.
export function TakeoverSection({ agentName }: { agentName: string }) {
  const { data, reload } = useAppData<{ conversations: VisitorConversationView[]; handling: number; idle_minutes: number }>(
    '/receptionist/conversations',
  )
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [minutes, setMinutes] = useState<string | null>(null)
  const [result, setResult] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)

  if (!data || data.conversations.length === 0) return null

  async function run(id: string, action: () => Promise<string>) {
    setBusy(id)
    setResult(null)
    try {
      setResult({ tone: 'info', text: await action() })
      await reload()
    } catch (e) {
      setResult({ tone: 'error', text: errorMessage(e) })
    } finally {
      setBusy(null)
    }
  }

  const write = (c: VisitorConversationView) =>
    run(c.context_id, async () => {
      const res = await call<{ notice?: string }>(`/receptionist/conversations/${c.context_id}/write`, {
        method: 'POST',
        json: { text: drafts[c.context_id] ?? '' },
      })
      setDrafts((d) => ({ ...d, [c.context_id]: '' }))
      return res.notice || `Sent. ${agentName} stays out of this conversation until you give it back.`
    })

  const giveBack = (c: VisitorConversationView) =>
    run(c.context_id, async () => {
      await call(`/receptionist/conversations/${c.context_id}/release`, { method: 'POST', json: {} })
      return `${agentName} is back in this conversation.`
    })

  const saveWindow = () =>
    run('window', async () => {
      await call('/receptionist', { method: 'PUT', json: { takeover_idle_minutes: Number(minutes) } })
      setMinutes(null)
      return `Saved. ${agentName} takes a conversation back after ${minutes} minutes without a message from you.`
    })

  const shown = minutes ?? String(data.idle_minutes)
  return (
    <section id="takeover" className="scroll-mt-6 space-y-3 border-t border-line pt-4" aria-labelledby="takeover-title" data-testid="takeover">
      <h3 id="takeover-title" className="text-base font-medium text-ink">
        Step into a conversation
      </h3>
      <p className="text-sm text-body">
        Write to someone yourself and {agentName} stays out of that conversation, but still reads what you wrote. She takes it back
        when you press the button, when you tell her to, or after {data.idle_minutes} minutes without a message from you. Their words
        are shown as they typed them; they aren’t verified.
      </p>
      <ul className="space-y-3">
        {data.conversations.map((c) => (
          <li key={c.context_id} className="space-y-2 rounded-lg border border-line p-3" data-testid="takeover-row">
            <p className="text-xs text-muted">
              {c.name ? `${c.name} (says so; not verified)` : 'Someone who gave no name'} on {WHERE[c.provider] ?? c.provider}
            </p>
            {c.takeover ? (
              <>
                <p className="text-sm font-medium text-ink" role="status" data-testid="takeover-indicator">
                  You’re handling this. {agentName} isn’t replying here.
                </p>
                <p className="text-xs text-muted">She takes it back {resumesIn(c.takeover.resumes_at)} unless you write again.</p>
              </>
            ) : null}
            {c.takeover && c.messages.length > 0 && (
              <ul className="space-y-1 text-sm" aria-label="Recent messages" data-testid="takeover-messages">
                {c.messages.map((m, i) => (
                  <li key={`${m.at}-${i}`} className={m.from === 'owner' ? 'text-ink' : 'text-body'}>
                    <span className="text-xs text-muted">{m.from === 'owner' ? 'You' : m.from === 'ava' ? agentName : 'Them'}: </span>
                    {m.from === 'visitor' ? `“${m.text}”` : m.text}
                  </li>
                ))}
              </ul>
            )}
            <Textarea
              aria-label={`Your message to ${c.name || 'this person'}`}
              rows={2}
              maxLength={1000}
              value={drafts[c.context_id] ?? ''}
              onChange={(e) => setDrafts((d) => ({ ...d, [c.context_id]: e.target.value }))}
              placeholder="Type what you want to say to them"
            />
            <div className="flex flex-wrap gap-2">
              <Button size="sm" disabled={busy === c.context_id || !(drafts[c.context_id] ?? '').trim()} onClick={() => void write(c)}>
                {c.takeover ? 'Send' : 'Send and take over'}
              </Button>
              {c.takeover && (
                <Button size="sm" variant="outline" disabled={busy === c.context_id} onClick={() => void giveBack(c)}>
                  Give control back to {agentName}
                </Button>
              )}
            </div>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-2 text-sm text-ink">
        <label htmlFor="takeover-minutes">{agentName} takes a conversation back after</label>
        <input
          id="takeover-minutes"
          type="number"
          min={5}
          max={1440}
          className="w-20 rounded-md border border-line bg-surface px-2 py-1"
          value={shown}
          onChange={(e) => setMinutes(e.target.value)}
        />
        <span>minutes without a message from you</span>
        <Button size="sm" variant="outline" disabled={busy === 'window' || minutes === null} onClick={() => void saveWindow()}>
          Save
        </Button>
      </div>
      {result && <Notice tone={result.tone}>{result.text}</Notice>}
    </section>
  )
}
