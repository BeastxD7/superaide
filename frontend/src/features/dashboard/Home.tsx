'use client'

import { AlarmClock, AlertTriangle, ArrowRight, BookOpen, Check, Clock, MessageCircle, Plug, ShieldCheck, Star, Users } from 'lucide-react'
import Link from 'next/link'
import type { ReactNode } from 'react'
import { BrandIcon } from '@/components/BrandIcon'
import { isBrand } from '@/components/brands/marks'
import { Orb } from '@/components/Orb'
import { AskRequests } from '@/features/ask/AskRequests'
import { ApprovalsCard } from '@/features/dashboard/Approvals'
import { STAGES } from '@/components/stages'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Loadable, type LoadState } from '@/components/load-state'
import { CATEGORY_LABEL, DIGEST_LABEL, CHANNEL_LABEL, capitalise, useAppData } from '@/features/dashboard/data'
import type { Me, Memory, Message, PersonCard, Preferences, Reminder } from '@/lib/api'
import { initialOf } from '@/lib/initial'
import { useSession } from '@/lib/session-context'

function greeting(): string {
  const h = new Date().getHours()
  return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'
}

export function DashboardHome() {
  const { me } = useSession()
  const notebook = useAppData<{ memories: Memory[] }>('/notebook')
  const people = useAppData<{ people: PersonCard[] }>('/people')
  const prefs = useAppData<Preferences>('/preferences')
  const messages = useAppData<Message[]>('/messages')
  const reminders = useAppData<{ reminders: Reminder[] }>('/reminders')
  if (!me) return null
  const name = me.agent.name
  const first = me.person.name.split(' ')[0]

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6 lg:py-8">
        <h1 className="text-3xl font-medium text-ink tight">
          {greeting()}, {first}
        </h1>
        <p className="mt-1 text-body">
          {me.paused
            ? `${name} is paused, so nobody gets a reply until you resume.`
            : me.brain === 'claude'
              ? `${name} is on duty. She answers what you allowed and keeps the rest for you.`
              : `${name} is in offline mode, so her replies are placeholders for now.`}
        </p>

        {me.brain === 'offline' && !me.paused && (
          <div className="mt-5 flex flex-col gap-3 rounded-2xl border border-lilac-200 bg-lilac-50 p-4 sm:flex-row sm:items-center">
            <Plug className="size-5 shrink-0 text-violet-600" aria-hidden />
            <p className="flex-1 text-sm text-body">
              <span className="font-medium text-ink">Connect Claude to switch {name} on.</span> It takes a minute: paste an API key, and she
              starts replying for real.
            </p>
            <Button asChild size="sm">
              <Link href="/dashboard/connections">Connect Claude</Link>
            </Button>
          </div>
        )}

        <ApprovalsCard name={name} />

        <AskRequests className="mt-6" />

        <div className="mt-6 grid gap-4 lg:grid-cols-3">
          <AgentCard me={me} />
          <ReachCard me={me} />
          <KnowsCard name={name} state={notebook} />
          <PeopleCard state={people} />
          <RulesCard name={name} state={prefs} />
          <ComingUpCard name={name} state={reminders} />
          <LatestCard name={name} state={messages} />
        </div>
      </div>
    </div>
  )
}

function SeeAll({ href, children }: { href: string; children: ReactNode }) {
  return (
    <CardAction>
      <Button asChild variant="ghost" size="sm" className="text-violet-700">
        <Link href={href}>
          {children}
          <ArrowRight />
        </Link>
      </Button>
    </CardAction>
  )
}

function AgentCard({ me }: { me: Me }) {
  const stage = Math.max(
    0,
    STAGES.findIndex((s) => s.name === me.agent.stage),
  )
  return (
    <Card className="bg-gradient-to-br from-lilac-100 to-white lg:col-span-2">
      <CardContent className="flex flex-col gap-6 sm:flex-row sm:items-center">
        <Orb hue="indigo" size={96} track mood={me.paused ? 'asleep' : 'idle'} label={me.agent.name} />
        <div className="min-w-0 flex-1">
          <p className="text-2xl font-medium text-ink tight">{me.agent.name}</p>
          <p className="text-sm text-violet-700">{me.agent.role}</p>
          <ol className="mt-5 grid grid-cols-4 gap-2" aria-label="Growth stages">
            {STAGES.map((s, i) => (
              <li key={s.name}>
                <div className={`h-1.5 rounded-full ${i <= stage ? 'bg-gradient-to-r from-violet-400 to-violet-600' : 'bg-white'}`} />
                <p className={`mt-1.5 text-xs ${i === stage ? 'font-medium text-violet-700' : 'text-muted'}`}>
                  {s.name}
                  {i === stage && <span className="sr-only"> (current stage)</span>}
                </p>
              </li>
            ))}
          </ol>
          <p className="mt-3 text-sm text-body">{STAGES[stage].does}</p>
        </div>
        <Button asChild className="self-start sm:self-center">
          <Link href="/dashboard/chat">
            <MessageCircle />
            Chat with {me.agent.name}
          </Link>
        </Button>
      </CardContent>
    </Card>
  )
}

function ReachCard({ me }: { me: Me }) {
  // A token the service refused means nothing reaches the owner there, whatever "linked" says.
  const refused = (provider: 'telegram' | 'slack') => me.channels[provider] && me.channels.health?.[provider]?.status === 'auth_failed'
  const rows = [
    { key: 'web', on: me.channels.web, bad: false, note: 'Always on' },
    {
      key: 'telegram',
      on: me.channels.telegram,
      bad: refused('telegram'),
      note: me.channels.telegram ? 'Linked to you' : me.channels.telegram_available ? 'Not linked yet' : 'Not connected',
    },
    { key: 'slack', on: me.channels.slack, bad: refused('slack'), note: me.channels.slack ? 'Linked to you' : 'Not connected' },
  ]
  return (
    <Card>
      <CardHeader>
        <CardTitle>Where people reach {me.agent.name}</CardTitle>
        <CardDescription>Channels she listens on for you.</CardDescription>
        <SeeAll href="/dashboard/connections">Manage</SeeAll>
      </CardHeader>
      <CardContent>
        <ul className="space-y-2.5">
          {rows.map((r) => (
            <li key={r.key} className="flex items-center gap-3 text-sm">
              <span className="grid size-6 place-items-center">
                {isBrand(r.key) ? (
                  <BrandIcon brand={r.key} size={18} decorative />
                ) : (
                  <MessageCircle className="size-4 text-violet-600" aria-hidden />
                )}
              </span>
              <span className="font-medium text-ink">{CHANNEL_LABEL[r.key]}</span>
              {r.bad ? (
                <Link href="/dashboard/connections" className="ml-auto inline-flex items-center gap-1 text-warn hover:underline">
                  <AlertTriangle className="size-3.5" aria-hidden />
                  Token rejected: reconnect
                </Link>
              ) : (
                <span className={`ml-auto inline-flex items-center gap-1 ${r.on ? 'text-ok' : 'text-muted'}`}>
                  {r.on ? <Check className="size-3.5" aria-hidden /> : <Plug className="size-3.5" aria-hidden />}
                  {r.note}
                </span>
              )}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}

function KnowsCard({ name, state }: { name: string; state: LoadState<{ memories: Memory[] }> }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BookOpen className="size-4 text-violet-600" aria-hidden />
          What {name} knows
        </CardTitle>
        <CardDescription>Only you can see this.</CardDescription>
        <SeeAll href="/dashboard/notebook">Notebook</SeeAll>
      </CardHeader>
      <CardContent>
        <Loadable state={state} what={`what ${name} knows`}>
          {({ memories }) => {
            const counts = new Map<string, number>()
            for (const m of memories) counts.set(m.category, (counts.get(m.category) ?? 0) + 1)
            return memories.length === 0 ? (
              <p className="text-sm text-muted">Nothing yet. Tell {name} &ldquo;remember that…&rdquo; in chat.</p>
            ) : (
              <>
                <p className="text-3xl font-medium text-ink tight">
                  {memories.length} <span className="text-base font-normal text-muted">{memories.length === 1 ? 'thing' : 'things'}</span>
                </p>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {[...counts.entries()].map(([cat, n]) => (
                    <Badge key={cat} variant="secondary">
                      {CATEGORY_LABEL[cat] ?? 'Note'} {n}
                    </Badge>
                  ))}
                </div>
                <p className="mt-4 line-clamp-2 text-sm text-body">Latest: {memories[0].text}</p>
              </>
            )
          }}
        </Loadable>
      </CardContent>
    </Card>
  )
}

function PeopleCard({ state }: { state: LoadState<{ people: PersonCard[] }> }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Users className="size-4 text-violet-600" aria-hidden />
          People who matter
        </CardTitle>
        <CardDescription>VIPs always get through.</CardDescription>
        <SeeAll href="/dashboard/people">People</SeeAll>
      </CardHeader>
      <CardContent>
        <Loadable state={state} what="your people">
          {({ people }) =>
            people.length === 0 ? (
              <p className="text-sm text-muted">No one yet.</p>
            ) : (
              <ul className="space-y-2">
                {people.slice(0, 5).map((p) => (
                  <li key={p.id} className="flex items-center gap-2 text-sm">
                    <span className="grid size-7 place-items-center rounded-full bg-mist text-xs font-semibold text-violet-700">
                      {initialOf(p.name)}
                    </span>
                    <span className="min-w-0 font-medium text-ink [overflow-wrap:anywhere]">{p.name}</span>
                    <span className="text-muted">{capitalise(p.relationship)}</span>
                    {p.vip && (
                      <Badge className="ml-auto gap-1">
                        <Star className="size-3" aria-hidden />
                        VIP
                      </Badge>
                    )}
                  </li>
                ))}
              </ul>
            )
          }
        </Loadable>
      </CardContent>
    </Card>
  )
}

function RulesCard({ name, state }: { name: string; state: LoadState<Preferences> }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="size-4 text-violet-600" aria-hidden />
          Your rules
        </CardTitle>
        <CardDescription>How {name} treats your time.</CardDescription>
        <SeeAll href="/dashboard/settings">Settings</SeeAll>
      </CardHeader>
      <CardContent>
        <Loadable state={state} what="your rules">
          {(prefs) => (
            <dl className="space-y-3 text-sm">
              <div>
                <dt className="sr-only">Quiet hours</dt>
                <dd className="flex gap-2 text-ink">
                  <Clock className="mt-0.5 size-4 shrink-0 text-muted" aria-hidden />
                  <span>
                    Quiet {prefs.quiet_start}–{prefs.quiet_end}
                    <span className="text-muted"> ({prefs.timezone})</span>
                  </span>
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted">Interrupts you for</dt>
                <dd className="mt-0.5 text-ink">{prefs.urgent_topics.length ? prefs.urgent_topics.join('; ') : 'Nothing yet'}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted">Digest</dt>
                <dd className="mt-0.5 text-ink">{DIGEST_LABEL[prefs.digest] ?? prefs.digest}</dd>
              </div>
            </dl>
          )}
        </Loadable>
      </CardContent>
    </Card>
  )
}

function ComingUpCard({ name, state }: { name: string; state: LoadState<{ reminders: Reminder[] }> }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <AlarmClock className="size-4 text-violet-600" aria-hidden />
          Coming up
        </CardTitle>
        <SeeAll href="/dashboard/reminders">Reminders</SeeAll>
      </CardHeader>
      <CardContent>
        <Loadable state={state} what="your reminders">
          {({ reminders }) =>
            reminders.length === 0 ? (
              <p className="text-sm text-muted">
                Nothing yet. Tell {name} &ldquo;remind me to…&rdquo; and she&apos;ll bring it back on time.
              </p>
            ) : (
              <ul className="space-y-2.5">
                {reminders.slice(0, 3).map((r) => (
                  <li key={r.id} className="text-sm">
                    <p className="line-clamp-1 font-medium text-ink">{r.text}</p>
                    <p className="text-xs text-muted">{r.due_text}</p>
                  </li>
                ))}
              </ul>
            )
          }
        </Loadable>
      </CardContent>
    </Card>
  )
}

function LatestCard({ name, state }: { name: string; state: LoadState<Message[]> }) {
  return (
    <Card className="lg:col-span-3">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MessageCircle className="size-4 text-violet-600" aria-hidden />
          Latest with {name}
        </CardTitle>
        <SeeAll href="/dashboard/chat">Open chat</SeeAll>
      </CardHeader>
      <CardContent>
        <Loadable state={state} what="your chat">
          {(messages) => {
            const recent = messages.filter((m) => m.kind !== 'onboarding').slice(-3)
            return recent.length === 0 ? (
              <p className="text-sm text-muted">You haven&apos;t chatted since the introduction. Ask {name} anything.</p>
            ) : (
              <ul className="space-y-3">
                {recent.map((m) => (
                  <li key={m.id} className="flex gap-3 text-sm">
                    <span className="w-12 shrink-0 text-xs text-muted">{m.from === 'you' ? 'You' : name}</span>
                    <p className="line-clamp-2 text-body">{m.text}</p>
                  </li>
                ))}
              </ul>
            )
          }}
        </Loadable>
      </CardContent>
    </Card>
  )
}
