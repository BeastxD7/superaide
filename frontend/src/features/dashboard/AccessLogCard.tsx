'use client'

// When someone at Superaide opened this Ava's people, conversations, memories or log (ADR 0009):
// operators can only do that by giving a reason, and every time is listed here for the owner.
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useAppData } from '@/features/dashboard/data'

type Access = { at: string; what: string; reason: string }

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
  const log = useAppData<Access[]>('/access-log')
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
        {log.error ? (
          <p className="text-sm text-danger">{log.error}</p>
        ) : !log.data ? (
          <Skeleton className="h-10 w-full" />
        ) : log.data.length === 0 ? (
          <p className="text-sm text-body">No one at Superaide has opened your assistant's content.</p>
        ) : (
          <ul className="divide-y divide-mist text-sm" aria-label="Access by Superaide staff">
            {log.data.map((a, i) => (
              <li key={`${a.at}-${i}`} className="grid gap-1 py-2 sm:grid-cols-[11rem_1fr]">
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
      </CardContent>
    </Card>
  )
}
