'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { BookOpen, Check, Lock, MessageCircle, ShieldCheck, Smartphone } from 'lucide-react'
import { motion } from 'motion/react'
import { useEffect, useState } from 'react'
import { BrandIcon } from '@/components/BrandIcon'
import { call, type AgentCard } from '@/lib/api'
import type { Session } from '@/lib/session-context'
import { Orb, huePanel } from '@/components/Orb'
import { homeFor } from '@/lib/session'
import { Button, Footer, NavBar, Sparkle, STAGES } from '@/components/site'

const MotionLink = motion.create(Link)

const rise = {
  hidden: { opacity: 0, y: 24, filter: 'blur(6px)' },
  show: (i: number) => ({
    opacity: 1,
    y: 0,
    filter: 'blur(0px)',
    transition: { delay: 0.08 * i, duration: 0.7, ease: [0.22, 1, 0.36, 1] as const },
  }),
}

export function Marketplace({ session, initialAgents = null }: { session: Session; initialAgents?: AgentCard[] | null }) {
  const router = useRouter()
  const [agents, setAgents] = useState<AgentCard[] | null>(initialAgents)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (initialAgents) return // rendered on the server with the catalog already
    call<AgentCard[]>('/agents')
      .then(setAgents)
      .catch((e: Error) => setError(e.message))
  }, [initialAgents])

  const ava = agents?.find((a) => a.slug === 'ava')
  const mine = Boolean(session.account?.hire)

  return (
    <div className="min-h-dvh bg-canvas">
      <NavBar session={session} />

      {/* Hero: the agent itself is the first thing you meet */}
      <section className="px-4 pt-10 sm:px-6 sm:pt-14">
        <div className="mx-auto grid max-w-6xl items-start gap-8 lg:grid-cols-[1fr_300px_1fr] lg:gap-10">
          <div className="lg:pt-6">
            <motion.h1
              custom={0}
              variants={rise}
              initial="hidden"
              animate="show"
              className="text-[44px] leading-[1.02] font-medium tight sm:text-[58px]"
            >
              Hire an AI that grows up on the job
            </motion.h1>
            <motion.div custom={2} variants={rise} initial="hidden" animate="show" className="mt-8">
              <Button tone="light" icon onClick={() => document.getElementById('agents')?.scrollIntoView({ behavior: 'smooth' })}>
                Meet the agents
              </Button>
            </motion.div>
          </div>

          <motion.div
            initial={{ opacity: 0, scale: 0.7 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ type: 'spring', stiffness: 120, damping: 14, delay: 0.15 }}
            className="relative z-10 mx-auto lg:-mb-24"
          >
            <Orb hue="indigo" size={300} track label="Ava, an AI content strategist" />
            <motion.div
              initial={{ opacity: 0, y: 10, scale: 0.9 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              transition={{ delay: 0.9, type: 'spring', stiffness: 200, damping: 18 }}
              className="glass absolute -top-3 -left-4 rounded-2xl rounded-bl-md border border-white/70 px-4 py-2.5 text-sm whitespace-nowrap text-ink shadow-[0_12px_30px_-16px_rgba(60,30,140,0.5)] sm:-left-20"
            >
              Hi, I'm Ava. New here, keen to learn.
            </motion.div>
          </motion.div>

          <motion.div custom={3} variants={rise} initial="hidden" animate="show" className="lg:pt-8">
            <div className="flex items-center gap-3">
              <div className="flex -space-x-3">
                <Orb hue="indigo" size={40} className="rounded-full ring-2 ring-white" />
                <Orb hue="sky" size={40} mood="asleep" className="rounded-full ring-2 ring-white" />
                <Orb hue="rose" size={40} mood="asleep" className="rounded-full ring-2 ring-white" />
              </div>
              <p className="text-2xl font-medium text-ink tight">
                {ava?.hired ? (mine ? 'Ava works for you' : 'Ava is already working for someone') : 'Ava is ready to start'}
              </p>
            </div>
            <p className="mt-6 max-w-xs leading-relaxed text-body">
              Every agent starts as a beginner. It learns how you work, keeps a notebook you can read, and takes on more
              only when it has earned it.
            </p>
          </motion.div>
        </div>
      </section>

      {/* The growth band: the idea in one picture */}
      <section className="mt-10 px-4 sm:px-6 lg:mt-0">
        <motion.div
          initial={{ opacity: 0, y: 40 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.35, duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
          className="mx-auto max-w-6xl overflow-hidden rounded-[28px] bg-[radial-gradient(90%_70%_at_50%_0%,#5b4bc4_0%,transparent_60%),linear-gradient(180deg,#2a2160,#120e2a)] pt-12 text-white lg:pt-28"
        >
          <p
            aria-hidden
            className="bg-gradient-to-b from-white/85 to-white/10 bg-clip-text px-4 text-center text-[15vw] leading-[0.95] font-medium whitespace-nowrap text-transparent tight lg:text-[150px]"
          >
            Infant to Adult
          </p>
          <div className="mt-8 border-t border-white/10 px-6 py-10 sm:px-10">
            <h2 className="sr-only">How an agent grows</h2>
            <ol className="relative grid gap-8 sm:grid-cols-4 sm:gap-6">
              <div aria-hidden className="absolute top-[7px] right-0 left-0 hidden h-px bg-white/15 sm:block" />
              {STAGES.map((s, i) => (
                <li key={s.name} className="relative">
                  <span
                    className={`relative block size-[15px] rounded-full border-2 ${
                      i === 0 ? 'border-violet-400 bg-violet-400 shadow-[0_0_0_6px_rgba(154,130,255,0.25)]' : 'border-white/30 bg-night-900'
                    }`}
                  />
                  <p className="mt-4 text-lg font-medium text-white">
                    {s.name}
                    {i === 0 && <span className="ml-2 align-middle text-xs font-normal text-violet-400">Ava today</span>}
                  </p>
                  <p className="mt-1 text-sm leading-relaxed text-white/60">{s.does}</p>
                </li>
              ))}
            </ol>
          </div>
        </motion.div>
      </section>

      {/* Agents */}
      <section id="agents" className="scroll-mt-24 px-4 pt-24 sm:px-6">
        <div className="mx-auto max-w-6xl">
          <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
            <h2 className="max-w-lg text-4xl leading-tight font-medium tight sm:text-5xl">Meet your future teammates</h2>
            <p className="max-w-sm text-body">One is ready to work today. The others are still in training.</p>
          </div>
          {error && <p className="mt-6 text-danger">{error}</p>}
          <div className="mt-10 grid gap-4 lg:grid-cols-[1.4fr_1fr]">
            {ava && <FeaturedAgent agent={ava} mine={mine && Boolean(ava.hired)} />}
            <div className="grid gap-4">
              {agents
                ?.filter((a) => a.status === 'coming_soon')
                .map((a) => (
                  <div key={a.slug} className={`flex items-center gap-5 rounded-[24px] p-6 ${huePanel(a.hue)}`}>
                    <Orb hue={a.hue} size={84} mood="asleep" />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-baseline gap-x-3">
                        <h3 className="text-2xl font-medium tight">{a.name}</h3>
                        <span className="text-sm text-muted">{a.role}</span>
                      </div>
                      <p className="mt-1.5 text-sm leading-relaxed text-body">{a.tagline}</p>
                      <p className="mt-3 inline-block rounded-full bg-white/70 px-3 py-1 text-xs font-medium text-muted">
                        In training
                      </p>
                    </div>
                  </div>
                ))}
            </div>
          </div>
        </div>
      </section>

      {/* Safety: what makes it trustworthy, shown with the real controls */}
      <section id="safety" className="scroll-mt-24 px-4 pt-24 sm:px-6">
        <div className="mx-auto max-w-6xl">
          <h2 className="max-w-2xl text-4xl leading-tight font-medium tight sm:text-5xl">Works like a colleague, minds your trust like one</h2>
          <div className="relative mt-10 grid gap-4 md:grid-cols-[1.5fr_1fr]">
            <span
              aria-hidden
              className="absolute top-1/2 left-[60%] z-10 hidden size-12 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full bg-white text-violet-600 md:grid"
            >
              <Sparkle className="size-7" />
            </span>

            <Bento tone="bg-haze-100" title="A notebook you can read" text="Everything Ava remembers about you is written down. Read it any time and remove anything.">
              <div className="rounded-2xl bg-white p-4 shadow-[0_20px_40px_-28px_rgba(15,42,35,0.5)]">
                <p className="flex items-center gap-2 text-sm font-medium text-ink">
                  <BookOpen className="size-4" /> Ava's notebook
                </p>
                {['Leads applied AI at a consultancy', 'Wants to win clients and share what they learn', 'Likes to sound direct and story-led'].map((t) => (
                  <div key={t} className="mt-2 flex items-center justify-between gap-3 rounded-xl bg-mist px-3 py-2 text-sm">
                    <span className="truncate">{t}</span>
                    <span className="shrink-0 text-xs text-muted">Forget</span>
                  </div>
                ))}
              </div>
            </Bento>

            <Bento tone="bg-lilac-100" title="Knows it's you, on any app" text="Talk in the app, on Telegram or in Slack. A new account only counts as you after a one-time code from one Ava already trusts.">
              <div className="flex flex-wrap gap-2">
                {[
                  { icon: <MessageCircle className="size-4 text-violet-600" aria-hidden />, label: 'In the app' },
                  { icon: <BrandIcon brand="telegram" size={16} decorative />, label: 'Telegram' },
                  { icon: <BrandIcon brand="slack" size={16} decorative />, label: 'Slack' },
                ].map(({ icon, label }) => (
                  <span key={label} className="inline-flex items-center gap-2 rounded-full bg-white px-3 py-2 text-sm text-ink">
                    {icon} {label}
                    <Check className="size-3.5 text-ok" />
                  </span>
                ))}
              </div>
            </Bento>

            <Bento tone="bg-sky-100" title="Asks before it acts" text="Nothing is posted, sent or deleted without your yes. Strangers get a polite reply and nothing else.">
              <div className="flex items-center justify-between gap-4 rounded-2xl bg-white p-4 shadow-[0_20px_40px_-28px_rgba(30,40,120,0.5)]">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-ink">Post to LinkedIn?</p>
                  <p className="truncate text-xs text-muted">"Most AI pilots fail for boring reasons…"</p>
                </div>
                <div className="flex shrink-0 gap-2">
                  <span className="rounded-full bg-mist px-3 py-1.5 text-xs">Not yet</span>
                  <span className="rounded-full bg-ink px-3 py-1.5 text-xs text-white">Approve</span>
                </div>
              </div>
            </Bento>

            <Bento tone="bg-rose-100" title="Private stays private" text="What you say one to one is never repeated in a group or to anyone else. And Ava always says it's an AI.">
              <div className="flex gap-3 text-ink">
                <span className="grid size-11 place-items-center rounded-full bg-white">
                  <Lock className="size-5" />
                </span>
                <span className="grid size-11 place-items-center rounded-full bg-white">
                  <ShieldCheck className="size-5" />
                </span>
                <span className="grid size-11 place-items-center rounded-full bg-white">
                  <Smartphone className="size-5" />
                </span>
              </div>
            </Bento>
          </div>
        </div>
      </section>

      {/* How hiring works: a real sequence */}
      <section id="how" className="scroll-mt-24 px-4 py-24 sm:px-6">
        <div className="mx-auto max-w-6xl">
          <h2 className="text-4xl font-medium tight sm:text-5xl">How hiring works</h2>
          <ol className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {[
              ['Meet Ava', 'Read what Ava does, what it never does and how it grows.'],
              ['Hire', 'Tell Ava your name and agree to how it handles what you share. Free while in beta.'],
              ['Introductions', 'Ava introduces itself and asks about you, your goals and your voice. About five minutes.'],
              ['Work together', 'Ava drafts, you edit and approve. Every edit teaches it something.'],
            ].map(([title, text], i) => (
              <li key={title} className="rounded-[24px] border border-line p-6">
                <span className="grid size-9 place-items-center rounded-full bg-lilac-100 text-sm font-medium text-violet-700">{i + 1}</span>
                <h3 className="mt-6 text-xl font-medium tight">{title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-body">{text}</p>
              </li>
            ))}
          </ol>
          <div className="mt-10">
            <Button tone="dark" icon onClick={() => router.push(mine ? homeFor(session.account) : '/agents/ava')}>
              {mine ? 'Go to Ava' : 'Meet Ava'}
            </Button>
          </div>
        </div>
      </section>

      <Footer />
    </div>
  )
}

function FeaturedAgent({ agent, mine }: { agent: AgentCard; mine: boolean }) {
  const taken = Boolean(agent.hired) && !mine
  return (
    <MotionLink
      href={`/agents/${agent.slug}`}
      whileHover={{ y: -4 }}
      transition={{ type: 'spring', stiffness: 260, damping: 22 }}
      className="group relative flex flex-col overflow-hidden rounded-[28px] bg-lilac-100 p-7 sm:p-9"
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <span className="inline-flex items-center gap-2 rounded-full bg-white px-3 py-1 text-xs font-medium text-ink">
            <span className={`size-1.5 rounded-full ${taken ? 'bg-warn' : 'bg-ok'}`} />
            {mine ? 'Works for you' : taken ? 'Already hired' : 'Available now'}
          </span>
          <h3 className="mt-5 text-5xl font-medium tight">{agent.name}</h3>
          <p className="mt-1 text-lg text-violet-700">{agent.role}</p>
        </div>
        <Orb hue={agent.hue} size={132} className="-mt-2 -mr-2 transition-transform duration-500 group-hover:rotate-6" />
      </div>
      <p className="mt-5 max-w-md text-lg leading-relaxed text-ink">{agent.tagline}</p>
      <ul className="mt-6 flex flex-wrap gap-2">
        {agent.skills.slice(0, 3).map((s) => (
          <li key={s} className="rounded-full bg-white/80 px-3 py-1.5 text-sm text-body">
            {s}
          </li>
        ))}
      </ul>
      <div className="mt-8 flex flex-wrap items-center justify-between gap-4">
        <p className="text-sm text-muted">
          {agent.stage} stage. {agent.price}.
        </p>
        <span className="inline-flex items-center gap-3 rounded-full bg-ink py-1.5 pr-1.5 pl-5 font-medium text-white">
          Meet {agent.name}
          <span className="grid size-9 place-items-center rounded-full bg-white text-ink">
            <Sparkle className="size-4" />
          </span>
        </span>
      </div>
    </MotionLink>
  )
}

function Bento({ tone, title, text, children }: { tone: string; title: string; text: string; children: React.ReactNode }) {
  return (
    <div className={`flex flex-col justify-between gap-8 rounded-[24px] p-7 ${tone}`}>
      <div>
        <h3 className="text-3xl leading-tight font-medium tight">{title}</h3>
        <p className="mt-3 max-w-md text-sm leading-relaxed text-body">{text}</p>
      </div>
      {children}
    </div>
  )
}
