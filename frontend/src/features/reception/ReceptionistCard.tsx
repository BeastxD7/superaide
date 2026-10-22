'use client'

import { useState, type FormEvent } from 'react'
import { Notice } from '@/components/site'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { useAppData } from '@/features/dashboard/data'
import { AppError, call, type ReceptionistSettings } from '@/lib/api'

// Settings → "People you don't know": whether Ava talks with them for you, what she may say, and whether
// she may use your first name. On by default; she only ever uses what you write here and notes you marked public.
export function ReceptionistCard({ agentName }: { agentName: string }) {
  const { data, error, reload } = useAppData<ReceptionistSettings>('/receptionist')
  const [profile, setProfile] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)

  async function save(body: Partial<ReceptionistSettings>, done: string) {
    setBusy(true)
    setResult(null)
    try {
      await call<ReceptionistSettings>('/receptionist', { method: 'PUT', json: body })
      await reload()
      setProfile(null)
      setResult({ tone: 'info', text: done })
    } catch (e) {
      setResult({ tone: 'error', text: e instanceof AppError ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  function saveProfile(e: FormEvent) {
    e.preventDefault()
    void save({ public_profile: profile ?? data?.public_profile ?? '' }, 'Saved. Anyone who writes to her can be told this.')
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>People you don’t know</CardTitle>
        <CardDescription>
          When someone {agentName} doesn’t recognise writes to her on Telegram or Slack, she answers like a receptionist: she
          says she’s an AI assistant, answers only from the public information below and notes you marked public, and can
          take a message for you. She never shares anything private, and never says where you are or what you’re doing.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}
        {!data && !error && <Skeleton className="h-24 w-full" />}
        {data && (
          <>
            <label className="flex items-start gap-3 text-sm text-ink">
              <input
                type="checkbox"
                className="mt-1 size-4"
                checked={data.enabled}
                disabled={busy}
                onChange={(e) =>
                  void save(
                    { enabled: e.target.checked },
                    e.target.checked ? `${agentName} will talk with people she doesn’t know.` : 'Off. They’ll get a short fixed reply.',
                  )
                }
              />
              <span>
                Let {agentName} talk with people she doesn’t know
                <span className="block text-muted">When this is off they get a short fixed reply and nothing is passed on.</span>
              </span>
            </label>

            {data.enabled && (
              <>
                <label className="flex items-start gap-3 text-sm text-ink">
                  <input
                    type="checkbox"
                    className="mt-1 size-4"
                    checked={data.show_first_name}
                    disabled={busy}
                    onChange={(e) =>
                      void save({ show_first_name: e.target.checked }, 'Saved. This is how she introduces herself now.')
                    }
                  />
                  <span>
                    Use my first name when she introduces herself
                    <span className="block text-muted">She’d say: “{data.greeting}”</span>
                  </span>
                </label>

                <form onSubmit={saveProfile} className="space-y-2">
                  <Label htmlFor="public-profile">What she may tell anyone</Label>
                  <Textarea
                    id="public-profile"
                    rows={4}
                    maxLength={2000}
                    value={profile ?? data.public_profile}
                    onChange={(e) => setProfile(e.target.value)}
                    placeholder="What you do, when you’re reachable, the best way to reach you. Don’t put anything private here."
                  />
                  <Button type="submit" size="sm" disabled={busy || profile === null}>
                    Save
                  </Button>
                </form>
                <p className="text-xs text-muted">
                  Limits per person: {data.per_hour} messages an hour, {data.per_day} a day. Messages she takes show up under Needs
                  you (or in your notifications).
                </p>
              </>
            )}
            {result && <Notice tone={result.tone}>{result.text}</Notice>}
          </>
        )}
      </CardContent>
    </Card>
  )
}
