'use client'

import { CheckCircle2, ExternalLink, Globe, KeyRound, Loader2, PlugZap, Server } from 'lucide-react'
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

function errorText(e: unknown): string {
  return e instanceof AppError ? e.message : String(e)
}

export function ModelsPage() {
  const { me } = useSession()
  const name = me?.agent.name ?? 'Ava'
  const { data, error, reload } = useAppData<ModelSettings>('/providers')
  const [open, setOpen] = useState<string | null>(null)

  const connected = useMemo(() => {
    if (!data) return new Set<string>()
    const ids = data.providers.map((p) => p.preset)
    return new Set(data.claude.connected ? ['anthropic', ...ids] : ids)
  }, [data])
  const label = (id: string) => data?.presets.find((p) => p.id === id)?.label ?? id

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-4 py-6 sm:px-6 lg:py-8">
        <h1 className="text-3xl font-medium text-ink tight">AI model</h1>
        <p className="mt-1 text-body">
          Choose the AI that {name} thinks with. Keys are checked with the service, kept encrypted, and never shown
          again. Every call counts against your spending cap.
        </p>
        {error && (
          <div className="mt-4">
            <Notice tone="error">{error}</Notice>
          </div>
        )}
        {data && (
          <div className="mt-4">
            {data.brain.ready ? (
              <Notice tone="info">
                {name} is thinking with <strong>{label(data.brain.provider ?? '')}</strong> ({data.roles.chat.model}).
              </Notice>
            ) : (
              <Notice tone="warn">No AI model is connected yet, so {name} can only echo you. Connect one below.</Notice>
            )}
          </div>
        )}

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
              settings={data}
              connected={connected.has(p.id)}
              open={open === p.id}
              onOpen={() => setOpen(open === p.id ? null : p.id)}
              onChanged={async () => {
                setOpen(null)
                await reload()
              }}
            />
          ))}
        </ul>

        {data && <WebSearchCard settings={data} onChanged={reload} />}

        {data && <RolePicker settings={data} connected={[...connected]} label={label} onSaved={reload} />}
      </div>
    </div>
  )
}

function ProviderCard({
  preset,
  settings,
  connected,
  open,
  onOpen,
  onChanged,
}: {
  preset: ProviderPreset
  settings: ModelSettings
  connected: boolean
  open: boolean
  onOpen: () => void
  onChanged: () => Promise<void>
}) {
  const mine = settings.providers.find((p) => p.preset === preset.id)
  const hint = preset.id === 'anthropic' ? settings.claude.key_hint : (mine?.key_hint ?? '')
  const fromEnv = preset.id === 'anthropic' && settings.claude.via === 'env'

  return (
    <li className="flex flex-col rounded-xl border border-line bg-white p-4" aria-label={preset.label}>
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
            {preset.label}
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
          baseUrl={mine?.base_url || preset.base_url}
          connected={connected}
          hint={hint}
          canRemove={connected && !fromEnv}
          onChanged={onChanged}
        />
      ) : (
        <div className="mt-auto pt-3">
          <Button size="sm" variant={connected ? 'outline' : 'default'} onClick={onOpen}>
            {connected ? 'Change' : 'Connect'}
          </Button>
        </div>
      )}
    </li>
  )
}

function ConnectForm({
  preset,
  baseUrl: savedUrl,
  connected,
  hint,
  canRemove,
  onChanged,
}: {
  preset: ProviderPreset
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
  const body = () => ({ ...(apiKey ? { api_key: apiKey } : {}), ...(showUrl ? { base_url: baseUrl } : {}) })

  async function run(kind: 'test' | 'save' | 'remove') {
    setBusy(kind)
    setProblem(null)
    try {
      if (kind === 'test') {
        setResult(
          await call<ConnectionTest>('/providers/test', { method: 'POST', json: { preset: preset.id, ...body() } }),
        )
      } else {
        const path = kind === 'save' ? `/providers/${preset.id}` : `/providers/${preset.id}/remove`
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
        <Label htmlFor={`${preset.id}-key`}>API key{!preset.key_required && ' (if your server needs one)'}</Label>
        <Input
          id={`${preset.id}-key`}
          type="password"
          autoComplete="off"
          value={apiKey}
          placeholder={connected && hint ? 'Leave blank to keep the saved key' : preset.key_hint}
          onChange={(e) => setApiKey(e.target.value)}
        />
      </div>
      {showUrl && (
        <div className="space-y-1">
          <Label htmlFor={`${preset.id}-url`}>Address (base URL)</Label>
          <Input
            id={`${preset.id}-url`}
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
  const initial = () =>
    Object.fromEntries(ROLES.map((r) => [r.id, { ...settings.roles[r.id], input: '', output: '' }])) as Record<
      RoleName,
      Choice
    >
  const [choices, setChoices] = useState<Record<RoleName, Choice>>(initial)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const modelsFor = (provider: string) =>
    provider === 'anthropic'
      ? (settings.priced.anthropic ?? [])
      : (settings.providers.find((p) => p.preset === provider)?.models ?? [])
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
        if (!isPriced(c)) {
          if (c.input === '' || c.output === '') {
            throw new Error(`Enter the price of ${c.model} (from ${label(c.provider)}'s price page) first.`)
          }
          await call('/providers/prices', {
            method: 'POST',
            json: {
              provider: c.provider,
              model: c.model,
              input_usd_per_mtok: Number(c.input),
              output_usd_per_mtok: Number(c.output),
            },
          })
        }
      }
      const roles = Object.fromEntries(
        ROLES.map((r) => [r.id, { provider: choices[r.id].provider, model: choices[r.id].model }]),
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
                    className="h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm"
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
        <Button disabled={busy} onClick={() => void save()}>
          {busy && <Loader2 className="animate-spin" aria-hidden />} Save models
        </Button>
        {saved && <span className="text-sm text-muted">Saved.</span>}
      </div>
    </section>
  )
}
