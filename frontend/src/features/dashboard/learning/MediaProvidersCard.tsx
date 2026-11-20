'use client'

import { Loader2 } from 'lucide-react'
import { useState } from 'react'
import { Notice } from '@/components/site'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useAppData } from '@/features/dashboard/data'
import { AppError, call, type ConnectionTest, type MediaCapability, type MediaSettings } from '@/lib/api'
import { errorMessage } from '@/lib/action'

// Voice notes and photos (#501, #511): the owner picks which connected service reads them, per kind. Nothing is sent
// anywhere until they do. The same price rule as the text models: an unknown price must be entered first.
const PRICE_HELP = 'Enter the price as a number with a dot, like 0.006.'

function price(text: string): number | null {
  const t = text.trim().replace(',', '.')
  return /^\d+(\.\d+)?$/.test(t) ? Number(t) : null
}

export function MediaProvidersCard() {
  const { data, error, reload } = useAppData<MediaSettings>('/media-providers')
  return (
    <section className="mt-8" aria-label="Voice notes and photos">
      <h2 className="text-lg font-medium text-ink">Voice notes and photos</h2>
      <p className="mt-1 text-sm text-body">
        Choose which service listens to voice notes and which looks at photos people send. Nothing is sent to any service
        until you choose one. Everything they say or show is treated as quoted content, never as an instruction, and every
        call counts against your spending cap.
      </p>
      {error && (
        <div className="mt-3">
          <Notice tone="error">{error}</Notice>
        </div>
      )}
      <div className="mt-3 space-y-3">
        {data?.capabilities.map((cap) => <CapabilityRow key={`${cap.id}-${cap.choice?.provider}-${cap.choice?.model}`} cap={cap} onChanged={reload} />)}
      </div>
    </section>
  )
}

function CapabilityRow({ cap, onChanged }: { cap: MediaCapability; onChanged: () => Promise<void> }) {
  const usable = cap.providers.filter((p) => p.ready)
  const [provider, setProvider] = useState(cap.choice?.provider ?? usable[0]?.key ?? '')
  const [model, setModel] = useState(cap.choice?.model ?? '')
  const [needsPrice, setNeedsPrice] = useState(cap.choice !== null && cap.price !== null && !cap.price.known)
  const [input, setInput] = useState('')
  const [output, setOutput] = useState('')
  const [busy, setBusy] = useState<'save' | 'test' | 'remove' | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [result, setResult] = useState<ConnectionTest | null>(null)
  const models = [...new Set([...(usable.find((p) => p.key === provider)?.models ?? []), ...cap.suggested])]
  const perMinute = cap.id === 'transcribe'

  async function save() {
    setBusy('save')
    setProblem(null)
    setResult(null)
    try {
      if (needsPrice) {
        const a = price(input)
        const b = perMinute ? 0 : price(output)
        if (a === null || b === null) throw new Error(PRICE_HELP)
        await call(perMinute ? '/media-providers/prices' : '/providers/prices', {
          method: 'POST',
          json: perMinute
            ? { provider, model, usd_per_minute: a }
            : { provider, model, input_usd_per_mtok: a, output_usd_per_mtok: b },
        })
      }
      await call(`/media-providers/${cap.id}`, { method: 'PUT', json: { provider, model: model.trim() } })
      setNeedsPrice(false)
      await onChanged()
    } catch (e) {
      if (e instanceof AppError && e.status === 409 && /price/i.test(e.message)) setNeedsPrice(true)
      setProblem(e instanceof AppError && e.status === 422 ? PRICE_HELP : errorMessage(e))
    } finally {
      setBusy(null)
    }
  }

  async function test() {
    setBusy('test')
    setProblem(null)
    try {
      setResult(await call<ConnectionTest>(`/media-providers/${cap.id}/test`, { method: 'POST', json: { provider, model: model.trim() } }))
    } catch (e) {
      setProblem(errorMessage(e))
    } finally {
      setBusy(null)
    }
  }

  async function remove() {
    setBusy('remove')
    setProblem(null)
    try {
      await call(`/media-providers/${cap.id}/remove`, { method: 'POST', json: {} })
      await onChanged()
    } catch (e) {
      setProblem(errorMessage(e))
    } finally {
      setBusy(null)
    }
  }

  const status = cap.problem ?? (cap.on ? (cap.price?.free ? 'On. This one is free.' : 'On.') : 'Off. Nothing is sent.')
  return (
    <div className="rounded-xl border border-line bg-white p-4" role="group" aria-label={cap.label}>
      <p className="font-medium text-ink">{cap.label}</p>
      <p className="text-xs text-muted">{cap.hint}</p>
      <p className="mt-1 text-xs text-body" data-testid={`media-status-${cap.id}`}>{status}</p>
      {usable.length === 0 ? (
        <p className="mt-3 text-sm text-body">No service is connected that can do this yet. Connect one above first.</p>
      ) : (
        <>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor={`${cap.id}-media-provider`}>Service</Label>
              <select
                id={`${cap.id}-media-provider`}
                className="h-9 w-full rounded-md border border-input bg-transparent px-2 text-base focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 md:text-sm"
                value={provider}
                onChange={(e) => {
                  setProvider(e.target.value)
                  setNeedsPrice(false)
                }}
              >
                {usable.map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <Label htmlFor={`${cap.id}-media-model`}>Model</Label>
              <Input
                id={`${cap.id}-media-model`}
                list={`${cap.id}-media-models`}
                value={model}
                onChange={(e) => {
                  setModel(e.target.value)
                  setNeedsPrice(false)
                }}
              />
              <datalist id={`${cap.id}-media-models`}>
                {models.map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
            </div>
          </div>
          {needsPrice && (
            <div className="mt-3 grid gap-3 sm:grid-cols-2" aria-label={`Price of ${model}`}>
              <p className="text-xs text-muted sm:col-span-2">
                Superaide doesn&apos;t know this model&apos;s price. Enter it from the service&apos;s price page so your
                spending cap stays accurate.
              </p>
              <div className="space-y-1">
                <Label htmlFor={`${cap.id}-media-in`}>{perMinute ? 'US$ per audio minute' : 'Input, US$ / 1M tokens'}</Label>
                <Input id={`${cap.id}-media-in`} inputMode="decimal" value={input} onChange={(e) => setInput(e.target.value)} />
              </div>
              {!perMinute && (
                <div className="space-y-1">
                  <Label htmlFor={`${cap.id}-media-out`}>Output, US$ / 1M tokens</Label>
                  <Input id={`${cap.id}-media-out`} inputMode="decimal" value={output} onChange={(e) => setOutput(e.target.value)} />
                </div>
              )}
            </div>
          )}
          {problem && (
            <div className="mt-3">
              <Notice tone="error">{problem}</Notice>
            </div>
          )}
          {result && (
            <div className="mt-3">
              <Notice tone={result.ok ? 'info' : 'error'}>{result.message}</Notice>
            </div>
          )}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button disabled={busy !== null || model.trim() === ''} onClick={() => void save()}>
              {busy === 'save' && <Loader2 className="animate-spin" aria-hidden />} Save
            </Button>
            <Button variant="outline" disabled={busy !== null || model.trim() === ''} onClick={() => void test()}>
              {busy === 'test' && <Loader2 className="animate-spin" aria-hidden />} Test
            </Button>
            {cap.choice && (
              <Button variant="ghost" disabled={busy !== null} onClick={() => void remove()}>
                Turn off
              </Button>
            )}
          </div>
        </>
      )}
    </div>
  )
}
