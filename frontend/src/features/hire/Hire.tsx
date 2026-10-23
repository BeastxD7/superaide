'use client'

import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { ChevronLeft, ShieldCheck } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { call, type AgentCard } from '@/lib/api'
import { LoadError } from '@/components/load-state'
import { errorMessage } from '@/lib/action'
import type { Session } from '@/lib/session-context'
import { Orb } from '@/components/Orb'
import { homeFor } from '@/lib/session'
import { AccountMenu, BrandMark, Button, Notice } from '@/components/site'

// Hiring is three steps: create an account (or log in), agree to how the agent
// handles what you share, and you're hired. The account step lives on #/signup.
type Stage = 'agree' | 'hired'

const slide = {
  initial: { opacity: 0, x: 40 },
  animate: { opacity: 1, x: 0, transition: { duration: 0.45, ease: [0.22, 1, 0.36, 1] as const } },
  exit: { opacity: 0, x: -40, transition: { duration: 0.25 } },
}

export function Hire({ slug, session }: { slug: string; session: Session }) {
  const router = useRouter()
  const [agent, setAgent] = useState<AgentCard | null>(null)
  const [stage, setStage] = useState<Stage>('agree')
  const [agreed, setAgreed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const account = session.account

  // The agent card holds the notice the owner must agree to: until it has loaded, the form stays shut.
  const loadAgent = useCallback(
    () =>
      call<AgentCard[]>('/agents').then(
        (all) => {
          setAgent(all.find((a) => a.slug === slug) ?? null)
          setLoadError(null)
        },
        (e: unknown) => setLoadError(errorMessage(e)),
      ),
    [slug],
  )
  useEffect(() => {
    void loadAgent()
  }, [loadAgent])

  // Step 1 is having an account. Already hired: carry on where you left off.
  useEffect(() => {
    if (!account) router.replace(`/signup?next=${encodeURIComponent(`/agents/${slug}/hire`)}`)
    else if (account.hire && stage !== 'hired') router.replace(homeFor(account))
  }, [account, slug, stage, router])

  async function hire(e: FormEvent) {
    e.preventDefault()
    if (!agent || !agreed) return
    setBusy(true)
    setError(null)
    try {
      await call('/hire', { method: 'POST', json: { agent: agent.slug, accepted_notice: true } })
      setStage('hired')
      await session.refresh()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  if (!account) return <div className="min-h-dvh mesh" aria-busy="true" />
  const agentName = agent?.name ?? 'Ava'
  const first = account.user.name.split(' ')[0]
  const progress = stage === 'agree' ? 0.66 : 1

  return (
    <div className="mesh flex min-h-dvh flex-col">
      <header className="mx-auto flex w-full max-w-xl items-center gap-4 page-x pt-4 sm:pt-6">
        {stage === 'agree' ? (
          <Link
            href={`/agents/${slug}`}
            className="grid size-11 shrink-0 place-items-center rounded-full bg-white text-ink shadow-sm hover:bg-mist"
            aria-label="Back"
          >
            <ChevronLeft className="size-5" />
          </Link>
        ) : (
          <span className="grid size-11 shrink-0 place-items-center">
            <BrandMark className="size-7" />
          </span>
        )}
        <div
          className="h-1.5 flex-1 overflow-hidden rounded-full bg-white"
          role="progressbar"
          aria-valuenow={Math.round(progress * 100)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Hiring progress"
        >
          <motion.div
            className="h-full rounded-full bg-gradient-to-r from-violet-400 to-violet-600"
            initial={{ width: '33%' }}
            animate={{ width: `${progress * 100}%` }}
            transition={{ type: 'spring', stiffness: 120, damping: 20 }}
          />
        </div>
        <AccountMenu session={session} />
      </header>

      <main className="mx-auto flex w-full max-w-xl flex-1 flex-col page-x pt-6 pb-8 sm:pt-10">
        <AnimatePresence mode="wait">
          {stage === 'agree' && (
            <motion.form key="agree" {...slide} onSubmit={hire} className="flex flex-1 flex-col">
              <h1 className="text-3xl font-medium tight sm:text-4xl">One thing before you start, {first}</h1>
              <p className="mt-2 text-body">Here's how {agentName} handles what you tell it.</p>
              <div className="glass mt-8 rounded-[24px] border border-white/70 p-6">
                {loadError && !agent ? (
                  <LoadError what={`${agentName}'s details`} error={loadError} onRetry={loadAgent} />
                ) : (
                  <p className="leading-relaxed text-ink">{agent?.notice}</p>
                )}
                <ul className="mt-5 space-y-3 text-sm">
                  {(agent?.never ?? []).map((n) => (
                    <li key={n} className="flex items-center gap-3 text-body">
                      <ShieldCheck className="size-4 shrink-0 text-violet-600" /> {agentName} never{' '}
                      {n.charAt(0).toLowerCase() + n.slice(1)}
                    </li>
                  ))}
                </ul>
              </div>
              <label className="mt-5 flex cursor-pointer items-start gap-3 rounded-2xl bg-white p-4 text-ink">
                <input
                  type="checkbox"
                  checked={agreed}
                  onChange={(e) => setAgreed(e.target.checked)}
                  disabled={!agent}
                  className="mt-0.5 size-5 accent-violet-600"
                />
                <span>I understand {agentName} is an AI, and I agree to how it handles what I share.</span>
              </label>
              {error && (
                <div className="mt-4">
                  <Notice tone="error">{error}</Notice>
                </div>
              )}
              <div className="mt-auto pt-10">
                <Button tone="violet" type="submit" disabled={!agreed || busy || !agent} className="w-full py-4">
                  {busy ? `Hiring ${agentName}…` : `Hire ${agentName}`}
                </Button>
                <p className="mt-3 text-center text-xs text-muted">Signed in as {account.user.email}</p>
              </div>
            </motion.form>
          )}

          {stage === 'hired' && (
            <motion.div key="hired" {...slide} className="flex flex-1 flex-col items-center text-center">
              <motion.div
                initial={{ scale: 0.6, rotate: -8 }}
                animate={{ scale: 1, rotate: 0 }}
                transition={{ type: 'spring', stiffness: 160, damping: 10 }}
              >
                <Orb hue={agent?.hue ?? 'indigo'} size={190} mood="happy" />
              </motion.div>
              <h1 className="mt-10 text-4xl font-medium tight">{agentName} is on your team</h1>
              <p className="mt-3 max-w-sm text-body">
                {agentName} would like to introduce itself and get to know you. It takes about five minutes.
              </p>
              <div className="mt-auto w-full pt-10">
                <Button tone="violet" className="w-full py-4" onClick={() => router.push('/meet')}>
                  Say hello to {agentName}
                </Button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </main>
    </div>
  )
}
