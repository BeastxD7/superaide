'use client'

import { CheckCircle2, ExternalLink, Globe, KeyRound, Loader2, PlugZap, Server } from 'lucide-react'
import Link from 'next/link'
import { useMemo, useState } from 'react'
import { BrandIcon, type Brand } from '@/components/BrandIcon'
import { Notice } from '@/components/site'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { useAppData } from '@/features/dashboard/data'
import { AppError, call, type ConnectionTest, type ModelSettings, type ProviderPreset, type RoleName } from '@/lib/api'
import { useSession } from '@/lib/session-context'
import { errorMessage } from '@/lib/action'

// Each provider's own mark (components/brands/marks.tsx); servers without one keep a generic icon.
const PROVIDER_BRAND: Record<string, Brand> = {
  anthropic: 'claude',
  openai: 'openai',
  azure: 'azure',
  openrouter: 'openrouter',
  xai: 'xai',
  gemini: 'gemini',
  ollama: 'ollama',
  vllm: 'vllm',
}

const ROLES: { id: RoleName; label: string; why: string }[] = [
  { id: 'chat', label: 'Conversation', why: 'Replies to you and the people you work with.' },
  {
    id: 'triage',
    label: 'Quick reading',
    why: 'Reads files, links and notes to learn facts. A small, cheap model is fine.',
  },
  { id: 'deep', label: 'Deep work', why: 'Longer research and harder questions.' },
]

// A price typed into the page: digits with an optional dot, like 1.50. Anything else is refused here, before
// it is sent (a comma or letters would otherwise reach the server as something it can't read).
const PRICE_HELP = 'Enter the price as a number with a dot, like 1.50.'
function parsePrice(text: string): number | null {
  const t = text.trim().replace(',', '.') // many keyboards and locales type 0,15
  if (!/^\d+(\.\d+)?$/.test(t)) return null
  const n = Number(t)
  return n <= 1000 ? n : null
}

function errorText(e: unknown): string {
  return errorMessage(e)
}

export function ModelsPage() {
  const { me } = useSession()
  const name = me?.agent.name ?? 'Ava'
  const { data, error, reload } = useAppData<ModelSettings>('/providers')
  const [open, setOpen] = useState<string | null>(null)
  const [adding, setAdding] = useState<{ preset: string; key: string } | null>(null)

  const connected = useMemo(() => {
    if (!data) return new Set<string>()
    const ids = data.providers.map((p) => p.key)
    return new Set(data.claude.connected ? ['anthropic', ...ids] : ids)
  }, [data])
  const presetOf = (key: string) => data?.providers.find((p) => p.key === key)?.preset ?? key
  const label = (key: string) => {
    if (key === 'platform') return 'Superaide AI credits'
    const base = data?.presets.find((p) => p.id === presetOf(key))?.label ?? key
    return key === presetOf(key) ? base : `${base} (${key})`
  }
  // Another endpoint of the same kind gets the next free key: custom-2, custom-3…
  const nextKey = (preset: string) => {
    const taken = new Set(data?.providers.map((p) => p.key))
    let n = 2
    while (taken.has(`${preset}-${n}`)) n += 1
    return `${preset}-${n}`
  }
  const changed = async () => {
    setOpen(null)
    setAdding(null)
    await reload()
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-4 py-6 sm:px-6 lg:py-8">
        <h1 className="text-3xl font-medium text-ink tight">AI model</h1>
        <p className="mt-1 text-body">
          Choose the AI that {name} thinks with: bring your own key from any provider below
          {data?.credits.available ? ', or use Superaide AI credits with no key at all' : ''}. Your own key is always
          used first. Keys are checked with the service, kept encrypted, and never shown again. Every call counts
          against your spending cap.
        </p>
        {me && !me.onboarding.done && (
          <div className="mt-4">
            <Notice tone="info">
              You are in the middle of meeting {name}.{' '}
              <Link href="/meet" className="font-medium underline">
                Back to the introduction
              </Link>{' '}
              once a model is connected.
            </Notice>
          </div>
        )}
        {error && (
          <div className="mt-4">
            <Notice tone="error">{error}</Notice>
          </div>
        )}
        {data?.key_status?.message && (
          <div className="mt-4">
            <Notice tone="error">
              {data.key_status.message}:{' '}
              {data.key_status.rejected.map((p) => (p === 'credits' ? 'Superaide AI credits' : label(p))).join(', ')}.
              Save a working key below and {name} carries on, including reading your documents.
            </Notice>
          </div>
        )}
        {data && (
          <div className="mt-4">
            {data.brain.ready ? (
              <Notice tone="info">
                {name} is thinking with <strong>{label(data.brain.provider ?? '')}</strong> ({data.brain.model}).
              </Notice>
            ) : (
              <Notice tone="warn">No AI model is connected yet, so {name} can only echo you. Connect one below.</Notice>
            )}
          </div>
        )}

        {data?.credits.available && <CreditsCard settings={data} onChanged={reload} />}

        <h2 className="mt-8 text-lg font-medium text-ink">Providers</h2>
        <ul className="mt-3 grid gap-3 sm:grid-cols-2" aria-label="Providers">
          {!data &&
            [0, 1, 2, 3].map((i) => (
              <li key={i} className="rounded-xl border border-line bg-white p-4">
                <Skeleton className="h-4 w-1/2" />
                <Skeleton className="mt-3 h-3 w-full" />
              </li>
            ))}
          {data?.presets.map((p) => (
            <ProviderCard
              key={p.id}
              preset={p}
              endpointKey={p.id}
              title={p.label}
              settings={data}
              connected={connected.has(p.id)}
              open={open === p.id}
              onOpen={() => setOpen(open === p.id ? null : p.id)}
              onAddAnother={
                p.local && connected.has(p.id) ? () => setAdding({ preset: p.id, key: nextKey(p.id) }) : undefined
              }
              onChanged={changed}
            />
          ))}
          {data?.providers
            .filter((e) => e.key !== e.preset)
            .map((e) => {
              const p = data.presets.find((x) => x.id === e.preset)
              return p ? (
                <ProviderCard
                  key={e.key}
                  preset={p}
                  endpointKey={e.key}
                  title={label(e.key)}
                  settings={data}
                  connected
                  open={open === e.key}
                  onOpen={() => setOpen(open === e.key ? null : e.key)}
                  onChanged={changed}
                />
              ) : null
            })}
          {data && adding && (
            <ProviderCard
              preset={data.presets.find((x) => x.id === adding.preset)!}
              endpointKey={adding.key}
              title={`${label(adding.preset)} (${adding.key})`}
              settings={data}
              connected={false}
              open
              onOpen={() => setAdding(null)}
              onChanged={changed}
            />
          )}
        </ul>

        {data && <WebSearchCard settings={data} onChanged={reload} />}

        {data && (
          <RolePicker
            key={[...connected].join(',')}
            settings={data}
            connected={[...connected]}
            label={label}
            onSaved={reload}
          />
        )}
      </div>
    </div>
  )
}

function ProviderCard({
  preset,
  endpointKey,
  title,
  settings,
  connected,
  open,
  onOpen,
  onAddAnother,
  onChanged,
}: {
  preset: ProviderPreset
  endpointKey: string
  title: string
  settings: ModelSettings
  connected: boolean
  open: boolean
  onOpen: () => void
  onAddAnother?: () => void
  onChanged: () => Promise<void>
}) {
  const mine = settings.providers.find((p) => p.key === endpointKey)
  const hint = preset.id === 'anthropic' ? settings.claude.key_hint : (mine?.key_hint ?? '')
  const fromEnv = preset.id === 'anthropic' && settings.claude.via === 'env'

  return (
    <li className="flex flex-col rounded-xl border border-line bg-white p-4" aria-label={title}>
      <div className="flex items-start gap-3">
        {PROVIDER_BRAND[preset.id] ? (
          <span className="grid size-9 shrink-0 place-items-center rounded-lg border border-line bg-white text-ink">
            <BrandIcon brand={PROVIDER_BRAND[preset.id]} size={20} mono={preset.id === 'xai'} decorative />
          </span>
        ) : (
          <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-lilac-100 text-violet-600">
            {preset.local ? <Server className="size-4.5" aria-hidden /> : <PlugZap className="size-4.5" aria-hidden />}
          </span>
        )}
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-1.5 font-medium text-ink">
            {title}
            {connected && (
              <Badge variant="secondary" className="gap-1 text-violet-700">
                <CheckCircle2 className="size-3" aria-hidden /> Connected
              </Badge>
            )}
          </p>
          {connected && hint && <p className="text-xs text-muted">Key {hint}</p>}
          {fromEnv && <p className="text-xs text-muted">Set up by your administrator</p>}
        </div>
      </div>
      {preset.note && <p className="mt-3 text-sm text-body">{preset.note}</p>}
      {preset.warnings.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-xs text-muted">
          {preset.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}

      {open ? (
        <ConnectForm
          preset={preset}
          endpointKey={endpointKey}
          baseUrl={mine?.base_url || preset.base_url}
          connected={connected}
          hint={hint}
          canRemove={connected && !fromEnv}
          onChanged={onChanged}
        />
      ) : (
        <div className="mt-auto pt-3">
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant={connected ? 'outline' : 'default'} onClick={onOpen}>
              {connected ? 'Change' : 'Connect'}
            </Button>
            {onAddAnother && (
              <Button size="sm" variant="ghost" onClick={onAddAnother}>
                Add another
              </Button>
            )}
          </div>
        </div>
      )}
    </li>
  )
}

function ConnectForm({
  preset,
  endpointKey,
  baseUrl: savedUrl,
  connected,
  hint,
  canRemove,
  onChanged,
}: {
  preset: ProviderPreset
  endpointKey: string
  baseUrl: string
  connected: boolean
  hint: string
  canRemove: boolean
  onChanged: () => Promise<void>
}) {
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState(savedUrl)
  const [busy, setBusy] = useState<'test' | 'save' | 'remove' | null>(null)
  const [result, setResult] = useState<ConnectionTest | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const showUrl = preset.id !== 'anthropic'

  // An empty key field keeps the saved key; the key is only sent when typed.
  const body = () => ({
    ...(apiKey ? { api_key: apiKey } : {}),
    ...(showUrl ? { base_url: baseUrl } : {}),
    ...(endpointKey !== preset.id ? { key: endpointKey } : {}),
  })

  async function run(kind: 'test' | 'save' | 'remove') {
    setBusy(kind)
    setProblem(null)
    try {
      if (kind === 'test') {
        setResult(
          await call<ConnectionTest>('/providers/test', { method: 'POST', json: { preset: preset.id, ...body() } }),
        )
      } else {
        const path = kind === 'save' ? `/providers/${preset.id}` : `/providers/${endpointKey}/remove`
        await call(path, { method: 'POST', json: kind === 'save' ? body() : {} })
        setApiKey('')
        await onChanged()
      }
    } catch (e) {
      setProblem(errorText(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <form
      className="mt-3 space-y-3 rounded-lg bg-mist p-3"
      aria-label={`Connect ${preset.label}`}
      onSubmit={(e) => {
        e.preventDefault()
        void run('save')
      }}
    >
      <div className="space-y-1">
        <Label htmlFor={`${endpointKey}-key`}>API key{!preset.key_required && ' (if your server needs one)'}</Label>
        <Input
          id={`${endpointKey}-key`}
          type="password"
          autoComplete="off"
          value={apiKey}
          placeholder={connected && hint ? 'Leave blank to keep the saved key' : preset.key_hint}
          onChange={(e) => setApiKey(e.target.value)}
        />
      </div>
      {showUrl && (
        <div className="space-y-1">
          <Label htmlFor={`${endpointKey}-url`}>Address (base URL)</Label>
          <Input
            id={`${endpointKey}-url`}
            inputMode="url"
            value={baseUrl}
            placeholder="https://…/v1"
            onChange={(e) => setBaseUrl(e.target.value)}
          />
        </div>
      )}
      {preset.guide_url && (
        <a
          href={preset.guide_url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-xs text-violet-700 underline"
        >
          <KeyRound className="size-3" aria-hidden /> Where to get a key <ExternalLink className="size-3" aria-hidden />
        </a>
      )}
      {result && (
        <Notice tone={result.ok ? 'info' : 'error'}>
          {result.message}
          {result.ok && result.models.length > 0 && ` · ${result.models.length} models available`}
        </Notice>
      )}
      {problem && <Notice tone="error">{problem}</Notice>}
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" variant="outline" disabled={busy !== null} onClick={() => void run('test')}>
          {busy === 'test' && <Loader2 className="animate-spin" aria-hidden />} Test connection
        </Button>
        <Button type="submit" size="sm" disabled={busy !== null}>
          {busy === 'save' && <Loader2 className="animate-spin" aria-hidden />} Save
        </Button>
        {canRemove && (
          <Button type="button" size="sm" variant="ghost" disabled={busy !== null} onClick={() => void run('remove')}>
            Disconnect
          </Button>
        )}
      </div>
    </form>
  )
}

function WebSearchCard({ settings, onChanged }: { settings: ModelSettings; onChanged: () => Promise<void> }) {
  const ws = settings.web_search
  const [open, setOpen] = useState(false)
  const [apiKey, setApiKey] = useState('')
  const [busy, setBusy] = useState<'test' | 'save' | 'remove' | null>(null)
  const [result, setResult] = useState<ConnectionTest | null>(null)
  const [problem, setProblem] = useState<string | null>(null)

  async function run(kind: 'test' | 'save' | 'remove') {
    setBusy(kind)
    setProblem(null)
    try {
      if (kind === 'test') {
        setResult(
          await call<ConnectionTest>('/providers/web-search/test', { method: 'POST', json: { api_key: apiKey } }),
        )
      } else {
        await call(kind === 'save' ? '/providers/web-search' : '/providers/web-search/remove', {
          method: 'POST',
          json: kind === 'save' ? { api_key: apiKey } : {},
        })
        setApiKey('')
        setResult(null)
        setOpen(false)
        await onChanged()
      }
    } catch (e) {
      setProblem(errorText(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="mt-8" aria-label="Web search">
      <h2 className="text-lg font-medium text-ink">Web search</h2>
      <div className="mt-3 rounded-xl border border-line bg-white p-4">
        <div className="flex items-start gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-lilac-100 text-violet-600">
            <Globe className="size-4.5" aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <p className="flex flex-wrap items-center gap-1.5 font-medium text-ink">
              Brave Search
              {ws.connected && (
                <Badge variant="secondary" className="gap-1 text-violet-700">
                  <CheckCircle2 className="size-3" aria-hidden /> Connected
                </Badge>
              )}
            </p>
            {ws.connected && ws.key_hint && <p className="text-xs text-muted">Key {ws.key_hint}</p>}
            {ws.via === 'env' && <p className="text-xs text-muted">Set up by your administrator</p>}
          </div>
        </div>
        <p className="mt-3 text-sm text-body">
          Claude searches the web by itself. Other models search through Brave with your own key. Each search counts
          about US${ws.usd_per_call.toFixed(3)} against your spending cap.
        </p>
        {open ? (
          <form
            className="mt-3 space-y-3 rounded-lg bg-mist p-3"
            aria-label="Connect Brave Search"
            onSubmit={(e) => {
              e.preventDefault()
              void run('save')
            }}
          >
            <div className="space-y-1">
              <Label htmlFor="brave-key">Brave Search API key</Label>
              <Input
                id="brave-key"
                type="password"
                autoComplete="off"
                value={apiKey}
                placeholder="BSA…"
                onChange={(e) => setApiKey(e.target.value)}
              />
            </div>
            <a
              href="https://api-dashboard.search.brave.com/app/keys"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-xs text-violet-700 underline"
            >
              <KeyRound className="size-3" aria-hidden /> Where to get a key{' '}
              <ExternalLink className="size-3" aria-hidden />
            </a>
            {result && <Notice tone={result.ok ? 'info' : 'error'}>{result.message}</Notice>}
            {problem && <Notice tone="error">{problem}</Notice>}
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={busy !== null || !apiKey}
                onClick={() => void run('test')}
              >
                {busy === 'test' && <Loader2 className="animate-spin" aria-hidden />} Test search
              </Button>
              <Button type="submit" size="sm" disabled={busy !== null || !apiKey}>
                {busy === 'save' && <Loader2 className="animate-spin" aria-hidden />} Save
              </Button>
              {ws.via === 'app' && (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={busy !== null}
                  onClick={() => void run('remove')}
                >
                  Turn off
                </Button>
              )}
            </div>
          </form>
        ) : (
          <div className="pt-3">
            <Button size="sm" variant={ws.connected ? 'outline' : 'default'} onClick={() => setOpen(true)}>
              {ws.connected ? 'Change' : 'Add a key'}
            </Button>
          </div>
        )}
      </div>
    </section>
  )
}

function CreditsCard({ settings, onChanged }: { settings: ModelSettings; onChanged: () => Promise<void> }) {
  const c = settings.credits
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const usd = (n: number) => `US$${n.toFixed(2)}`
  // A missing limit means none of your own (only Superaide's shared daily limit applies), never US$0.00.
  const daily = c.allowance.daily_usd
  const monthly = c.allowance.monthly_usd
  const limitText =
    daily !== null && monthly !== null
      ? ` up to ${usd(daily)} a day and ${usd(monthly)} a month`
      : daily !== null
        ? ` up to ${usd(daily)} a day`
        : monthly !== null
          ? ` up to ${usd(monthly)} a month`
          : ', with no limit of your own (Superaide shares a daily limit across everyone)'

  async function toggle() {
    setBusy(true)
    setProblem(null)
    try {
      await call('/providers/credits', { method: 'POST', json: { on: !c.opted_in } })
      await onChanged()
    } catch (e) {
      setProblem(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="mt-8" aria-label="Superaide AI credits">
      <div className="rounded-xl border border-line bg-white p-4">
        <p className="flex flex-wrap items-center gap-1.5 font-medium text-ink">
          Superaide AI credits
          {c.opted_in && (
            <Badge variant="secondary" className="gap-1 text-violet-700">
              <CheckCircle2 className="size-3" aria-hidden /> On
            </Badge>
          )}
        </p>
        <p className="mt-1 text-sm text-body">
          No key needed: use Superaide&apos;s own Claude ({c.model}){limitText}. If you also connect your own key
          below, your key is used first.
        </p>
        <p className="mt-1 text-xs text-muted">
          Used in the last 24 hours: {usd(c.used.today_usd)}
          {daily !== null && ` of ${usd(daily)}`} · last 30 days: {usd(c.used.month_usd)}
          {monthly !== null && ` of ${usd(monthly)}`}
        </p>
        {problem && (
          <div className="mt-3">
            <Notice tone="error">{problem}</Notice>
          </div>
        )}
        <div className="pt-3">
          <Button size="sm" variant={c.opted_in ? 'outline' : 'default'} disabled={busy} onClick={() => void toggle()}>
            {busy && <Loader2 className="animate-spin" aria-hidden />}
            {c.opted_in ? 'Stop using credits' : 'Use Superaide AI credits'}
          </Button>
        </div>
      </div>
    </section>
  )
}

type Choice = { provider: string; model: string; input: string; output: string }

function RolePicker({
  settings,
  connected,
  label,
  onSaved,
}: {
  settings: ModelSettings
  connected: string[]
  label: (id: string) => string
  onSaved: () => Promise<void>
}) {
  const modelsFor = (provider: string) =>
    provider === 'anthropic'
      ? (settings.priced.anthropic ?? [])
      : (settings.providers.find((p) => p.key === provider)?.models ?? [])
  // A role saved on a provider that isn't connected (Claude, by default) starts on the first one that is,
  // with its first model, so Save models works without touching anything.
  const initial = () =>
    Object.fromEntries(
      ROLES.map((r) => {
        const saved = settings.roles[r.id]
        const provider = connected.includes(saved.provider) ? saved.provider : (connected[0] ?? saved.provider)
        const model = provider === saved.provider ? saved.model : (modelsFor(provider)[0] ?? '')
        return [r.id, { provider, model, input: '', output: '' }]
      }),
    ) as Record<RoleName, Choice>
  const [choices, setChoices] = useState<Record<RoleName, Choice>>(initial)
  // The saved roles changed under us (a reload, a provider removed): show them, not what was chosen before.
  const rolesNow = JSON.stringify(settings.roles)
  const [rolesSeen, setRolesSeen] = useState(rolesNow)
  if (rolesSeen !== rolesNow) {
    setRolesSeen(rolesNow)
    setChoices(initial())
  }
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const isPriced = (c: Choice) => (settings.priced[c.provider] ?? []).includes(c.model)
  const update = (role: RoleName, patch: Partial<Choice>) => {
    setSaved(false)
    setChoices((all) => ({ ...all, [role]: { ...all[role], ...patch } }))
  }

  async function save() {
    setBusy(true)
    setProblem(null)
    try {
      for (const role of ROLES) {
        const c = choices[role.id]
        if (!connected.includes(c.provider)) continue
        if (c.model.trim() === '') throw new Error(`Choose a model for ${role.label}.`)
        if (!isPriced(c)) {
          const input = parsePrice(c.input)
          const output = parsePrice(c.output)
          if (input === null || output === null) throw new Error(PRICE_HELP)
          try {
            await call('/providers/prices', {
              method: 'POST',
              json: { provider: c.provider, model: c.model, input_usd_per_mtok: input, output_usd_per_mtok: output },
            })
          } catch (e) {
            // The server's own validation wording isn't for people.
            throw e instanceof AppError && e.status === 422 ? new Error(PRICE_HELP) : e
          }
        }
      }
      // A role on a provider that isn't connected uses the conversation model instead.
      const usable = (c: Choice) => connected.includes(c.provider)
      const roles = Object.fromEntries(
        ROLES.map((r) => {
          const c = usable(choices[r.id]) ? choices[r.id] : choices.chat
          return [r.id, { provider: c.provider, model: c.model }]
        }),
      )
      await call('/providers/roles', { method: 'PUT', json: roles })
      await onSaved()
      setSaved(true)
    } catch (e) {
      setProblem(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  if (connected.length === 0) return null
  return (
    <section className="mt-8" aria-label="Models">
      <h2 className="text-lg font-medium text-ink">Models</h2>
      <p className="mt-1 text-sm text-body">Pick a provider and model for each kind of work.</p>
      <div className="mt-3 space-y-3">
        {ROLES.map((role) => {
          const c = choices[role.id]
          const options = modelsFor(c.provider)
          return (
            <div
              key={role.id}
              className="rounded-xl border border-line bg-white p-4"
              role="group"
              aria-label={role.label}
            >
              <p className="font-medium text-ink">{role.label}</p>
              <p className="text-xs text-muted">{role.why}</p>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <div className="space-y-1">
                  <Label htmlFor={`${role.id}-provider`}>Provider</Label>
                  <select
                    id={`${role.id}-provider`}
                    className="h-9 w-full rounded-md border border-input bg-transparent px-2 text-base focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 md:text-sm"
                    value={c.provider}
                    onChange={(e) =>
                      update(role.id, { provider: e.target.value, model: modelsFor(e.target.value)[0] ?? '' })
                    }
                  >
                    {[...new Set([c.provider, ...connected])].map((id) => (
                      <option key={id} value={id}>
                        {label(id)}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`${role.id}-model`}>Model</Label>
                  <Input
                    id={`${role.id}-model`}
                    list={`${role.id}-models`}
                    value={c.model}
                    onChange={(e) => update(role.id, { model: e.target.value })}
                  />
                  <datalist id={`${role.id}-models`}>
                    {options.map((m) => (
                      <option key={m} value={m} />
                    ))}
                  </datalist>
                </div>
              </div>
              {c.model && !isPriced(c) && (
                <div className="mt-3 grid gap-3 sm:grid-cols-2" aria-label={`Price of ${c.model}`}>
                  <p className="text-xs text-muted sm:col-span-2">
                    Superaide doesn&apos;t know this model&apos;s price. Enter it from {label(c.provider)}&apos;s price
                    page (US$ per million tokens) so your spending cap stays accurate.
                  </p>
                  <div className="space-y-1">
                    <Label htmlFor={`${role.id}-in`}>Input, US$ / 1M tokens</Label>
                    <Input
                      id={`${role.id}-in`}
                      inputMode="decimal"
                      value={c.input}
                      onChange={(e) => update(role.id, { input: e.target.value })}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor={`${role.id}-out`}>Output, US$ / 1M tokens</Label>
                    <Input
                      id={`${role.id}-out`}
                      inputMode="decimal"
                      value={c.output}
                      onChange={(e) => update(role.id, { output: e.target.value })}
                    />
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
      {problem && (
        <div className="mt-3">
          <Notice tone="error">{problem}</Notice>
        </div>
      )}
      <div className="mt-3 flex items-center gap-3">
        <Button disabled={busy || ROLES.some((r) => connected.includes(choices[r.id].provider) && !choices[r.id].model.trim())} onClick={() => void save()}>
          {busy && <Loader2 className="animate-spin" aria-hidden />} Save models
        </Button>
        {saved && <span className="text-sm text-muted">Saved.</span>}
      </div>
    </section>
  )
}
