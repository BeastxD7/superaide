'use client'

import { Star } from 'lucide-react'
import Link from 'next/link'
import { BrandIcon } from '@/components/BrandIcon'
import { isBrand } from '@/components/brands/marks'
import { useParams } from 'next/navigation'
import { MasterDetail, PageHeader } from '@/components/layout'
import { Notice } from '@/components/site'
import { Badge } from '@/components/ui/badge'
import type { ReactNode } from 'react'
import { ListFooter } from '@/components/ui/list-footer'
import { Skeleton } from '@/components/ui/skeleton'
import { CHANNEL_LABEL, capitalise } from '@/features/dashboard/data'
import type { PersonCard } from '@/lib/api'
import { initialOf } from '@/lib/initial'
import { usePaginated } from '@/lib/paginated'
import { useSession } from '@/lib/session-context'
import { PendingFriends } from '@/features/dashboard/people/PendingFriends'

// Where someone is recognised, or that they are not yet. The table shows it as a column from the small
// breakpoint up; below it the same line sits under the name, so a phone loses nothing (#300).
function Channels({ channels }: { channels: string[] }) {
  return channels.length ? (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
      {channels.map((c) => (
        <span key={c} className="inline-flex items-center gap-1.5">
          {isBrand(c) && <BrandIcon brand={c} size={14} decorative />}
          {CHANNEL_LABEL[c] ?? capitalise(c)}
        </span>
      ))}
    </span>
  ) : (
    <span className="text-muted">Not linked yet</span>
  )
}

// What the page shows when no one is chosen: on a wide screen the detail pane says so; on a phone the list is the page.
export function PeoplePage() {
  return <p className="pt-10 text-center text-sm text-muted">Choose someone to see what Ava knows about them.</p>
}

// People as a list, and the chosen person beside it (a layout, so the list and how far you have scrolled it stay put as you
// move from one person to the next). On a phone the list is the page, and a person opens on their own.
export function PeopleLayout({ children }: { children: ReactNode }) {
  const { me } = useSession()
  const { id } = useParams<{ id?: string }>()
  const list = usePaginated<PersonCard>('/people', { limit: 50, itemsKey: 'people' })
  const error = list.failed === 'first' ? list.error : null
  const name = me?.agent.name ?? 'Ava'
  const people = list.status === 'loading' || (list.status === 'error' && list.failed === 'first') ? undefined : list.items

  return (
    <MasterDetail
      selected={Boolean(id)}
      listLabel="People"
      detailLabel="Person"
      listWidth="lg"
      header={
        <div className={id ? 'hidden @3xl/md:block' : undefined}>
          <PageHeader
            as={id ? 'h2' : 'h1'}
            title="People who matter"
            description={`${name} treats these people as yours. VIPs always get through to you, even in quiet hours. She recognises someone on a channel only once they've linked it, never from a name alone.`}
          />
        </div>
      }
      list={
        <div className="pt-4">
          <PendingFriends />
          {error && (
            <div className="mt-4">
              <Notice tone="error">{error}</Notice>
            </div>
          )}
          <ul aria-label="Your people" className="mt-4 space-y-1.5">
            {!people &&
              !error &&
              [0, 1, 2, 3].map((i) => (
                <li key={i} className="rounded-xl border border-line bg-white p-3">
                  <Skeleton className="h-9 w-2/3" />
                </li>
              ))}
            {people?.map((p) => (
              <li key={p.id}>
                <Link
                  href={`/dashboard/people/${p.id}`}
                  aria-current={id === p.id ? 'page' : undefined}
                  className={`flex min-w-0 items-start gap-2.5 rounded-xl border p-3 touch:min-h-11 ${
                    id === p.id ? 'border-violet-400 bg-lilac-50' : 'border-line bg-white hover:bg-mist'
                  }`}
                >
                  <span className="grid size-8 shrink-0 place-items-center rounded-full bg-lilac-100 text-xs font-semibold text-violet-700">{initialOf(p.name)}</span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center justify-between gap-2">
                      <span className="font-medium text-ink [overflow-wrap:anywhere]">{p.name}</span>
                      {p.vip && (
                        <Badge className="shrink-0 gap-1">
                          <Star className="size-3" aria-hidden />
                          VIP
                        </Badge>
                      )}
                    </span>
                    <span className="block text-xs text-body">{capitalise(p.relationship)}</span>
                    <span className="mt-1 block text-xs text-body">
                      <span className="text-muted">Recognised on: </span>
                      <Channels channels={p.channels} />
                    </span>
                  </span>
                </Link>
              </li>
            ))}
            {people && people.length === 0 && (
              <li className="rounded-xl border border-dashed border-line p-6 text-center text-sm text-muted">
                No one yet. You can name the people who matter during the introduction.
              </li>
            )}
          </ul>
          {people && people.length > 0 && <ListFooter {...list.footer} endLabel="That is everyone" />}
        </div>
      }
      detail={children}
      empty={children}
    />
  )
}
