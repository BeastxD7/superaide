'use client'

import Link from 'next/link'
import { useRef, useState, useSyncExternalStore, type FormEvent } from 'react'
import { BrandMark } from '@/components/site'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import type { AskPage } from '@/lib/server'

type Field = 'name' | 'contact' | 'topic' | 'why' | 'deadline'
type Sent = { message: string; get_own: { label: string; href: string } }

const HEADERS = { 'Content-Type': 'application/json', 'X-Requested-With': 'superaide' }

function today(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// What the API's error body says, and which field it's about.
// The API's standard error body (ADR 0013) carries the sentence in `message`; the old `detail` is the fallback.
function errorDetail(body: { message?: string; detail?: unknown; error?: { detail?: unknown } | null }): unknown {
  const nested = body.error?.detail
  if (Array.isArray(nested)) return nested
  if (body.error && body.message) {
    const field = nested && typeof nested === 'object' ? (nested as { field?: Field }).field : undefined
    return { message: body.message, field }
  }
  return body.detail
}

function problemOf(status: number, detail: unknown): { text: string; field?: Field } {
  // A link turned off while the form was open: the API's bare "Not Found" is not for people.
  if (status === 404) return { text: 'This link has just been turned off.' }
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const d = detail as { message: string; field?: Field }
    return { text: d.message, field: d.field }
  }
  if (Array.isArray(detail)) {
    const first = detail[0] as { loc?: string[]; msg?: string } | undefined
    const field = first?.loc?.at(-1) as Field | undefined
    return { text: 'Check this field and try again.', field }
  }
  if (typeof detail === 'string') return { text: detail }
  return { text: "That didn't go through. Try again in a minute." }
}

// The public intake form. No model reads it on the visitor's behalf: code ranks it, the owner decides.
export function AskForm({ page }: { page: AskPage }) {
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState<Sent | null>(null)
  const [problem, setProblem] = useState<{ text: string; field?: Field } | null>(null)
  const summary = useRef<HTMLDivElement>(null)
  const done = useRef<HTMLHeadingElement>(null)
  // Today in the visitor's own zone. The server has no idea of it, so nothing is rendered there (no `min` until the page is live).
  const minDate = useSyncExternalStore(
    () => () => {},
    today,
    () => undefined,
  )
  const url = `/api/p/ask/${encodeURIComponent(page.handle)}`

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const data = new FormData(e.currentTarget)
    const text = (k: string) => String(data.get(k) ?? '').trim()
    setBusy(true)
    setProblem(null)
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: HEADERS,
        body: JSON.stringify({
          name: text('name'),
          contact: text('contact'),
          topic: text('topic'),
          why: text('why'),
          deadline: text('deadline') || null,
          website: text('website'),
          token: page.token,
        }),
      })
      const body = (await res.json().catch(() => ({}))) as { detail?: unknown; error?: { detail?: unknown } | null } & Partial<Sent>
      if (!res.ok) {
        setProblem(problemOf(res.status, errorDetail(body)))
        requestAnimationFrame(() => summary.current?.focus())
        return
      }
      setSent({ message: body.message ?? '', get_own: body.get_own ?? { label: 'Get your own Ava', href: '/' } })
      requestAnimationFrame(() => done.current?.focus())
    } catch {
      setProblem({ text: "Can't reach the server. Check your connection and try again." })
    } finally {
      setBusy(false)
    }
  }

  function countClick() {
    // A count only; keepalive lets it finish while the next page loads.
    void fetch(`${url}/click`, { method: 'POST', headers: HEADERS, body: '{}', keepalive: true }).catch(() => {})
  }

  const invalid = (f: Field) => (problem?.field === f ? true : undefined)
  const describedBy = (f: Field, hint?: string) =>
    [hint, problem?.field === f ? 'ask-problem' : undefined].filter(Boolean).join(' ') || undefined

  return (
    <main className="min-h-dvh bg-mist px-4 py-8 sm:py-12">
      <div className="mx-auto w-full max-w-lg">
        <header className="flex items-center gap-3">
          <BrandMark className="size-8" />
          <div>
            <p className="text-sm text-muted">Ava</p>
            <h1 className="text-2xl font-medium text-ink tight">{page.display}</h1>
          </div>
        </header>

        <p className="mt-4 rounded-2xl bg-sky-100 px-4 py-3 text-sm text-ink" id="ai-notice">
          {page.notice}
        </p>

        {sent ? (
          <section className="mt-6 rounded-3xl bg-white p-6 shadow-sm" aria-labelledby="ask-done">
            <h2 id="ask-done" ref={done} tabIndex={-1} className="text-xl font-medium text-ink outline-none">
              Thanks, it&rsquo;s on its way
            </h2>
            <p className="mt-2 text-body" role="status">
              {sent.message}
            </p>
            <p className="mt-6 text-sm text-muted">Want an assistant like this for your own inbox?</p>
            <Link
              href={sent.get_own.href}
              onClick={countClick}
              className="mt-2 inline-flex min-h-11 items-center rounded-full bg-ink px-5 text-sm font-medium text-white hover:bg-night-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-600"
            >
              {sent.get_own.label}
            </Link>
          </section>
        ) : (
          <form onSubmit={submit} className="mt-6 grid gap-5 rounded-3xl bg-white p-5 shadow-sm sm:p-6" aria-describedby="ai-notice">
            <p className="text-sm text-body">
              Say who you are and what you need. Fields marked <span aria-hidden="true">*</span>
              <span className="sr-only">required</span> are needed.
            </p>

            {problem && (
              <div ref={summary} tabIndex={-1} id="ask-problem" role="alert" className="rounded-2xl bg-[#fbe8ea] px-4 py-3 text-sm text-danger outline-none">
                {problem.text}
              </div>
            )}

            <div className="grid gap-1.5">
              <Label htmlFor="ask-name">
                Your name <span aria-hidden="true">*</span>
              </Label>
              <Input id="ask-name" name="name" required maxLength={page.limits.name} autoComplete="name" className="min-h-11" aria-invalid={invalid('name')} aria-describedby={describedBy('name')} />
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="ask-contact">
                How to reach you <span aria-hidden="true">*</span>
              </Label>
              <Input
                id="ask-contact"
                name="contact"
                required
                maxLength={page.limits.contact}
                autoComplete="email"
                className="min-h-11"
                aria-invalid={invalid('contact')}
                aria-describedby={describedBy('contact', 'ask-contact-hint')}
              />
              <p id="ask-contact-hint" className="text-xs text-muted">
                An email address or a phone number. It&rsquo;s only used if they decide to reply.
              </p>
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="ask-topic">
                What it&rsquo;s about <span aria-hidden="true">*</span>
              </Label>
              <Input id="ask-topic" name="topic" required minLength={3} maxLength={page.limits.topic} className="min-h-11" aria-invalid={invalid('topic')} aria-describedby={describedBy('topic')} />
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="ask-why">Why it matters</Label>
              <Textarea id="ask-why" name="why" rows={4} maxLength={page.limits.why} aria-invalid={invalid('why')} aria-describedby={describedBy('why', 'ask-why-hint')} />
              <p id="ask-why-hint" className="text-xs text-muted">
                A few lines is plenty.
              </p>
            </div>

            <div className="grid gap-1.5">
              <Label htmlFor="ask-deadline">Deadline, if there is one</Label>
              <Input id="ask-deadline" name="deadline" type="date" min={minDate} className="min-h-11 sm:max-w-56" aria-invalid={invalid('deadline')} aria-describedby={describedBy('deadline')} />
            </div>

            {/* For bots only: people never see or reach this field. */}
            <div aria-hidden="true" className="absolute -left-[10000px] top-auto size-px overflow-hidden">
              <label htmlFor="ask-website">Website</label>
              <input id="ask-website" name="website" type="text" tabIndex={-1} autoComplete="off" />
            </div>

            <Button type="submit" disabled={busy} className="min-h-11 w-full sm:w-auto">
              {busy ? 'Sending…' : 'Send to Ava'}
            </Button>
          </form>
        )}

        <p className="mt-6 text-center text-xs text-muted">
          Powered by <Link href="/" className="underline underline-offset-2">Superaide</Link>.
        </p>
      </div>
    </main>
  )
}
