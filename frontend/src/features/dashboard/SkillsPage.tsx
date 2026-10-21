'use client'

import { BadgeCheck, Puzzle, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { Notice } from '@/components/site'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useAppData } from '@/features/dashboard/data'
import { AppError, call, type SkillCard } from '@/lib/api'
import { useSession } from '@/lib/session-context'

export function SkillsPage() {
  const { me } = useSession()
  const name = me?.agent.name ?? 'Ava'
  const { data, error, reload } = useAppData<SkillCard[]>('/skills')
  const [consent, setConsent] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  async function act(fn: () => Promise<unknown>) {
    setBusy(true)
    setProblem(null)
    try {
      await fn()
      setConsent(null)
      await reload()
    } catch (e) {
      setProblem(e instanceof AppError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-4 py-6 sm:px-6 lg:py-8">
        <h1 className="text-3xl font-medium text-ink tight">Skills</h1>
        <p className="mt-1 text-body">
          What {name} can do for you. Each skill says exactly what it needs; anything that changes things outside{' '}
          {name}, sends or spends always waits for your OK.
        </p>
        {(error || problem) && (
          <div className="mt-4">
            <Notice tone="error">{problem ?? error}</Notice>
          </div>
        )}
        <ul className="mt-6 grid gap-3 sm:grid-cols-2" aria-label="Skills">
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
              <li key={s.id} className="flex flex-col rounded-xl border border-line bg-white p-4" aria-label={s.title}>
                <div className="flex items-start gap-3">
                  <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-lilac-100 text-violet-600">
                    <Puzzle className="size-4.5" aria-hidden />
                  </span>
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
              </li>
            )
          })}
        </ul>
        <p className="mt-6 text-xs text-muted">
          Skills follow one open protocol: instructions in the Agent Skills format, tools over MCP, and a manifest that
          says what each one may touch.
        </p>
      </div>
    </div>
  )
}
