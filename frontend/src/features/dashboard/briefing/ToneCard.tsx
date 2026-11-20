'use client'

import { useState } from 'react'
import { Notice } from '@/components/site'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useAppData } from '@/features/dashboard/data'
import { call } from '@/lib/api'
import { errorMessage } from '@/lib/action'

type ToneSettings = {
  tone: string
  source: 'default' | 'observed' | 'owner'
  options: { value: string; label: string }[]
}

const SOURCE_TEXT: Record<ToneSettings['source'], string> = {
  default: 'Nothing learned yet.',
  observed: 'Learned from how you write. Pick one to override it.',
  owner: 'You chose this. Ava will not change it.',
}

// Settings → "Tone of drafted replies": the tone Ava writes drafts for you in. She learns it from how you write until
// you pick one; the choice only changes the style of a draft, never what she may say or who she is.
export function ToneCard({ agentName }: { agentName: string }) {
  const { data, error, reload } = useAppData<ToneSettings>('/briefing/tone')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)

  async function save(tone: string | null, done: string) {
    setBusy(true)
    setResult(null)
    try {
      await call<ToneSettings>('/briefing/tone', { method: 'PUT', json: { tone } })
      await reload()
      setResult({ tone: 'info', text: done })
    } catch (e) {
      setResult({ tone: 'error', text: errorMessage(e) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card data-testid="tone-card">
      <CardHeader>
        <CardTitle>Tone of drafted replies</CardTitle>
        <CardDescription>
          How {agentName} words the replies she drafts for you to send. She learns it from how you write, and you can change
          it any time. It never changes what she may say, and she still says she is an AI assistant.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {error && <Notice tone="error">{error}</Notice>}
        {!data && !error && <Skeleton className="h-10 w-full" />}
        {data && (
          <>
            <label className="block text-sm text-ink">
              <span className="mb-1 block text-muted">Tone</span>
              <select
                className="h-10 w-full max-w-xs rounded-md border border-line bg-white px-3 text-sm"
                value={data.tone}
                disabled={busy}
                onChange={(e) => void save(e.target.value, 'Saved. New drafts use this tone.')}
              >
                {data.options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <p className="text-sm text-muted" data-testid="tone-source">
              {SOURCE_TEXT[data.source]}
            </p>
            {data.source === 'owner' && (
              <button
                type="button"
                className="text-sm text-ink underline"
                disabled={busy}
                onClick={() => void save(null, 'Back to learning it from how you write.')}
              >
                Let {agentName} learn it again
              </button>
            )}
            <div role="status" aria-live="polite">
              {result && <Notice tone={result.tone}>{result.text}</Notice>}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
