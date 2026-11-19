'use client'

import { useState } from 'react'
import { Notice } from '@/components/site'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useAppData } from '@/features/dashboard/data'
import { errorMessage } from '@/lib/action'
import { call, type VisitorQuestionView } from '@/lib/api'

const WHERE: Record<string, string> = { telegram: 'Telegram', slack: 'Slack' }

// "Questions for you": things people Ava doesn't know asked that she couldn't answer. What they typed is shown quoted
// (it is their claim, never yours); your answer goes to that person, once, inside a fixed sentence, and is not saved for
// anyone else (#494).
export function QuestionsSection({ agentName }: { agentName: string }) {
  const { data, reload } = useAppData<{ questions: VisitorQuestionView[]; open: number }>('/receptionist/questions')
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [result, setResult] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)

  const waiting = (data?.questions ?? []).filter((q) => q.state === 'asked')
  if (!data || waiting.length === 0) return null

  async function send(q: VisitorQuestionView, what: 'answer' | 'decline') {
    setBusy(q.id)
    setResult(null)
    try {
      const res = await call<{ notice?: string }>(`/receptionist/questions/${q.id}/${what}`, {
        method: 'POST',
        json: what === 'answer' ? { text: drafts[q.id] ?? '' } : {},
      })
      setResult({
        tone: res.notice ? 'error' : 'info',
        text: res.notice || (what === 'answer' ? 'Sent. They were told this is the answer.' : 'Done. They were told you can’t answer.'),
      })
      setDrafts((d) => ({ ...d, [q.id]: '' }))
      await reload()
    } catch (e) {
      setResult({ tone: 'error', text: errorMessage(e) })
    } finally {
      setBusy(null)
    }
  }

  return (
    <section id="questions" className="scroll-mt-6 space-y-3 border-t border-line pt-4" aria-labelledby="questions-title">
      <h3 id="questions-title" className="text-base font-medium text-ink">
        Questions for you ({waiting.length})
      </h3>
      <p className="text-sm text-body">
        People {agentName} doesn’t know asked these and she had no answer. Your answer goes only to the person who asked, inside a
        fixed sentence. It isn’t saved for anyone else.
      </p>
      <ul className="space-y-4">
        {waiting.map((q) => (
          <li key={q.id} className="space-y-2 rounded-lg border border-line p-3">
            <p className="text-xs text-muted">
              {q.name ? `${q.name} (says so; not verified)` : 'Someone who gave no name'} on {WHERE[q.provider] ?? q.provider}
            </p>
            <p className="text-sm text-ink">“{q.question}”</p>
            <Textarea
              aria-label={`Your answer to: ${q.question}`}
              rows={2}
              maxLength={500}
              value={drafts[q.id] ?? ''}
              onChange={(e) => setDrafts((d) => ({ ...d, [q.id]: e.target.value }))}
              placeholder="Type the answer they should get"
            />
            <div className="flex gap-2">
              <Button size="sm" disabled={busy === q.id || !(drafts[q.id] ?? '').trim()} onClick={() => void send(q, 'answer')}>
                Send my answer
              </Button>
              <Button size="sm" variant="outline" disabled={busy === q.id} onClick={() => void send(q, 'decline')}>
                Don’t answer
              </Button>
            </div>
          </li>
        ))}
      </ul>
      {result && <Notice tone={result.tone}>{result.text}</Notice>}
    </section>
  )
}
