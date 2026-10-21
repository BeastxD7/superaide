'use client'

import { Star } from 'lucide-react'
import { BrandIcon } from '@/components/BrandIcon'
import { isBrand } from '@/components/brands/marks'
import { Notice } from '@/components/site'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { CHANNEL_LABEL, capitalise, useAppData } from '@/features/dashboard/data'
import type { PersonCard } from '@/lib/api'
import { useSession } from '@/lib/session-context'

export function PeoplePage() {
  const { me } = useSession()
  const { data, error } = useAppData<{ people: PersonCard[] }>('/people')
  const name = me?.agent.name ?? 'Ava'
  const people = data?.people

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-4 py-6 sm:px-6 lg:py-8">
        <h1 className="text-3xl font-medium text-ink tight">People who matter</h1>
        <p className="mt-1 text-body">
          {name} treats these people as yours. VIPs always get through to you, even in quiet hours. She recognises
          someone on a channel only once they&apos;ve linked it, never from a name alone.
        </p>
        {error && (
          <div className="mt-4">
            <Notice tone="error">{error}</Notice>
          </div>
        )}
        <div className="mt-6 overflow-hidden rounded-xl border border-line bg-white">
          <table className="w-full text-left text-sm">
            <thead className="bg-mist text-xs text-muted">
              <tr>
                <th scope="col" className="px-4 py-2.5 font-medium">Name</th>
                <th scope="col" className="px-4 py-2.5 font-medium">Relationship</th>
                <th scope="col" className="hidden px-4 py-2.5 font-medium sm:table-cell">Recognised on</th>
                <th scope="col" className="px-4 py-2.5 font-medium">
                  <span className="sr-only">VIP</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {!people &&
                [0, 1].map((i) => (
                  <tr key={i}>
                    <td className="px-4 py-3" colSpan={4}>
                      <Skeleton className="h-4 w-1/2" />
                    </td>
                  </tr>
                ))}
              {people?.map((p) => (
                <tr key={p.id}>
                  <td className="px-4 py-3">
                    <span className="flex items-center gap-2.5">
                      <span className="grid size-8 shrink-0 place-items-center rounded-full bg-lilac-100 text-xs font-semibold text-violet-700">
                        {p.name.charAt(0).toUpperCase()}
                      </span>
                      <span className="font-medium text-ink">{p.name}</span>
                    </span>
                  </td>
                  <td className="px-4 py-3 text-body">{capitalise(p.relationship)}</td>
                  <td className="hidden px-4 py-3 text-body sm:table-cell">
                    {p.channels.length ? (
                      <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                        {p.channels.map((c) => (
                          <span key={c} className="inline-flex items-center gap-1.5">
                            {isBrand(c) && <BrandIcon brand={c} size={14} decorative />}
                            {CHANNEL_LABEL[c] ?? capitalise(c)}
                          </span>
                        ))}
                      </span>
                    ) : (
                      <span className="text-muted">Not linked yet</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {p.vip && (
                      <Badge className="gap-1">
                        <Star className="size-3" aria-hidden />
                        VIP
                      </Badge>
                    )}
                  </td>
                </tr>
              ))}
              {people && people.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-8 text-center text-muted">
                    No one yet. You can name the people who matter during the introduction.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
