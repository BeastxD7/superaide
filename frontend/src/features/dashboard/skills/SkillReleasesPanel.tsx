'use client'

import { useEffect, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { call } from '@/lib/api'

type Release = {
  skill_id: string
  version: string
  effect_class: string
  available_at: string
  available: boolean
  security_patch: boolean
}
type Retired = { skill_id: string; reason: string }

// New skill versions wait before they can be installed, and retired names stay reserved (SKILL_COOLDOWN_ENABLED).
// The server answers 404 while that is off; the panel then shows nothing.
export function SkillReleasesPanel() {
  const [data, setData] = useState<{ releases: Release[]; retired: Retired[] } | null>(null)

  useEffect(() => {
    let live = true
    call<{ releases: Release[]; retired: Retired[] }>('/skill-releases')
      .then((d) => live && setData(d))
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [])

  if (!data || (data.releases.length === 0 && data.retired.length === 0)) return null
  return (
    <section className="mt-6 rounded-lg border border-line p-4" aria-label="New versions and retired names">
      <h2 className="text-sm font-medium text-ink">New versions</h2>
      <p className="mt-0.5 text-xs text-muted">
        A new version waits before it can be installed: longer the more it can change. A reviewed security fix skips the
        wait.
      </p>
      <ul className="mt-2 space-y-1.5">
        {data.releases.map((r) => (
          <li key={`${r.skill_id}@${r.version}`} className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-medium text-ink">
              {r.skill_id} {r.version}
            </span>
            {r.available ? (
              <Badge variant="outline">{r.security_patch ? 'Security fix, ready' : 'Ready to install'}</Badge>
            ) : (
              <Badge variant="outline">Waiting until {new Date(r.available_at).toLocaleString()}</Badge>
            )}
          </li>
        ))}
      </ul>
      {data.retired.length > 0 && (
        <p className="mt-3 text-xs text-muted">
          Retired names, never reused: {data.retired.map((t) => t.skill_id).join(', ')}
        </p>
      )}
    </section>
  )
}
