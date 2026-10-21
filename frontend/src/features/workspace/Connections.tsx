'use client'

import { Check, ChevronDown, ExternalLink, KeyRound } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { AppError, call, type Me } from '@/lib/api'
import { BrandIcon } from '@/components/BrandIcon'
import { Button, Notice } from '@/components/site'
import { GoogleCalendarRow, type GoogleGuide, type GoogleService } from '@/features/workspace/GoogleCalendar'

// Connect Claude, Telegram, Slack and Google Calendar from the app. Keys are checked with the
// service, then kept encrypted on this computer; the app only ever shows a hint.

type Service = {
  connected: boolean
  via?: 'app' | 'env' | null
  hints?: Record<string, string>
  meta?: Record<string, string | null>
  available?: boolean
  reason?: string
}
type Status = {
  services: Record<'anthropic' | 'telegram' | 'slack' | 'whatsapp', Service> & { google: GoogleService }
  guides: Record<string, { url: string; steps: string[] }> & { google: GoogleGuide }
  keys_kept_in: string
}

const FIELDS: Record<string, { name: string; label: string; placeholder: string }[]> = {
  anthropic: [{ name: 'api_key', label: 'Claude API key', placeholder: 'sk-ant-…' }],
  telegram: [{ name: 'bot_token', label: 'Bot token from BotFather', placeholder: '123456789:AAE…' }],
  slack: [
    { name: 'bot_token', label: 'Bot User OAuth Token', placeholder: 'xoxb-…' },
    { name: 'app_token', label: 'App-level token', placeholder: 'xapp-…' },
  ],
}
const OPEN_LABEL: Record<string, string> = {
  anthropic: 'Open the Claude Console',
  telegram: 'Open BotFather',
  slack: 'Create my Slack app',
}

export function Connections({ agentName, me, refreshMe }: { agentName: string; me: Me; refreshMe: () => Promise<Me | null> }) {
  const [status, setStatus] = useState<Status | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [googleOutcome, setGoogleOutcome] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setStatus(await call<Status>('/connections'))
      // Back from Google sign-in: /dashboard/connections?google=<outcome>. Show it once, then tidy the URL.
      const params = new URLSearchParams(window.location.search)
      const outcome = params.get('google')
      if (outcome) {
        setGoogleOutcome(outcome)
        setOpen('google')
        params.delete('google')
        const rest = params.toString()
        window.history.replaceState(window.history.state, '', `${window.location.pathname}${rest ? `?${rest}` : ''}${window.location.hash}`)
      }
    } catch (e) {
      setError(e instanceof AppError ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])


  if (!status) return error ? <Notice tone="error">{error}</Notice> : <p className="text-sm text-muted">Checking connections…</p>

  const rows = [
    { key: 'anthropic', brand: 'claude', name: `Claude, ${agentName}'s brain`, why: 'Real replies and reading your files' },
    { key: 'telegram', brand: 'telegram', name: 'Telegram', why: 'Message me from your phone' },
    { key: 'slack', brand: 'slack', name: 'Slack', why: 'Your team can reach me in Slack' },
  ] as const
  const where = status.keys_kept_in === 'keychain' ? 'your Mac’s Keychain' : status.keys_kept_in

  return (
    <div className="space-y-2">
      {rows.map(({ key, brand, name, why }) => {
        const s = status.services[key]
        const expanded = open === key
        return (
          <div key={key} className="overflow-hidden rounded-2xl bg-white">
            <button
              onClick={() => setOpen(expanded ? null : key)}
              aria-expanded={expanded}
              className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-lilac-50"
            >
              <span className="grid size-9 shrink-0 place-items-center rounded-full bg-lilac-100">
                <BrandIcon brand={brand} size={18} decorative />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium text-ink">{name}</span>
                <span className="block truncate text-xs text-muted">
                  {s.connected
                    ? s.via === 'env'
                      ? 'Connected from the .env file'
                      : `Connected${s.meta?.username ? ` as @${s.meta.username}` : s.meta?.team ? ` to ${s.meta.team}` : ''} (${Object.values(s.hints ?? {})[0] ?? ''})`
                    : why}
                </span>
              </span>
              {s.connected ? (
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
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.25 }}
                >
                  <ConnectForm
                    provider={key}
                    service={s}
                    guide={status.guides[key]}
                    where={where}
                    onSaved={async (next) => {
                      setStatus(next)
                      setOpen(key === 'telegram' && next.services.telegram.connected ? 'telegram' : null)
                      await refreshMe()
                    }}
                    me={me}
                    refreshMe={refreshMe}
                  />
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )
      })}
      <GoogleCalendarRow
        service={status.services.google}
        guide={status.guides.google}
        where={where}
        expanded={open === 'google'}
        onToggle={() => setOpen(open === 'google' ? null : 'google')}
        outcome={googleOutcome}
        onChanged={(next) => {
          setGoogleOutcome(null)
          setStatus((prev) => (prev ? { ...prev, services: { ...prev.services, google: next.services.google } } : prev))
        }}
      />
      <div className="flex items-center gap-3 rounded-2xl bg-white/60 px-4 py-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-full bg-mist">
          <BrandIcon brand="whatsapp" size={18} decorative />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-ink">WhatsApp</span>
          <span className="block text-xs text-muted">{status.services.whatsapp.reason}</span>
        </span>
        <span className="rounded-full bg-mist px-2.5 py-1 text-xs text-muted">Not available</span>
      </div>
      <p className="flex items-center gap-1.5 px-1 pt-1 text-xs text-muted">
        <KeyRound className="size-3.5" /> Keys are checked with each service, then kept encrypted on this computer ({where}). They're never shown
        again.
      </p>
    </div>
  )
}

function ConnectForm({
  provider,
  service,
  guide,
  where,
  onSaved,
  me,
  refreshMe,
}: {
  provider: 'anthropic' | 'telegram' | 'slack'
  service: Service
  guide: { url: string; steps: string[] }
  where: string
  onSaved: (s: Status) => Promise<void>
  me: Me
  refreshMe: () => Promise<Me | null>
}) {
  const [values, setValues] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fields = FIELDS[provider]

  async function save(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const next = await call<Status>(`/connections/${provider}`, { method: 'POST', json: values })
      setValues({})
      await onSaved(next)
    } catch (err) {
      setError(err instanceof AppError ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function disconnect() {
    setBusy(true)
    try {
      await onSaved(await call<Status>(`/connections/${provider}/disconnect`, { method: 'POST' }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="border-t border-line px-4 pt-3 pb-4">
      {provider === 'telegram' && service.connected && <TelegramPairing me={me} refreshMe={refreshMe} username={service.meta?.username ?? null} />}
      {!(service.connected && service.via === 'app') && (
        <>
          <ol className="space-y-1.5 text-sm text-body">
            {guide.steps.map((s, i) => (
              <li key={s} className="flex gap-2.5">
                <span className="grid size-5 shrink-0 place-items-center rounded-full bg-lilac-100 text-xs font-medium text-violet-700">{i + 1}</span>
                <span>{s}</span>
              </li>
            ))}
          </ol>
          {guide.url && (
            <a
              href={guide.url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-lilac-100 px-3.5 py-2 text-sm font-medium text-violet-700 hover:bg-lilac-200"
            >
              {OPEN_LABEL[provider]} <ExternalLink className="size-3.5" />
            </a>
          )}
          <form onSubmit={save} className="mt-3 space-y-2" autoComplete="off">
            {fields.map((f) => (
              <label key={f.name} className="block">
                <span className="text-xs font-medium text-ink">{f.label}</span>
                <input
                  type="password"
                  value={values[f.name] ?? ''}
                  onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
                  placeholder={f.placeholder}
                  autoComplete="off"
                  spellCheck={false}
                  className="mt-1 w-full rounded-xl bg-mist px-3 py-2.5 font-mono text-sm text-ink outline-none focus:ring-2 focus:ring-violet-400/50"
                />
              </label>
            ))}
            {error && <Notice tone="error">{error}</Notice>}
            <Button tone="violet" type="submit" className="w-full py-3" disabled={busy || fields.some((f) => !(values[f.name] ?? '').trim())}>
              {busy ? 'Checking…' : 'Check and save'}
            </Button>
            <p className="text-center text-xs text-muted">Kept encrypted in {where}. Never shown again.</p>
          </form>
        </>
      )}
      {service.connected && service.via === 'app' && (
        <button onClick={() => void disconnect()} disabled={busy} className="mt-3 w-full rounded-full py-2 text-sm text-muted hover:text-danger">
          Disconnect {provider === 'anthropic' ? 'Claude' : provider === 'telegram' ? 'Telegram' : 'Slack'}
        </button>
      )}
    </div>
  )
}

// After the bot is connected: link the owner's own Telegram account with a one-time link.
function TelegramPairing({ me, refreshMe, username }: { me: Me; refreshMe: () => Promise<Me | null>; username: string | null }) {
  const [url, setUrl] = useState<string | null>(null)
  const [linked, setLinked] = useState(me.channels.telegram)

  useEffect(() => {
    if (!url || linked) return
    const t = window.setInterval(async () => {
      const m = await refreshMe()
      if (m?.channels.telegram) setLinked(true)
    }, 3000)
    return () => window.clearInterval(t)
  }, [url, linked, refreshMe])

  if (linked)
    return (
      <p className="mb-3 flex items-center gap-2 rounded-xl bg-sky-100 px-3 py-2 text-sm text-ink">
        <Check className="size-4 text-ok" /> Your Telegram is linked. Messages from you there are from you.
      </p>
    )
  return (
    <div className="mb-3 rounded-xl bg-sky-100 px-3 py-3 text-sm text-ink">
      <p>
        Bot connected{username ? ` (@${username})` : ''}. Now link <strong>your</strong> Telegram, so I know it's you there.
      </p>
      {url ? (
        <a href={url} target="_blank" rel="noopener noreferrer" className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-white px-3 py-1.5 font-medium text-[#1f6fa8]">
          Open Telegram and press Start <ExternalLink className="size-3.5" />
        </a>
      ) : (
        <button
          onClick={async () => {
            const r = await call<{ available: boolean; url?: string }>('/channels/telegram', { method: 'POST' })
            if (r.url) setUrl(r.url)
          }}
          className="mt-2 rounded-full bg-ink px-3 py-1.5 text-xs font-medium text-white"
        >
          Link my Telegram
        </button>
      )}
    </div>
  )
}
