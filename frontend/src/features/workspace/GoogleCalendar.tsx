'use client'

import { Check, ChevronDown, Copy, ExternalLink, Puzzle } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import Link from 'next/link'
import { useEffect, useState, type FormEvent } from 'react'
import { AppError, call, type SkillCard } from '@/lib/api'
import { BrandIcon } from '@/components/BrandIcon'
import { Button, Notice } from '@/components/site'

// Google Calendar. Two steps: an OAuth client (the owner's own, or this server's), then sign in
// with Google. Read-only unless calendar actions are switched on under Capabilities. The backend runs the OAuth flow with PKCE
// and sends the browser back to /dashboard/connections?google=<outcome>.

export type GoogleService = {
  connected: boolean
  via?: 'app' | 'env' | null
  hints?: Record<string, string>
  client_ready?: boolean
  meta?: { scopes?: string[] }
}
export type GoogleGuide = { url: string; steps: string[]; redirect_uri?: string; signin_redirect_uri?: string }

const EVENTS_SCOPE = 'https://www.googleapis.com/auth/calendar.events'
type Saved = { services: { google: GoogleService } }

const SKILL_ID = 'superaide/google-calendar'

// What the callback's ?google= value means, for the owner.
const OUTCOMES: Record<string, { tone: 'info' | 'error'; text: string }> = {
  connected: { tone: 'info', text: 'Google Calendar is connected. I can read your calendar now; changing it needs calendar actions, under Capabilities.' },
  cancelled: { tone: 'error', text: 'Google sign-in was cancelled, so nothing was connected. Press Connect Google Calendar to try again.' },
  expired: { tone: 'error', text: 'That sign-in had expired or was already used. Press Connect Google Calendar to start again.' },
  failed: {
    tone: 'error',
    text: "Google didn't give access. Check the client ID and secret, and that the redirect URI in Google Cloud matches exactly, then try again.",
  },
  no_calendar: { tone: 'error', text: "Calendar access wasn't granted. Connect again and tick the calendar box on Google's screen." },
  no_actions: { tone: 'error', text: "Changing your calendar wasn't allowed. Connect again and tick both calendar boxes on Google's screen." },
}

export function GoogleCalendarRow({
  service,
  guide,
  where,
  expanded,
  onToggle,
  outcome,
  onChanged,
}: {
  service: GoogleService
  guide: GoogleGuide
  where: string
  expanded: boolean
  onToggle: () => void
  outcome: string | null
  onChanged: (next: Saved) => void
}) {
  const clientHint = service.hints?.client_id
  const summary = service.connected
    ? service.via === 'env'
      ? 'Connected from the .env file'
      : service.meta?.scopes?.includes(EVENTS_SCOPE)
        ? 'Connected, can add and move events'
        : 'Connected, read-only'
    : service.client_ready
      ? `OAuth client saved${clientHint ? ` (${clientHint})` : ''}. Sign in to finish`
      : 'What’s on, when you’re free, who you’re meeting'

  return (
    <div className="overflow-hidden rounded-2xl bg-white">
      <button onClick={onToggle} aria-expanded={expanded} className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-lilac-50">
        <span className="grid size-9 shrink-0 place-items-center rounded-full bg-lilac-100">
          <BrandIcon brand="googlecalendar" size={18} decorative />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-ink">Google Calendar</span>
          <span className="block truncate text-xs text-muted">{summary}</span>
        </span>
        {service.connected ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-sky-100 px-2.5 py-1 text-xs font-medium text-ok">
            <Check className="size-3.5" /> Connected
          </span>
        ) : (
          <span className="rounded-full bg-ink px-3 py-1.5 text-xs font-medium text-white">Connect</span>
        )}
        <ChevronDown className={`size-4 shrink-0 text-muted transition-transform ${expanded ? 'rotate-180' : ''}`} />
      </button>
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.25 }}>
            <GoogleSetup service={service} guide={guide} where={where} outcome={outcome} onChanged={onChanged} />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function GoogleSetup({
  service,
  guide,
  where,
  outcome,
  onChanged,
}: {
  service: GoogleService
  guide: GoogleGuide
  where: string
  outcome: string | null
  onChanged: (next: Saved) => void
}) {
  const [changing, setChanging] = useState(false)
  const editing = changing || !service.client_ready
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const shown = outcome ? OUTCOMES[outcome] : undefined
  const fromApp = service.via !== 'env'

  async function run(fn: () => Promise<void>) {
    setBusy(true)
    setError(null)
    try {
      await fn()
    } catch (err) {
      setError(err instanceof AppError ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const connect = () =>
    run(async () => {
      const { url } = await call<{ url: string }>('/connections/google/start', { method: 'POST' })
      window.location.assign(url)
    })
  const disconnect = () => run(async () => onChanged(await call<Saved>('/connections/google/disconnect', { method: 'POST' })))

  return (
    <div className="space-y-3 border-t border-line px-4 pt-3 pb-4">
      {shown && (
        <div role="status" aria-label="Google Calendar result">
          <Notice tone={shown.tone}>{shown.text}</Notice>
        </div>
      )}

      {service.connected ? (
        <>
          <p className="flex items-center gap-2 rounded-xl bg-sky-100 px-3 py-2 text-sm text-ink">
            <Check className="size-4 text-ok" />
            {fromApp ? 'Signed in with Google. I can read your calendar, never change it.' : 'Connected from the .env file.'}
          </p>
          <SkillNudge />
        </>
      ) : (
        <>
          <section aria-label="Step 1: your Google OAuth client">
            <p className="text-xs font-medium tracking-wide text-muted uppercase">Step 1 · Your Google OAuth client</p>
            {editing ? (
              <ClientForm
                guide={guide}
                where={where}
                onSaved={(next) => {
                  setChanging(false)
                  onChanged(next)
                }}
                onCancel={service.client_ready ? () => setChanging(false) : undefined}
              />
            ) : (
              <p className="mt-1.5 flex flex-wrap items-center gap-2 text-sm text-body">
                <Check className="size-4 text-ok" /> Saved{service.hints?.client_id ? <span className="font-mono text-xs">{service.hints.client_id}</span> : null}
                {service.hints?.client_secret && <span className="font-mono text-xs text-muted">secret {service.hints.client_secret}</span>}
                <button onClick={() => setChanging(true)} className="text-xs font-medium text-violet-700 hover:underline">
                  Change
                </button>
              </p>
            )}
          </section>
          <section aria-label="Step 2: sign in with Google">
            <p className="text-xs font-medium tracking-wide text-muted uppercase">Step 2 · Sign in with Google</p>
            <p className="mt-1 text-sm text-body">Google asks you to allow read-only access to your calendar, then sends you back here.</p>
            <Button tone="violet" className="mt-2 w-full py-3" disabled={busy || !service.client_ready || editing} onClick={() => void connect()}>
              {busy ? 'Opening Google…' : 'Connect Google Calendar'}
            </Button>
          </section>
        </>
      )}

      {error && <Notice tone="error">{error}</Notice>}

      {fromApp && (service.connected || service.client_ready) && (
        <button onClick={() => void disconnect()} disabled={busy} className="w-full rounded-full py-2 text-sm text-muted hover:text-danger">
          Disconnect Google Calendar
        </button>
      )}
    </div>
  )
}

function ClientForm({
  guide,
  where,
  onSaved,
  onCancel,
}: {
  guide: GoogleGuide
  where: string
  onSaved: (next: Saved) => void
  onCancel?: () => void
}) {
  const [clientId, setClientId] = useState('')
  const [secret, setSecret] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const next = await call<Saved>('/connections/google', { method: 'POST', json: { client_id: clientId.trim(), client_secret: secret } })
      setClientId('')
      setSecret('')
      onSaved(next)
    } catch (err) {
      setError(err instanceof AppError ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-2">
      <ol className="space-y-1.5 text-sm text-body">
        {guide.steps.map((s, i) => (
          <li key={s} className="flex gap-2.5">
            <span className="grid size-5 shrink-0 place-items-center rounded-full bg-lilac-100 text-xs font-medium text-violet-700">{i + 1}</span>
            <span>{s}</span>
          </li>
        ))}
      </ol>
      {guide.redirect_uri && <RedirectUri value={guide.redirect_uri} />}
      {guide.signin_redirect_uri && (
        <p className="mt-2 text-xs text-muted">
          The same client can also serve Sign in with Google; its redirect URI is{' '}
          <span className="font-mono break-all">{guide.signin_redirect_uri}</span>.
        </p>
      )}
      <p className="mt-2 text-xs text-muted">
        Scopes: <span className="font-mono">calendar.readonly</span> when you connect, and{' '}
        <span className="font-mono">calendar.events</span> only if you switch on calendar actions under Capabilities.
      </p>
      {guide.url && (
        <a
          href={guide.url}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-lilac-100 px-3.5 py-2 text-sm font-medium text-violet-700 hover:bg-lilac-200"
        >
          Open Google Cloud credentials <ExternalLink className="size-3.5" />
        </a>
      )}
      <form onSubmit={save} className="mt-3 space-y-2" autoComplete="off">
        <label className="block">
          <span className="text-xs font-medium text-ink">Client ID</span>
          <input
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
            placeholder="1234…-abc.apps.googleusercontent.com"
            autoComplete="off"
            spellCheck={false}
            className="mt-1 w-full rounded-xl bg-mist px-3 py-2.5 font-mono text-base text-ink outline-none md:text-sm focus:ring-2 focus:ring-violet-400/50"
          />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-ink">Client secret</span>
          <input
            type="password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            placeholder="GOCSPX-…"
            autoComplete="off"
            spellCheck={false}
            className="mt-1 w-full rounded-xl bg-mist px-3 py-2.5 font-mono text-base text-ink outline-none md:text-sm focus:ring-2 focus:ring-violet-400/50"
          />
        </label>
        {error && <Notice tone="error">{error}</Notice>}
        <Button tone="violet" type="submit" className="w-full py-3" disabled={busy || !clientId.trim() || !secret.trim()}>
          {busy ? 'Saving…' : 'Save OAuth client'}
        </Button>
        {onCancel && (
          <button type="button" onClick={onCancel} className="w-full py-1 text-xs text-muted hover:text-ink">
            Keep the saved client
          </button>
        )}
        <p className="text-center text-xs text-muted">Kept encrypted in {where}. The secret is never shown again.</p>
      </form>
    </div>
  )
}

function RedirectUri({ value }: { value: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="mt-3">
      <p className="text-xs font-medium text-ink">Authorised redirect URI (paste exactly)</p>
      <div className="mt-1 flex items-center gap-2 rounded-xl bg-mist px-3 py-2">
        <code className="min-w-0 flex-1 truncate text-xs text-ink" aria-label="Redirect URI">
          {value}
        </code>
        <button
          type="button"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(value)
              setCopied(true)
              window.setTimeout(() => setCopied(false), 2000)
            } catch {
              setCopied(false)
            }
          }}
          className="inline-flex shrink-0 items-center gap-1 rounded-full bg-white px-2.5 py-1 text-xs font-medium text-violet-700"
        >
          {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />} {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
    </div>
  )
}

// Connected, but the skill that reads the calendar is off: point to the Capabilities page.
function SkillNudge() {
  const [installed, setInstalled] = useState<boolean | null>(null)
  useEffect(() => {
    let live = true
    call<SkillCard[]>('/skills')
      .then((skills) => live && setInstalled(Boolean(skills.find((s) => s.id === SKILL_ID)?.installed)))
      .catch(() => live && setInstalled(null))
    return () => {
      live = false
    }
  }, [])
  if (installed !== false) return null
  return (
    <p className="flex flex-wrap items-center gap-2 rounded-xl bg-lilac-50 px-3 py-2 text-sm text-ink">
      <Puzzle className="size-4 text-violet-600" /> One more step: turn on the Google Calendar capability so I use it.
      <Link href="/dashboard/capabilities" className="font-medium text-violet-700 hover:underline">
        Enable it on the Capabilities page
      </Link>
    </p>
  )
}
