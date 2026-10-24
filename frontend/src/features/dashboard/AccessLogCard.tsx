'use client'

// When someone at Superaide opened this Ava's people, conversations, memories or log (ADR 0009):
// operators can only do that by giving a reason, and every time is listed here for the owner.
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ListFooter } from '@/components/ui/list-footer'
import { SkeletonList } from '@/components/ui/skeleton'
import { arrayAppFetcher, usePaginated } from '@/lib/paginated'

type Access = { id: string; at: string; what: string; reason: string }

const WHAT: Record<string, string> = {
  '/api/people': 'People and linked accounts',
  '/api/conversations': 'The list of conversations',
  '/api/events': 'Messages',
  '/api/memories': 'Notebook',
  '/api/approvals': 'Approvals',
  '/api/audit': 'Safety log',
}

// The paged routes (ADR 0014) show the same screens as their unpaged ones: /api/people/page, /api/conversations/page.
const unpaged = (path: string) => path.replace(/\/unlinked\/page$/, '').replace(/\/page$/, '')

function what(raw: string): string {
  const path = unpaged(raw)
  if (WHAT[path]) return WHAT[path]
  if (path.startsWith('/api/identities/')) return path.endsWith('/unlink') ? 'Unlinked an account' : 'Linked an account'
  return path
}

export function AccessLogCard({ name }: { name: string }) {
  const log = usePaginated<Access>('/access-log', { limit: 25, fetcher: arrayAppFetcher<Access> })
  const error = log.failed === 'first' ? log.error : null
  return (
    <Card>
      <CardHeader>
        <CardTitle>Access log</CardTitle>
        <CardDescription>
          Times someone at Superaide opened what {name} holds for you. They can only do that by giving a reason, and
          every time is listed here.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {error ? (
          <p className="text-sm text-danger">{error}</p>
        ) : log.status === 'loading' ? (
          <SkeletonList count={3} rowHeight={64} what="the access log" />
        ) : log.items.length === 0 ? (
          <p className="text-sm text-body">No one at Superaide has opened your assistant's content.</p>
        ) : (
          <ul className="divide-y divide-mist text-sm" aria-label="Access by Superaide staff">
            {log.items.map((a) => (
              <li key={a.id} className="grid gap-1 py-2 sm:grid-cols-[11rem_1fr]">
                <time className="text-muted" dateTime={a.at}>
                  {new Date(a.at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}
                </time>
                <div>
                  <p className="text-ink">{what(a.what)}</p>
                  <p className="text-body">Reason given: {a.reason}</p>
                </div>
              </li>
            ))}
          </ul>
        )}
        {log.items.length > 0 && <ListFooter {...log.footer} endLabel="That is every visit" />}
      </CardContent>
    </Card>
  )
}
