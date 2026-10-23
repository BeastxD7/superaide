'use client'

import { Copy, Link2 } from 'lucide-react'
import { useState } from 'react'
import { ActionError } from '@/components/load-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useAppData } from '@/features/dashboard/data'
import { call } from '@/lib/api'
import { errorMessage } from '@/lib/action'

type Link = { id: string; created_at: string; expires_at: string; revoked_at: string | null; views: number; status: 'active' | 'expired' | 'revoked' }
type Links = { links: Link[]; max_days: number; max_active: number }
type Created = Link & { token: string; path: string }

const when = (iso: string) => new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })

// Create, see and revoke unlisted links to the counts-only picture. The server answers 404 while the feature
// is off, so nothing shows then.
export function BrainShare() {
  const { data, reload } = useAppData<Links>('/brain/share-links')
  const [days, setDays] = useState(1)
  const [fresh, setFresh] = useState<Created | null>(null)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (!data) return null

  const url = fresh ? `${window.location.origin}${fresh.path}` : ''

  async function create() {
    setError(null)
    setCopied(false)
    try {
      setFresh(await call<Created>('/brain/share-links', { method: 'POST', json: { days } }))
      await reload()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  async function revoke(id: string) {
    setError(null)
    try {
      await call(`/brain/share-links/${id}/revoke`, { method: 'POST', json: {} })
      if (fresh?.id === id) setFresh(null)
      await reload()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
    } catch {
      setCopied(false) // the link is shown in a field, so it can be copied by hand
    }
  }

  return (
    <section className="rounded-xl border border-line bg-white p-4 text-sm" aria-labelledby="share-h">
      <h2 id="share-h" className="flex items-center gap-1.5 font-medium text-ink">
        <Link2 className="size-4" aria-hidden /> Share the picture by a link
      </h2>
      <p className="mt-1 text-xs text-muted">
        Anyone with the link sees counts only, no names or notes, until it expires (at most {data.max_days} days). You can turn it off at any time.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <label htmlFor="share-days" className="text-xs text-muted">
          Lasts
        </label>
        <select
          id="share-days"
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="h-8 rounded-md border border-line bg-white px-2 text-sm"
        >
          {Array.from({ length: data.max_days }, (_, i) => i + 1).map((d) => (
            <option key={d} value={d}>
              {d} {d === 1 ? 'day' : 'days'}
            </option>
          ))}
        </select>
        <Button size="sm" onClick={() => void create()}>
          Create link
        </Button>
      </div>
      {error && (
        <div className="mt-2">
          <ActionError action="change the link" error={error} />
        </div>
      )}
      {fresh && (
        <div className="mt-3 rounded-lg border border-line bg-surface p-3" role="status">
          <p className="text-xs text-muted">Copy it now: it is shown only once.</p>
          <div className="mt-1 flex items-center gap-2">
            <input readOnly value={url} aria-label="Share link" className="min-w-0 flex-1 rounded-md border border-line bg-white px-2 py-1 text-xs" onFocus={(e) => e.currentTarget.select()} />
            <Button size="sm" variant="outline" onClick={() => void copy()}>
              <Copy className="size-4" aria-hidden /> {copied ? 'Copied' : 'Copy'}
            </Button>
          </div>
        </div>
      )}
      {data.links.length > 0 && (
        <ul className="mt-3 space-y-2" aria-label="Your share links">
          {data.links.map((l) => (
            <li key={l.id} className="flex flex-wrap items-center gap-2 text-xs text-muted">
              <Badge variant={l.status === 'active' ? 'secondary' : 'outline'}>{l.status}</Badge>
              <span>
                {l.status === 'active' ? `until ${when(l.expires_at)}` : `made ${when(l.created_at)}`} · {l.views} {l.views === 1 ? 'view' : 'views'}
              </span>
              {l.status === 'active' && (
                <Button size="sm" variant="ghost" onClick={() => void revoke(l.id)}>
                  Turn off
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
