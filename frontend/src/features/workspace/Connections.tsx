'use client'

import { AlertTriangle, AudioLines, Check, ChevronDown, Copy, ExternalLink, KeyRound, ShieldAlert } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { call, type Me } from '@/lib/api'
import { ActionError, LoadError } from '@/components/load-state'
import { errorMessage, useAction, UserError } from '@/lib/action'
import { BrandIcon } from '@/components/BrandIcon'
import { Button, Notice } from '@/components/site'
import { GoogleCalendarRow, type GoogleGuide, type GoogleService } from '@/features/workspace/GoogleCalendar'
import { LinkedAccounts, type LinkedAccount } from '@/features/workspace/LinkedAccounts'

// Connect Telegram, Slack, Google Calendar and the voice call's speech services from the app. Keys are checked with the
// service, then kept encrypted on this computer; the app only ever shows a hint.

type Service = {
  connected: boolean
  via?: 'app' | 'env' | null
  hints?: Record<string, string>
  meta?: Record<string, string | null>
  available?: boolean
  reason?: string
  // Telegram and Slack: whether the service still accepts the saved token (backend channels/health.py).
  health?: { status: 'ok' | 'auth_failed' | 'unreachable'; last_error?: string; since?: string | null }
}

// Saved but refused by the service (revoked or regenerated token): needs a new one.
const rejected = (s: Service) => s.connected && s.health?.status === 'auth_failed'

type Status = {
  services: Record<'telegram' | 'slack' | 'whatsapp' | 'deepgram' | 'elevenlabs', Service> & { google: GoogleService }
  guides: Record<string, { url: string; steps: string[] }> & { google: GoogleGuide }
  keys_kept_in: string
}

const FIELDS: Record<string, { name: string; label: string; placeholder: string }[]> = {
  telegram: [{ name: 'bot_token', label: 'Bot token from BotFather', placeholder: '123456789:AAE…' }],
  slack: [
    { name: 'bot_token', label: 'Bot User OAuth Token', placeholder: 'xoxb-…' },
    { name: 'app_token', label: 'App-level token', placeholder: 'xapp-…' },
  ],
  deepgram: [{ name: 'api_key', label: 'Deepgram API key', placeholder: 'Paste your Deepgram key' }],
  elevenlabs: [{ name: 'api_key', label: 'ElevenLabs API key', placeholder: 'sk_…' }],
}
const NAMES: Record<Provider, string> = {
  telegram: 'Telegram',
  slack: 'Slack',
  deepgram: 'Deepgram',
  elevenlabs: 'ElevenLabs',
}
type Provider = 'telegram' | 'slack' | 'deepgram' | 'elevenlabs'
const VOICE: ReadonlySet<Provider> = new Set(['deepgram', 'elevenlabs'])
const OPEN_LABEL: Record<string, string> = {
  telegram: 'Open BotFather',
  slack: 'Create my Slack app',
  deepgram: 'Open the Deepgram console',
  elevenlabs: 'Open ElevenLabs API keys',
}

export function Connections({ agentName, me, refreshMe }: { agentName: string; me: Me; refreshMe: () => Promise<Me | null> }) {
  const [status, setStatus] = useState<Status | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [googleOutcome, setGoogleOutcome] = useState<string | null>(null)
  const [linked, setLinked] = useState<LinkedAccount[]>([])

  const reloadLinked = useCallback(async () => {
    try {
      const rows = (await call<{ accounts: LinkedAccount[] }>('/channels/linked')).accounts
      setLinked(rows)
      const waiting = rows.find((a) => a.pending)
      if (waiting) setOpen((o) => o ?? waiting.provider)
    } catch {
      setLinked([])
    }
  }, [])

  // State is set only once the request settles (written with .then so the effect lint can see that).
  const load = useCallback(
    () =>
      call<Status>('/connections')
        .then((s) => {
          setStatus(s)
          void reloadLinked()
          // Back from Google sign-in: /dashboard/connections?google=<outcome>. Show it once, then tidy the URL.
          const params = new URLSearchParams(window.location.search)
          const outcome = params.get('google')
          if (outcome) {
            // Only say "connected" if Google really is: anyone can send a link with ?google=connected.
            if (outcome !== 'connected' || s.services.google.connected) {
              setGoogleOutcome(outcome)
              setOpen('google')
            }
            params.delete('google')
            const rest = params.toString()
            window.history.replaceState(window.history.state, '', `${window.location.pathname}${rest ? `?${rest}` : ''}${window.location.hash}`)
          }
        })
        .catch((e: unknown) => {
          setError(errorMessage(e))
        }),
    [reloadLinked],
  )

  useEffect(() => {
    void load()
  }, [load])


  if (!status)
    return error ? (
      <LoadError what="your connections" error={error} onRetry={() => load()} />
    ) : (
      <p className="text-sm text-muted">Checking connections…</p>
    )

  const rows = [
    { key: 'telegram', brand: 'telegram', name: 'Telegram', why: 'Message me from your phone' },
    { key: 'slack', brand: 'slack', name: 'Slack', why: 'Your team can reach me in Slack' },
    // The voice call (ADR 0012). Optional: without them, your browser's own speech is used.
    { key: 'deepgram', brand: null, name: 'Deepgram, for the voice call', why: 'Hears you on the call (and a voice, in English)' },
    { key: 'elevenlabs', brand: null, name: 'ElevenLabs, for the voice call', why: `A natural voice for ${agentName}` },
  ] as const
  // Where the vault's master key lives (backend vault/crypto.py): the Keychain on a Mac, settings on a server.
  const KEY_PLACES: Record<string, string> = {
    keychain: 'your Mac’s Keychain',
    environment: 'the server’s vault',
    'secret file': 'the server’s vault',
  }
  const where = KEY_PLACES[status.keys_kept_in] ?? 'a private key file'

  return (
    <div className="space-y-2">
      {rows.map(({ key, brand, name, why }) => {
        const s = status.services[key]
        const expanded = open === key
        const waiting = linked.some((a) => a.provider === key && a.pending)
        return (
          <div key={key} className="overflow-hidden rounded-2xl bg-white">
            <button
              onClick={() => setOpen(expanded ? null : key)}
              aria-expanded={expanded}
              className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-lilac-50"
            >
              <span className="grid size-9 shrink-0 place-items-center rounded-full bg-lilac-100">
                {brand ? <BrandIcon brand={brand} size={18} decorative /> : <AudioLines className="size-[18px] text-violet-700" aria-hidden />}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium text-ink">{name}</span>
                <span className={`block truncate text-xs ${rejected(s) ? 'text-warn' : 'text-muted'}`}>
                  {rejected(s)
                    ? `Token rejected: reconnect${s.meta?.username ? ` (@${s.meta.username})` : ''}`
                    : s.connected
                      ? s.via === 'env'
                        ? 'Connected from the .env file'
                        : `Connected${s.meta?.username ? ` as @${s.meta.username}` : s.meta?.team ? ` to ${s.meta.team}` : ''} (${Object.values(s.hints ?? {})[0] ?? ''})${s.health?.status === 'unreachable' ? ' · couldn’t be reached lately' : ''}`
                      : why}
                </span>
              </span>
              {rejected(s) ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-warn/10 px-2.5 py-1 text-xs font-medium text-warn">
                  <AlertTriangle className="size-3.5" /> Reconnect
                </span>
              ) : waiting ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700">
                  <ShieldAlert className="size-3.5" /> Confirm
                </span>
              ) : s.connected ? (
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
                    linked={linked}
                    onLinkedChanged={async () => {
                      await reloadLinked()
                      await refreshMe()
                    }}
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
        <KeyRound className="size-3.5" /> Keys are checked with each service, then kept encrypted ({where}). They're never shown
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
  linked,
  onLinkedChanged,
}: {
  provider: Provider
  service: Service
  guide: { url: string; steps: string[] }
  where: string
  onSaved: (s: Status) => Promise<void>
  me: Me
  refreshMe: () => Promise<Me | null>
  linked: LinkedAccount[]
  onLinkedChanged: () => Promise<void>
}) {
  const [values, setValues] = useState<Record<string, string>>({})
  // Saving and disconnecting share one busy flag and one error line, shown whichever form is on screen.
  const { run, busy, error } = useAction()
  const [failed, setFailed] = useState<'save' | 'disconnect'>('save')
  const fields = FIELDS[provider]
  const label = NAMES[provider]

  function save(e: FormEvent) {
    e.preventDefault()
    setFailed('save')
    void run(async () => {
      const next = await call<Status>(`/connections/${provider}`, { method: 'POST', json: values })
      setValues({})
      await onSaved(next)
    })
  }

  function disconnect() {
    setFailed('disconnect')
    void run(async () => onSaved(await call<Status>(`/connections/${provider}/disconnect`, { method: 'POST' })))
  }

  return (
    <div className="border-t border-line px-4 pt-3 pb-4">
      {rejected(service) && (
        <div className="mb-3">
          <Notice tone="warn">
            {provider === 'telegram' ? 'Telegram' : 'Slack'} rejected the saved token
            {service.health?.since ? ` (since ${new Date(service.health.since).toLocaleString()})` : ''}, so nothing reaches you there.
            It was probably revoked or regenerated.{' '}
            {provider === 'telegram'
              ? 'In BotFather, send /token and pick your bot to get its current token, then paste it below.'
              : 'Reinstall your Slack app (OAuth & Permissions) and paste the new tokens below.'}
          </Notice>
        </div>
      )}
      {(provider === 'telegram' || provider === 'slack') && service.connected && (
        <LinkedAccounts provider={provider} accounts={linked} onChanged={onLinkedChanged} />
      )}
      {provider === 'telegram' && service.connected && !rejected(service) && !linked.some((a) => a.provider === 'telegram' && a.mine) && (
        <TelegramPairing me={me} refreshMe={refreshMe} username={service.meta?.username ?? null} />
      )}
      {provider === 'slack' && service.connected && !rejected(service) && !linked.some((a) => a.provider === 'slack' && a.mine) && (
        <SlackPairing me={me} refreshMe={refreshMe} onLinked={onLinkedChanged} />
      )}
      {(!(service.connected && service.via === 'app') || rejected(service)) && (
        <>
          <ol className="space-y-1.5 text-sm text-body">
            {guide.steps.map((s, i) => (
              <li key={s} className="flex gap-2.5">
                <span className="grid size-5 shrink-0 place-items-center rounded-full bg-lilac-100 text-xs font-medium text-violet-700">{i + 1}</span>
                <span>{s}</span>
              </li>
            ))}
          </ol>
          {VOICE.has(provider) && (
            <p className="mt-2 text-xs text-muted">Optional. Without it, {"the voice call uses your browser's own speech"}. Audio is never stored.</p>
          )}
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
                  className="mt-1 w-full rounded-xl bg-mist px-3 py-2.5 font-mono text-base text-ink outline-none md:text-sm focus:ring-2 focus:ring-violet-400/50"
                />
              </label>
            ))}
            <Button tone="violet" type="submit" className="w-full py-3" disabled={busy || fields.some((f) => !(values[f.name] ?? '').trim())}>
              {busy ? 'Checking…' : rejected(service) ? 'Check and reconnect' : 'Check and save'}
            </Button>
            <p className="text-center text-xs text-muted">Kept encrypted in {where}. Never shown again.</p>
          </form>
        </>
      )}
      <div className="mt-3 empty:hidden">
        <ActionError action={failed === 'save' ? `connect ${label}` : `disconnect ${label}`} error={error} />
      </div>
      {service.connected && service.via === 'app' && (
        <button onClick={disconnect} disabled={busy} className="mt-3 w-full rounded-full py-2 text-sm text-muted hover:text-danger">
          Disconnect {label}
        </button>
      )}
    </div>
  )
}

// After the bot is connected: link the owner's own Telegram account with a one-time link.
function TelegramPairing({ me, refreshMe, username }: { me: Me; refreshMe: () => Promise<Me | null>; username: string | null }) {
  const [url, setUrl] = useState<string | null>(null)
  const [linked, setLinked] = useState(me.channels.telegram)
  const { run, busy, error } = useAction()

  const link = () =>
    run(async () => {
      const r = await call<{ available: boolean; url?: string }>('/channels/telegram', { method: 'POST' })
      if (!r.url) throw new UserError("Telegram didn't answer for this bot. Check it is still connected.")
      setUrl(r.url)
    })

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
          onClick={() => void link()}
          disabled={busy}
          className="mt-2 rounded-full bg-ink px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60"
        >
          Link my Telegram
        </button>
      )}
      {error && (
        <div className="mt-2">
          <ActionError action="make the link" error={error} />
        </div>
      )}
    </div>
  )
}

// After the Slack app is connected: link the owner's own Slack account. Slack has no start link, so the owner types a
// one-time code in a direct message to Ava. The code is shown only here, in the signed-in app.
function SlackPairing({ me, refreshMe, onLinked }: { me: Me; refreshMe: () => Promise<Me | null>; onLinked: () => Promise<void> }) {
  const [invite, setInvite] = useState<{ code: string; instruction: string; expiresAt: number } | null>(null)
  const [linked, setLinked] = useState(me.channels.slack)
  const [now, setNow] = useState(() => Date.now())
  const [copied, setCopied] = useState(false)
  const { run, busy, error } = useAction()

  const make = () =>
    run(async () => {
      const r = await call<{ code: string; instruction: string; expires_at: string }>('/channels/slack/invite', { method: 'POST' })
      setInvite({ code: r.code, instruction: r.instruction, expiresAt: new Date(r.expires_at).getTime() })
      setCopied(false)
      setNow(Date.now())
    })

  const left = invite ? Math.max(0, Math.round((invite.expiresAt - now) / 1000)) : 0
  const live = invite !== null && left > 0

  useEffect(() => {
    if (!live || linked) return
    const tick = window.setInterval(() => setNow(Date.now()), 1000)
    const poll = window.setInterval(async () => {
      const m = await refreshMe()
      if (m?.channels.slack) {
        setLinked(true)
        await onLinked()
      }
    }, 3000)
    return () => {
      window.clearInterval(tick)
      window.clearInterval(poll)
    }
  }, [live, linked, refreshMe, onLinked])

  async function copy() {
    if (!invite) return
    try {
      await navigator.clipboard.writeText(`link ${invite.code}`)
      setCopied(true)
    } catch {
      setCopied(false) // the sentence is on screen to select by hand
    }
  }

  if (linked)
    return (
      <p className="mb-3 flex items-center gap-2 rounded-xl bg-sky-100 px-3 py-2 text-sm text-ink">
        <Check className="size-4 text-ok" /> Your Slack account is linked. Messages from you there are from you.
      </p>
    )
  return (
    <div className="mb-3 rounded-xl bg-sky-100 px-3 py-3 text-sm text-ink">
      <p>
        Slack app connected. Now link <strong>your</strong> Slack account, so I know it's you there.
      </p>
      {live && invite ? (
        <div className="mt-2 space-y-2">
          <p>
            Open a direct message with {me.agent.name} in Slack and send: <strong>link {invite.code}</strong>
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <button onClick={() => void copy()} className="inline-flex items-center gap-1.5 rounded-full bg-white px-3 py-1.5 text-xs font-medium text-ink">
              <Copy className="size-3.5" aria-hidden /> {copied ? 'Copied' : 'Copy the message'}
            </button>
            <span className="text-xs text-muted" aria-live="off">
              Works once, for {Math.floor(left / 60)}:{String(left % 60).padStart(2, '0')} more.
            </span>
          </div>
          <p className="text-xs text-muted">Send it in a direct message, never in a channel.</p>
        </div>
      ) : (
        <>
          {invite && <p className="mt-2 text-muted">That code has expired. Make a new one.</p>}
          <button
            onClick={() => void make()}
            disabled={busy}
            className="mt-2 rounded-full bg-ink px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60"
          >
            Link my Slack account
          </button>
        </>
      )}
      {error && (
        <div className="mt-2">
          <ActionError action="make the code" error={error} />
        </div>
      )}
    </div>
  )
}
