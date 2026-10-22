'use client'

import { useState, type FormEvent } from 'react'
import { Loadable } from '@/components/load-state'
import { Notice } from '@/components/site'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { AskLinkCard } from '@/features/ask/AskLinkCard'
import { AccessLogCard } from '@/features/dashboard/AccessLogCard'
import { CHANNEL_LABEL, DIGEST_LABEL, useAppData } from '@/features/dashboard/data'
import { AppError, auth, type Preferences } from '@/lib/api'
import { useSession } from '@/lib/session-context'

export function SettingsPage() {
  const { account, me } = useSession()
  const prefs = useAppData<Preferences>('/preferences')
  if (!account || !me) return null
  const name = me.agent.name

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl space-y-4 px-4 py-6 sm:px-6 lg:py-8">
        <div>
          <h1 className="text-3xl font-medium text-ink tight">Settings</h1>
          <p className="mt-1 text-body">Your account, and the rules {name} works by.</p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Account</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid gap-3 text-sm sm:grid-cols-[10rem_1fr]">
              <dt className="text-muted">Name</dt>
              <dd className="text-ink">{account.user.name}</dd>
              <dt className="text-muted">Email</dt>
              <dd className="text-ink">{account.user.email}</dd>
              <dt className="text-muted">Member since</dt>
              <dd className="text-ink">{new Date(account.user.created_at).toLocaleDateString(undefined, { dateStyle: 'long' })}</dd>
            </dl>
          </CardContent>
        </Card>

        <EmailCard current={account.user.email} />

        <PasswordCard />

        <Card>
          <CardHeader>
            <CardTitle>Your rules</CardTitle>
            <CardDescription>What you told {name} in your introduction.</CardDescription>
          </CardHeader>
          <CardContent>
            <Loadable state={prefs} what="your rules">
              {(rules) => (
                <dl className="grid gap-3 text-sm sm:grid-cols-[10rem_1fr]">
                  <dt className="text-muted">Interrupt me for</dt>
                  <dd className="text-ink">{rules.urgent_topics.join('; ') || 'Nothing yet'}</dd>
                  <dt className="text-muted">Reach me</dt>
                  <dd className="text-ink">{CHANNEL_LABEL[rules.interrupt_channel] ?? rules.interrupt_channel}</dd>
                  <dt className="text-muted">Quiet hours</dt>
                  <dd className="text-ink">
                    {rules.quiet_start}–{rules.quiet_end} ({rules.timezone})
                    {rules.vip_breaks_quiet ? ', VIPs can break through' : ''}
                  </dd>
                  <dt className="text-muted">Digest</dt>
                  <dd className="text-ink">{DIGEST_LABEL[rules.digest] ?? rules.digest}</dd>
                  <dt className="text-muted">Others may know</dt>
                  <dd className="text-ink">{rules.shareable.map((s) => s.label).join('; ') || 'Nothing beyond your name'}</dd>
                  <dt className="text-muted">Never share</dt>
                  <dd className="text-ink">{rules.never_share || 'Nothing listed'}</dd>
                </dl>
              )}
            </Loadable>
          </CardContent>
        </Card>

        <AskLinkCard />

        <AccessLogCard name={name} />
      </div>
    </div>
  )
}

function EmailCard({ current }: { current: string }) {
  const { refresh } = useSession()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setResult(null)
    try {
      const changed = await auth.changeEmail(email, password)
      setEmail('')
      setPassword('')
      await refresh()
      setResult({
        tone: 'info',
        text: `Your email is now ${changed.user.email}. We sent a link there: open it to confirm the address.`,
      })
    } catch (err) {
      setResult({ tone: 'error', text: err instanceof AppError ? err.message : String(err) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Change email</CardTitle>
        <CardDescription>
          You sign in with {current}. After changing it, confirm the new address from the link we send there.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="grid gap-4 sm:max-w-sm" aria-label="Change email">
          <div className="grid gap-1.5">
            <Label htmlFor="new-email">New email</Label>
            <Input id="new-email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="email-password">Password</Label>
            <Input
              id="email-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
          <div>
            <Button type="submit" disabled={busy || !email || !password}>
              Change email
            </Button>
          </div>
          {result && <Notice tone={result.tone}>{result.text}</Notice>}
        </form>
      </CardContent>
    </Card>
  )
}

function PasswordCard() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setResult(null)
    try {
      await auth.changePassword(current, next)
      setCurrent('')
      setNext('')
      setResult({ tone: 'info', text: 'Password changed. Your other devices have been signed out.' })
    } catch (err) {
      setResult({ tone: 'error', text: err instanceof AppError ? err.message : String(err) })
    } finally {
      setBusy(false)
    }
  }

  async function everywhere() {
    setResult(null)
    try {
      await auth.logoutEverywhere()
      window.location.assign('/login')
    } catch (err) {
      setResult({ tone: 'error', text: err instanceof AppError ? err.message : String(err) })
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Password and sign-in</CardTitle>
        <CardDescription>Changing your password signs out your other devices.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="grid gap-4 sm:max-w-sm">
          <div className="grid gap-1.5">
            <Label htmlFor="current-password">Current password</Label>
            <Input id="current-password" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="new-password">New password</Label>
            <Input id="new-password" type="password" autoComplete="new-password" minLength={10} value={next} onChange={(e) => setNext(e.target.value)} required />
            <p className="text-xs text-muted">At least 10 characters. A few unrelated words work well.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy || !current || !next}>
              Change password
            </Button>
            <Button type="button" variant="outline" onClick={() => void everywhere()}>
              Sign out everywhere
            </Button>
          </div>
          {result && <Notice tone={result.tone}>{result.text}</Notice>}
        </form>
      </CardContent>
    </Card>
  )
}
