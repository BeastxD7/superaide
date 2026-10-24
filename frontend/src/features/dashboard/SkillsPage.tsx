'use client'

import { BadgeCheck, Puzzle, ShieldCheck } from 'lucide-react'
import Link from 'next/link'
import { useState } from 'react'
import { BrandIcon, type Brand } from '@/components/BrandIcon'
import { Notice } from '@/components/site'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useAppData } from '@/features/dashboard/data'
import { GmailPanel } from '@/features/dashboard/GmailPanel'
import { SkillReleasesPanel } from '@/features/dashboard/SkillReleasesPanel'
import { call, type SkillCard, type SkillOption } from '@/lib/api'
import { useSession } from '@/lib/session-context'
import { errorMessage } from '@/lib/action'

// Capabilities that wrap a branded service show that service's logo; the rest keep the generic piece.
const SKILL_BRAND: Record<string, Brand> = {
  'superaide/google-calendar': 'googlecalendar',
  'superaide/gmail': 'gmail',
}

// What Google's consent screen sent back (?google=…) after switching calendar actions on.
const GOOGLE_OUTCOME: Record<string, { tone: 'info' | 'error'; text: string }> = {
  connected: { tone: 'info', text: 'Google allowed it. Ava can now add, move and decline events; anything that emails other people waits for your OK.' },
  no_actions: { tone: 'error', text: "Google didn't allow changes to your calendar (the box was left unticked). Try again and tick it." },
  no_calendar: { tone: 'error', text: "Google didn't allow calendar access. Try again and tick the calendar boxes." },
  cancelled: { tone: 'error', text: 'You cancelled on Google. Calendar actions stay unusable until you allow them there.' },
  expired: { tone: 'error', text: 'That Google page expired. Press Finish on Google again.' },
  api_disabled: { tone: 'error', text: "The Google Calendar API isn't switched on for your Google project. In Google Cloud open APIs & Services, Library, search Google Calendar API, press Enable, then try again." },
  failed: { tone: 'error', text: "Google didn't confirm. Try again in a minute." },
}

export function SkillsPage({ google = null }: { google?: string | null }) {
  const { me } = useSession()
  const name = me?.agent.name ?? 'Ava'
  const { data, error, reload } = useAppData<SkillCard[]>('/skills')
  const [consent, setConsent] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [optionConsent, setOptionConsent] = useState<string | null>(null) // "<skill id>:<setting>"
  const outcome = google ? (GOOGLE_OUTCOME[google] ?? GOOGLE_OUTCOME.failed) : null

  // Switch an option on (with consent) or off. Calendar actions then need Google's own consent too.
  async function setOption(skill: SkillCard, option: SkillOption, on: boolean) {
    const [publisher, slug] = skill.id.split('/')
    setBusy(true)
    setProblem(null)
    try {
      const card = await call<SkillCard>(`/skills/${publisher}/${slug}/options`, {
        method: 'POST',
        json: { setting: option.setting, on, accepted: on ? option.permissions.map((p) => p.id) : [] },
      })
      setOptionConsent(null)
      if (on && card.google_url) {
        window.location.assign(card.google_url)
        return
      }
      if (on && card.google_url === null) setProblem('Connect Google Calendar under Connections first, then come back to finish.')
      await reload()
    } catch (e) {
      setProblem(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  async function act(fn: () => Promise<unknown>) {
    setBusy(true)
    setProblem(null)
    try {
      await fn()
      setConsent(null)
      await reload()
    } catch (e) {
      setProblem(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-4 py-6 sm:px-6 lg:py-8">
        <h1 className="text-3xl font-medium text-ink tight">Capabilities</h1>
        <p className="mt-1 text-body">
          What {name} can do for you. Each capability says exactly what it needs; anything that changes things outside{' '}
          {name}, sends or spends always waits for your OK.
        </p>
        {(error || problem) && (
          <div className="mt-4">
            <Notice tone="error">{problem ?? error}</Notice>
          </div>
        )}
        {outcome && !problem && (
          <div className="mt-4">
            <Notice tone={outcome.tone}>{outcome.text}</Notice>
          </div>
        )}
        <ul className="mt-6 grid gap-3 sm:grid-cols-2" aria-label="Capabilities">
          {!data &&
            [0, 1, 2].map((i) => (
              <li key={i} className="rounded-xl border border-line bg-white p-4">
                <Skeleton className="h-4 w-1/2" />
                <Skeleton className="mt-3 h-3 w-full" />
              </li>
            ))}
          {data?.map((s) => {
            const [publisher, slug] = s.id.split('/')
            const asking = consent === s.id
            return (
              <li key={s.id} className="flex min-w-0 flex-col rounded-xl border border-line bg-white p-4" aria-label={s.title}>
                <div className="flex items-start gap-3">
                  {SKILL_BRAND[s.id] ? (
                    <span className="grid size-9 shrink-0 place-items-center rounded-lg border border-line bg-white">
                      <BrandIcon brand={SKILL_BRAND[s.id]} size={20} decorative />
                    </span>
                  ) : (
                    <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-lilac-100 text-violet-600">
                      <Puzzle className="size-4.5" aria-hidden />
                    </span>
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="flex flex-wrap items-center gap-1.5 font-medium text-ink">
                      {s.title}
                      {s.tier === 'official' && (
                        <Badge variant="secondary" className="gap-1 text-violet-700">
                          <BadgeCheck className="size-3" aria-hidden /> Official
                        </Badge>
                      )}
                      {s.installed && <Badge variant="outline">Installed</Badge>}
                    </p>
                    <p className="text-xs text-muted">
                      {s.publisher} · v{s.version}
                    </p>
                  </div>
                </div>
                <p className="mt-3 text-sm text-body">{s.summary}</p>
                {s.cost && <p className="mt-1 text-xs text-muted">{s.cost}</p>}

                {asking ? (
                  <div className="mt-3 rounded-lg bg-mist p-3" role="group" aria-label={`Permissions for ${s.title}`}>
                    <p className="flex items-center gap-1.5 text-sm font-medium text-ink">
                      <ShieldCheck className="size-4 text-violet-600" aria-hidden /> {s.title} will be able to:
                    </p>
                    <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-sm text-body">
                      {s.permissions.map((p) => (
                        <li key={p.id}>{p.label}</li>
                      ))}
                    </ul>
                    <div className="mt-3 flex gap-2">
                      <Button
                        size="sm"
                        disabled={busy}
                        onClick={() =>
                          void act(() =>
                            call(`/skills/${publisher}/${slug}/install`, {
                              method: 'POST',
                              json: { accepted: s.permissions.map((p) => p.id) },
                            }),
                          )
                        }
                      >
                        Allow and install
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setConsent(null)}>
                        Not now
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className="mt-auto flex gap-2 pt-3">
                    {!s.installed && (
                      <Button size="sm" onClick={() => setConsent(s.id)}>
                        Install
                      </Button>
                    )}
                    {s.installed && s.removable && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => void act(() => call(`/skills/${publisher}/${slug}/remove`, { method: 'POST' }))}
                      >
                        Remove
                      </Button>
                    )}
                    {s.installed && !s.removable && <span className="text-xs text-muted">Always on</span>}
                  </div>
                )}
                {s.installed && s.google_connected === false && s.id !== 'superaide/gmail' && (
                  <div className="mt-3">
                    <Notice tone="warn">
                      Google isn&apos;t connected, so this can&apos;t work yet.{' '}
                      <Link href="/dashboard/connections" className="font-medium underline">
                        Connect Google under Connections
                      </Link>
                      .
                    </Notice>
                  </div>
                )}
                {s.installed && s.id === 'superaide/gmail' && !asking && <GmailPanel name={name} />}

                {s.installed &&
                  (s.google_connected !== false || s.id === 'superaide/gmail') && // Gmail's own panel and options explain a missing Google
                  (s.options ?? []).map((o) => {
                    const key = `${s.id}:${o.setting}`
                    return (
                      <div key={key} className="mt-3 rounded-lg border border-line p-3" role="group" aria-label={o.title}>
                        <p className="text-sm font-medium text-ink">{o.title}</p>
                        <p className="mt-0.5 text-xs text-muted">{o.description}</p>
                        {optionConsent === key ? (
                          <div className="mt-2 rounded-lg bg-mist p-3">
                            <p className="flex items-center gap-1.5 text-sm font-medium text-ink">
                              <ShieldCheck className="size-4 text-violet-600" aria-hidden /> {s.title} will also be able to:
                            </p>
                            <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-sm text-body">
                              {o.permissions.map((p) => (
                                <li key={p.id}>{p.label}</li>
                              ))}
                            </ul>
                            <p className="mt-2 text-xs text-muted">
                              Private events are added or moved straight away. Inviting people, changing a meeting others are in, and
                              declining always wait for your OK. Next, Google asks you to let {name} change your calendar.
                            </p>
                            <div className="mt-3 flex gap-2">
                              <Button size="sm" disabled={busy} onClick={() => void setOption(s, o, true)}>
                                Allow and continue to Google
                              </Button>
                              <Button size="sm" variant="ghost" onClick={() => setOptionConsent(null)}>
                                Not now
                              </Button>
                            </div>
                          </div>
                        ) : (
                          <div className="mt-2 flex flex-wrap items-center gap-2">
                            {!o.on && (
                              <Button size="sm" variant="outline" onClick={() => setOptionConsent(key)}>
                                Allow
                              </Button>
                            )}
                            {o.on && o.needs_google && (
                              <Button size="sm" disabled={busy} onClick={() => void setOption(s, o, true)}>
                                Finish on Google
                              </Button>
                            )}
                            {o.on && !o.needs_google && <Badge variant="outline">On</Badge>}
                            {o.on && (
                              <Button size="sm" variant="ghost" disabled={busy} onClick={() => void setOption(s, o, false)}>
                                Turn off
                              </Button>
                            )}
                          </div>
                        )}
                      </div>
                    )
                  })}
              </li>
            )
          })}
        </ul>
        <SkillReleasesPanel />
        <p className="mt-6 text-xs text-muted">
          Capabilities follow one open protocol: instructions in the Agent Skills format, tools over MCP, and a manifest
          that says what each one may touch.
        </p>
      </div>
    </div>
  )
}
