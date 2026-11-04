'use client'

import { BellOff } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAppData } from '@/features/dashboard/data'
import { call } from '@/lib/api'

export type Undelivered = {
  id: string
  channel: string
  channel_name: string
  text: string
  state: 'queued' | 'failed'
  attempts: number
  next_attempt_at: string | null
  reason: string
  at: string
}

// Pushes to the owner's Telegram or Slack that did not arrive: still being retried, or given up on.
// The in-app copy is always in the chat; this says plainly that the other copy is missing. Hidden when none.
export function UndeliveredCard({ name }: { name: string }) {
  const { data, reload } = useAppData<{ count: number; items: Undelivered[] }>('/notifications/undelivered')
  if (!data || data.count === 0) return null
  return (
    <Card className="mt-6" data-testid="undelivered-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BellOff className="size-5 text-amber-600" aria-hidden />
          {data.count === 1 ? '1 notification did not reach you' : `${data.count} notifications did not reach you`}
        </CardTitle>
        <CardDescription>
          {name} could not send {data.count === 1 ? 'this' : 'these'} to your phone. {data.count === 1 ? 'It is' : 'They are'} in your chat here. Marking one as seen stops the retries and clears it from this list.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="space-y-3">
          {data.items.map((n) => (
            <li key={n.id} className="text-sm" data-state={n.state}>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={n.state === 'failed' ? 'destructive' : 'secondary'}>
                  {n.state === 'failed' ? `Not delivered on ${n.channel_name}` : `Retrying on ${n.channel_name}`}
                </Badge>
                <span className="text-body">
                  {n.attempts} {n.attempts === 1 ? 'try' : 'tries'}
                </span>
              </div>
              <p className="mt-1 text-ink">{n.text}</p>
              {n.reason && <p className="text-body">{n.reason}</p>}
              <Button
                size="sm"
                variant="outline"
                className="mt-2"
                aria-label={`Mark as seen: ${n.text}`}
                onClick={async () => {
                  await call(`/notifications/${n.id}/ack`, { method: 'POST' })
                  await reload()
                }}
              >
                I have seen this
              </Button>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}
