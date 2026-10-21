'use client'

import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { Check, ChevronLeft, MessageCircle, Send, ShieldCheck, Hash } from 'lucide-react'
import { motion } from 'motion/react'
import { useEffect, useState } from 'react'
import { call, type AgentCard } from '@/lib/api'
import type { Session } from '@/lib/session-context'
import { Orb } from '@/components/Orb'
import { homeFor } from '@/lib/session'
import { Button, NavBar, STAGES } from '@/components/ui'

const CHANNEL_ICONS: Record<string, typeof Send> = { 'In the app': MessageCircle, Telegram: Send, Slack: Hash }

type ProfileProps = { slug: string; session: Session; initialAgents?: AgentCard[] | null }

export function Profile({ slug, session, initialAgents = null }: ProfileProps) {
  const router = useRouter()
  const initial = initialAgents?.find((x) => x.slug === slug) ?? null
  const [agent, setAgent] = useState<AgentCard | null>(initial)
  const [missing, setMissing] = useState(initialAgents !== null && initial === null)

  useEffect(() => {
    if (initialAgents) return // rendered on the server with the catalog already
    call<AgentCard[]>('/agents')
      .then((all) => {
        const a = all.find((x) => x.slug === slug)
        if (a) setAgent(a)
        else setMissing(true)
      })
      .catch(() => setMissing(true))
  }, [slug, initialAgents])

  if (missing)
    return (
      <div className="min-h-dvh">
        <NavBar session={session} />
        <p className="mx-auto max-w-6xl px-6 pt-20 text-lg">
          That agent isn't here. <Link className="text-violet-600 underline" href="/">See all agents</Link>
        </p>
      </div>
    )
  if (!agent) return <div className="min-h-dvh mesh" aria-busy="true" />

  const available = agent.status === 'available'
  const mine = session.account?.hire?.agent === agent.slug
  const taken = Boolean(agent.hired_by) && !mine

  return (
    <div className="min-h-dvh bg-canvas pb-16">
      <NavBar session={session} links={false} cta={false} />
      <div className="mx-auto max-w-6xl px-4 pt-6 sm:px-6">
        <Link href="/" className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-sm text-muted hover:text-ink">
          <ChevronLeft className="size-4" /> All agents
        </Link>
      </div>

      <div className="mx-auto mt-4 grid max-w-6xl gap-6 px-4 sm:px-6 lg:grid-cols-[420px_1fr]">
        {/* The agent's card */}
        <motion.aside
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
          className="mesh relative flex flex-col items-center overflow-hidden rounded-[28px] border border-lilac-200/60 px-6 pt-12 pb-8 text-center lg:sticky lg:top-24 lg:self-start"
        >
          <Orb hue={agent.hue} size={190} track={available} mood={available ? 'idle' : 'asleep'} label={agent.name} />
          <h1 className="mt-8 text-5xl font-medium tight">{agent.name}</h1>
          <p className="mt-1 text-lg text-violet-700">{agent.role}</p>
          <div className="mt-5 flex flex-wrap justify-center gap-2 text-xs font-medium">
            <span className="inline-flex items-center gap-2 rounded-full bg-white px-3 py-1.5 text-ink">
              <span className={`size-1.5 rounded-full ${!available ? 'bg-muted' : taken ? 'bg-warn' : 'bg-ok'}`} />
              {!available ? 'In training' : mine ? 'Works for you' : taken ? `Works for ${agent.hired_by}` : 'Available now'}
            </span>
            <span className="rounded-full bg-white px-3 py-1.5 text-ink">{agent.stage} stage</span>
            <span className="rounded-full bg-white px-3 py-1.5 text-ink">{agent.price}</span>
          </div>

          <div className="mt-8 w-full">
            {!available ? (
              <p className="rounded-2xl bg-white/70 px-4 py-3 text-sm text-muted">{agent.name} is still in training. Ava is ready now.</p>
            ) : mine ? (
              <Button tone="dark" icon className="w-full justify-between" onClick={() => router.push(homeFor(session.account))}>
                Open {agent.name}
              </Button>
            ) : taken ? (
              <p className="rounded-2xl bg-white/70 px-4 py-3 text-left text-sm text-body">
                {agent.name} already works for {agent.hired_by}.{' '}
                {session.account ? (
                  <>You're signed in as {session.account.user.name}. </>
                ) : (
                  <>
                    If that's you,{' '}
                    <Link className="font-medium text-violet-700 underline" href={`/login?next=${encodeURIComponent('/meet')}`}>
                      log in
                    </Link>
                    .
                  </>
                )}
              </p>
            ) : (
              <Button tone="violet" icon className="w-full justify-between" onClick={() =>
                  router.push(session.account
                    ? `/agents/${agent.slug}/hire`
                    : `/signup?next=${encodeURIComponent(`/agents/${agent.slug}/hire`)}`)
                }>
                Hire {agent.name}
              </Button>
            )}
          </div>
        </motion.aside>

        {/* What it does, what it never does, how it grows */}
        <div className="space-y-4">
          <section className="rounded-[28px] border border-line p-7 sm:p-9">
            <p className="text-2xl leading-snug font-medium text-ink tight sm:text-3xl">{agent.tagline}</p>
          </section>

          {agent.skills.length > 0 && (
            <section className="rounded-[28px] bg-lilac-50 p-7 sm:p-9">
              <h2 className="text-xl font-medium tight">What {agent.name} does</h2>
              <ul className="mt-5 grid gap-3 sm:grid-cols-2">
                {agent.skills.map((s) => (
                  <li key={s} className="flex items-start gap-3 rounded-2xl bg-white p-4 text-sm text-ink">
                    <span className="grid size-7 shrink-0 place-items-center rounded-full bg-lilac-100 text-violet-600">
                      <Check className="size-4" />
                    </span>
                    <span className="pt-1">{s}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {agent.never.length > 0 && (
            <section className="rounded-[28px] bg-haze-100 p-7 sm:p-9">
              <h2 className="text-xl font-medium tight">What {agent.name} never does</h2>
              <ul className="mt-5 space-y-3">
                {agent.never.map((s) => (
                  <li key={s} className="flex items-center gap-3 text-ink">
                    <ShieldCheck className="size-5 shrink-0 text-violet-600" />
                    {s}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section className="rounded-[28px] border border-line p-7 sm:p-9">
            <h2 className="text-xl font-medium tight">How {agent.name} grows</h2>
            <p className="mt-2 max-w-xl text-sm text-body">
              {agent.name} moves up a stage only when the numbers show it has learned, and only when you sign off.
            </p>
            <ol className="relative mt-7 space-y-6 pl-8">
              <span aria-hidden className="absolute top-2 bottom-2 left-[7px] w-px bg-line" />
              {STAGES.map((s, i) => {
                const now = available && i === 0
                return (
                  <li key={s.name} className="relative">
                    <span
                      className={`absolute top-1 -left-8 size-[15px] rounded-full border-2 ${
                        now ? 'border-violet-500 bg-violet-500 shadow-[0_0_0_5px_rgba(124,92,250,0.18)]' : 'border-line bg-white'
                      }`}
                    />
                    <p className="font-medium text-ink">
                      {s.name}
                      {now && <span className="ml-2 rounded-full bg-lilac-100 px-2 py-0.5 text-xs text-violet-700">{agent.name} is here</span>}
                    </p>
                    <p className="mt-1 text-sm text-body">{s.does}</p>
                  </li>
                )
              })}
            </ol>
          </section>

          {agent.channels.length > 0 && (
            <section className="rounded-[28px] bg-sky-100 p-7 sm:p-9">
              <h2 className="text-xl font-medium tight">Where you can talk to {agent.name}</h2>
              <ul className="mt-5 flex flex-wrap gap-2">
                {agent.channels.map((c) => {
                  const Icon = CHANNEL_ICONS[c] ?? MessageCircle
                  return (
                    <li key={c} className="inline-flex items-center gap-2 rounded-full bg-white px-4 py-2 text-sm text-ink">
                      <Icon className="size-4 text-violet-600" /> {c}
                    </li>
                  )
                })}
              </ul>
              <p className="mt-4 max-w-xl text-sm text-body">
                It's the same {agent.name} everywhere, and it knows it's you. A new account is only linked to you after a one-time code.
              </p>
            </section>
          )}
        </div>
      </div>
    </div>
  )
}
