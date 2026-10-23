'use client'

import { Copy, ExternalLink } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { ConfirmAction } from '@/components/confirm-action'
import { Notice } from '@/components/site'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useAsk } from '@/features/ask/useAsk'
import { call, type AskLinkView } from '@/lib/api'
import { errorMessage } from '@/lib/action'

// Settings → "Ask my Ava link": pick a handle, publish it, copy it, revoke it, and choose what the page
// shows. Renders nothing while the backend has the feature off.
export function AskLinkCard() {
  const { enabled, data, setData } = useAsk<AskLinkView>('/ask-link')
  const [handle, setHandle] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)
  if (!enabled || !data) return null

  const link = data.link
  const live = Boolean(link?.published && !link.revoked)
  const value = handle ?? (link && !link.revoked ? link.handle : data.suggested)
  const url = link ? `${window.location.origin}${link.path}` : ''

  async function save(body: Record<string, unknown>, done: string) {
    setBusy(true)
    setResult(null)
    try {
      setData(await call<AskLinkView>('/ask-link', { method: 'PUT', json: body }))
      setHandle(null)
      setResult({ tone: 'info', text: done })
    } catch (e) {
      setResult({ tone: 'error', text: errorMessage(e) })
    } finally {
      setBusy(false)
    }
  }

  async function revoke() {
    setBusy(true)
    setResult(null)
    try {
      setData(await call<AskLinkView>('/ask-link/revoke', { method: 'POST' }))
      setResult({ tone: 'info', text: 'Revoked. The link now looks like any link that doesn’t exist.' })
    } catch (e) {
      setResult({ tone: 'error', text: errorMessage(e) })
    } finally {
      setBusy(false)
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(url)
      setResult({ tone: 'info', text: 'Link copied.' })
    } catch {
      setResult({ tone: 'error', text: 'Copy didn’t work here. Select the link and copy it yourself.' })
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault()
    void save({ handle: value, published: true }, 'Your link is live.')
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Ask my Ava link</CardTitle>
        <CardDescription>
          A link for your email signature or bio. People leave a short request; Ava ranks it and shows it to you. She
          never replies to them on her own, and the page doesn&rsquo;t say who you are unless you choose to.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {live && link && (
          <div className="grid gap-2">
            <Label htmlFor="ask-url">Your link</Label>
            <div className="flex flex-wrap gap-2">
              <Input id="ask-url" readOnly value={url} className="min-w-0 flex-1 font-mono text-sm" onFocus={(e) => e.currentTarget.select()} />
              <Button type="button" variant="outline" onClick={() => void copy()}>
                <Copy aria-hidden /> Copy
              </Button>
              <Button asChild variant="outline">
                <a href={link.path} target="_blank" rel="noreferrer">
                  <ExternalLink aria-hidden /> Preview
                </a>
              </Button>
            </div>
          </div>
        )}

        <form onSubmit={submit} className="grid gap-2 sm:max-w-md">
          <Label htmlFor="ask-handle">{live ? 'Change the handle' : 'Pick a handle'}</Label>
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted" aria-hidden>
              /a/
            </span>
            <Input
              id="ask-handle"
              value={value}
              onChange={(e) => setHandle(e.target.value.toLowerCase())}
              maxLength={30}
              autoCapitalize="none"
              spellCheck={false}
              aria-describedby="ask-handle-hint"
            />
          </div>
          <p id="ask-handle-hint" className="text-xs text-muted">
            3 to 30 lowercase letters, numbers or hyphens. The suggestion doesn&rsquo;t say who you are. Changing it turns
            the old link off for good, and nobody else can take it.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy || !value || (live && value === link?.handle)}>
              {live ? 'Save handle' : 'Publish link'}
            </Button>
            {live && (
              <Button type="button" variant="outline" disabled={busy} onClick={() => void save({ published: false }, 'Unpublished. You can publish it again.')}>
                Unpublish
              </Button>
            )}
            {link && !link.revoked && (
              <ConfirmAction
                label="Revoke"
                variant="ghost"
                confirmLabel="Yes, revoke it for good"
                question="The link stops working for good and can't be turned back on."
                disabled={busy}
                onConfirm={revoke}
              />
            )}
            {link && !live && !link.revoked && (
              <Button type="button" variant="outline" disabled={busy} onClick={() => void save({ published: true }, 'Your link is live.')}>
                Publish again
              </Button>
            )}
          </div>
        </form>

        {link && (
          <label className="flex items-start gap-3 text-sm">
            <input
              type="checkbox"
              className="mt-1 size-4"
              checked={link.show_first_name}
              disabled={busy}
              onChange={(e) =>
                void save({ show_first_name: e.target.checked }, e.target.checked ? `The page now says “${data.first_name}’s assistant”.` : 'The page no longer shows your name.')
              }
            />
            <span>
              Show my first name on the page (&ldquo;{data.first_name}&rsquo;s assistant&rdquo;)
              <span className="block text-xs text-muted">Off: the page says only that it&rsquo;s an AI assistant&rsquo;s inbox.</span>
            </span>
          </label>
        )}

        {link && (
          <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4" aria-label="Last 30 days">
            {(
              [
                ['Views', data.stats.views],
                ['Requests', data.stats.submissions],
                ['Blocked as bots', data.stats.dropped],
                ['“Get your own” clicks', data.stats.get_own_clicks],
              ] as const
            ).map(([label, n]) => (
              <div key={label} className="rounded-xl bg-mist p-3">
                <dt className="text-xs text-muted">{label}</dt>
                <dd className="text-lg font-medium text-ink">{n}</dd>
              </div>
            ))}
          </dl>
        )}

        {result && <Notice tone={result.tone}>{result.text}</Notice>}
      </CardContent>
    </Card>
  )
}
