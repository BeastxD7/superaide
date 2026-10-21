import { ChevronLeft, ShieldCheck } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useState, type FormEvent } from 'react'
import { AppError, call, type AgentCard } from './api'
import type { Session } from './AppRoot'
import { Orb } from './Orb'
import { Button, Notice, PRODUCT, Sparkle } from './ui'

type Stage = 'name' | 'agree' | 'hired'

const slide = {
  initial: { opacity: 0, x: 40 },
  animate: { opacity: 1, x: 0, transition: { duration: 0.45, ease: [0.22, 1, 0.36, 1] as const } },
  exit: { opacity: 0, x: -40, transition: { duration: 0.25 } },
}

export function Hire({ slug, session }: { slug: string; session: Session }) {
  const [agent, setAgent] = useState<AgentCard | null>(null)
  const [stage, setStage] = useState<Stage>('name')
  const [name, setName] = useState('')
  const [agreed, setAgreed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    call<AgentCard[]>('/agents').then((all) => setAgent(all.find((a) => a.slug === slug) ?? null))
  }, [slug])

  // Already hired from this browser: carry on where you left off.
  useEffect(() => {
    if (session.me && stage !== 'hired') window.location.hash = session.me.onboarding.done ? '/workspace' : '/meet'
  }, [session.me, stage])

  async function hire() {
    if (!agent) return
    setBusy(true)
    setError(null)
    try {
      const r = await call<{ token: string }>('/hire', { method: 'POST', json: { agent: agent.slug, name: name.trim(), accepted_notice: agreed } })
      setStage('hired')
      await session.signedIn(r.token)
    } catch (e) {
      setError(e instanceof AppError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function next(e: FormEvent) {
    e.preventDefault()
    if (stage === 'name' && name.trim()) setStage('agree')
    else if (stage === 'agree' && agreed) void hire()
  }

  const agentName = agent?.name ?? 'Ava'
  const progress = stage === 'name' ? 0.33 : stage === 'agree' ? 0.66 : 1

  return (
    <div className="mesh flex min-h-dvh flex-col">
      <header className="mx-auto flex w-full max-w-xl items-center gap-4 px-5 pt-6">
        {stage !== 'hired' ? (
          <button
            onClick={() => (stage === 'agree' ? setStage('name') : (window.location.hash = `/agents/${slug}`))}
            className="grid size-10 shrink-0 place-items-center rounded-full bg-white text-ink shadow-sm hover:bg-mist"
            aria-label="Back"
          >
            <ChevronLeft className="size-5" />
          </button>
        ) : (
          <span className="grid size-10 shrink-0 place-items-center text-forest-800">
            <Sparkle />
          </span>
        )}
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white" role="progressbar" aria-valuenow={Math.round(progress * 100)} aria-valuemin={0} aria-valuemax={100} aria-label="Hiring progress">
          <motion.div className="h-full rounded-full bg-gradient-to-r from-violet-400 to-violet-600" animate={{ width: `${progress * 100}%` }} transition={{ type: 'spring', stiffness: 120, damping: 20 }} />
        </div>
        <a href="#/" className="text-sm font-medium text-muted hover:text-ink">
          {PRODUCT}
        </a>
      </header>

      <main className="mx-auto flex w-full max-w-xl flex-1 flex-col px-5 pt-10 pb-8">
        <AnimatePresence mode="wait">
          {stage === 'name' && (
            <motion.form key="name" {...slide} onSubmit={next} className="flex flex-1 flex-col">
              <div className="flex flex-col items-center text-center">
                <Orb hue={agent?.hue ?? 'indigo'} size={150} track />
                <h1 className="mt-8 text-4xl font-medium tight">Hire {agentName}</h1>
                <p className="mt-2 text-body">
                  {agent?.role ?? 'LinkedIn content strategist'}. {agent?.price ?? 'Free while in beta'}.
                </p>
              </div>
              <label className="mt-10 block">
                <span className="text-sm font-medium text-ink">Your name</span>
                <input
                  autoFocus
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  maxLength={80}
                  autoComplete="name"
                  placeholder="e.g. Shashank Rao"
                  className="mt-2 w-full rounded-2xl border border-white bg-white px-5 py-4 text-lg text-ink shadow-[0_12px_30px_-22px_rgba(60,30,140,0.6)] outline-none placeholder:text-muted/60 focus:border-violet-400"
                />
                <span className="mt-2 block text-sm text-muted">{agentName} will call you by your first name.</span>
              </label>
              <div className="mt-auto pt-10">
                <Button tone="violet" type="submit" disabled={!name.trim()} className="w-full py-4 text-base">
                  Continue
                </Button>
              </div>
            </motion.form>
          )}

          {stage === 'agree' && (
            <motion.form key="agree" {...slide} onSubmit={next} className="flex flex-1 flex-col">
              <h1 className="text-4xl font-medium tight">Before you start</h1>
              <p className="mt-2 text-body">Here's how {agentName} handles what you tell it.</p>
              <div className="glass mt-8 rounded-[24px] border border-white/70 p-6">
                <p className="leading-relaxed text-ink">{agent?.notice}</p>
                <ul className="mt-5 space-y-3 text-sm">
                  {(agent?.never ?? []).map((n) => (
                    <li key={n} className="flex items-center gap-3 text-body">
                      <ShieldCheck className="size-4 shrink-0 text-violet-600" /> {agentName} never {n.charAt(0).toLowerCase() + n.slice(1)}
                    </li>
                  ))}
                </ul>
              </div>
              <label className="mt-5 flex cursor-pointer items-start gap-3 rounded-2xl bg-white p-4 text-ink">
                <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} className="mt-0.5 size-5 accent-violet-600" />
                <span>
                  I understand {agentName} is an AI, and I agree to how it handles what I share.
                </span>
              </label>
              {error && (
                <div className="mt-4">
                  <Notice tone="error">{error}</Notice>
                </div>
              )}
              <div className="mt-auto pt-10">
                <Button tone="violet" type="submit" disabled={!agreed || busy} className="w-full py-4 text-base">
                  {busy ? `Hiring ${agentName}…` : `Hire ${agentName}`}
                </Button>
              </div>
            </motion.form>
          )}

          {stage === 'hired' && (
            <motion.div key="hired" {...slide} className="flex flex-1 flex-col items-center text-center">
              <motion.div initial={{ scale: 0.6, rotate: -8 }} animate={{ scale: 1, rotate: 0 }} transition={{ type: 'spring', stiffness: 160, damping: 10 }}>
                <Orb hue={agent?.hue ?? 'indigo'} size={190} mood="happy" />
              </motion.div>
              <h1 className="mt-10 text-4xl font-medium tight">{agentName} is on your team</h1>
              <p className="mt-3 max-w-sm text-body">
                {agentName} would like to introduce itself and get to know you. It takes about five minutes.
              </p>
              <div className="mt-auto w-full pt-10">
                <Button tone="violet" className="w-full py-4 text-base" onClick={() => (window.location.hash = '/meet')}>
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
