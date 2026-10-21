'use client'

import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { X } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { AppError, call, type Message, type StepView } from '@/lib/api'
import type { Session } from '@/lib/session-context'
import { Conversation, useReveal } from '@/components/Conversation'
import { Orb } from '@/components/Orb'
import { StepControls } from '@/features/onboarding/steps'
import { unlockSpeech } from '@/features/onboarding/speech'
import { VideoCall, type CallQuestion, type CallTurnResult } from '@/features/onboarding/VideoCall'
import { AccountMenu, Notice } from '@/components/ui'

export function Meet({ session }: { session: Session }) {
  const router = useRouter()
  const [view, setView] = useState<StepView | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [loaded, setLoaded] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fresh, setFresh] = useState(false)
  const scrollerRef = useRef<HTMLElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const me = session.me

  // Keep the latest line in view as the conversation grows or the answer panel changes size.
  useEffect(() => {
    const scroller = scrollerRef.current
    const content = contentRef.current
    if (!scroller || !content) return
    const stick = () => scroller.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' })
    const ro = new ResizeObserver(stick)
    ro.observe(scroller)
    ro.observe(content)
    return () => ro.disconnect()
  }, [loaded])

  useEffect(() => {
    if (!me) {
      router.push(session.account ? '/' : `/login?next=${encodeURIComponent('/meet')}`)
      return
    }
    Promise.all([call<StepView>('/onboarding'), call<Message[]>('/messages')])
      .then(([v, m]) => {
        setFresh(!m.some((x) => x.from === 'you'))
        setView(v)
        setMessages(m)
        setLoaded(true)
      })
      .catch((e: Error) => setError(e.message))
    // Only on first mount; later updates come from answers.
  }, [me?.person.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const { visible, typing, settled } = useReveal(messages, pending, fresh)

  const answer = useCallback(
    async (answer: Record<string, unknown>, said: string | null) => {
      if (!view) return
      setError(null)
      if (said) setMessages((m) => [...m, { id: `local-${Date.now()}`, from: 'you', text: said, at: new Date().toISOString() }])
      setPending(true)
      try {
        const v = await call<StepView>('/onboarding', { method: 'POST', json: { step: view.step, answer } })
        const m = await call<Message[]>('/messages')
        setView(v)
        setMessages(m)
        if (v.done) void session.refresh()
      } catch (e) {
        setError(e instanceof AppError ? e.message : String(e))
        setMessages((m) => m.filter((x) => !x.id.startsWith('local-')))
      } finally {
        setPending(false)
      }
    },
    [view, session],
  )

  // Re-read the step and conversation (after a file, link or call changed them).
  const reload = useCallback(async () => {
    const [v, m] = await Promise.all([call<StepView>('/onboarding'), call<Message[]>('/messages')])
    setView(v)
    setMessages(m)
  }, [])

  const [calling, setCalling] = useState(false)
  const [firstQuestion, setFirstQuestion] = useState<CallQuestion | null>(null)

  const [callCount, setCallCount] = useState(0)

  const openCall = useCallback(async () => {
    unlockSpeech() // must run inside the click, or Safari won't let Ava speak
    try {
      const r = await call<{ first: CallQuestion }>('/intro/call')
      setFirstQuestion(r.first)
      setCallCount((n) => n + 1)
      setCalling(true)
    } catch (e) {
      setError(e instanceof AppError ? e.message : String(e))
    }
  }, [])

  const endCall = useCallback(
    async (finished: boolean) => {
      if (finished) await call('/intro/call/finish', { method: 'POST' })
      setCalling(false)
      await reload()
    },
    [reload],
  )

  if (!me) return null
  const agentName = me.agent.name

  return (
    <div className="mesh flex h-dvh flex-col">
      <header className="z-20 border-b border-white/70 bg-white/80 backdrop-blur-xl">
        <div className="mx-auto flex max-w-2xl items-center gap-3 px-4 py-3">
          <Orb hue="indigo" size={40} mood={typing ? 'thinking' : 'idle'} />
          <div className="min-w-0 flex-1">
            <p className="font-medium text-ink">{agentName}</p>
            <p className="truncate text-xs text-muted">
              {view && !view.done ? `Getting to know you. Step ${view.index + 1} of ${view.total}: ${view.labels[view.index]}` : 'All set'}
            </p>
          </div>
          <AccountMenu session={session} />
          <Link href="/" className="grid size-9 place-items-center rounded-full text-muted hover:bg-white hover:text-ink" aria-label="Leave for now" title="Leave for now. Ava will pick up where you left off.">
            <X className="size-4" />
          </Link>
        </div>
        {view && (
          <div className="mx-auto flex max-w-2xl gap-1.5 px-4 pb-3" aria-hidden>
            {view.labels.map((l, i) => (
              <div key={l} className="h-1 flex-1 overflow-hidden rounded-full bg-lilac-100">
                <motion.div
                  className="h-full rounded-full bg-gradient-to-r from-violet-400 to-violet-600"
                  initial={false}
                  animate={{ width: view.done || i < view.index ? '100%' : i === view.index ? '45%' : '0%' }}
                  transition={{ type: 'spring', stiffness: 120, damping: 20 }}
                />
              </div>
            ))}
          </div>
        )}
      </header>

      <main ref={scrollerRef} className="min-h-0 flex-1 overflow-y-auto">
        <div ref={contentRef} className="mx-auto w-full max-w-2xl px-4 pt-6 pb-6">
          {!loaded && !error && <p className="text-center text-muted">{agentName} is getting ready…</p>}
          {fresh && (
            <motion.div initial={{ opacity: 0, scale: 0.8 }} animate={{ opacity: 1, scale: 1 }} transition={{ type: 'spring', stiffness: 140, damping: 14 }} className="mb-6 flex flex-col items-center text-center">
              <Orb hue="indigo" size={120} track mood={typing ? 'thinking' : 'idle'} />
              <p className="mt-4 text-sm text-muted">You hired {agentName} today</p>
            </motion.div>
          )}
          <Conversation messages={visible} typing={typing} hue="indigo" agentName={agentName} />
        </div>
      </main>

      <div className="px-3 pt-1 pb-3">
        <div className="mx-auto max-h-[62dvh] max-w-2xl overflow-y-auto rounded-[28px] border border-white bg-[#f6f2ff]/90 p-4 shadow-[0_-10px_40px_-24px_rgba(60,30,140,0.5)] backdrop-blur-xl">
          {error && (
            <div className="mb-3">
              <Notice tone="error">{error}</Notice>
            </div>
          )}
          {view?.hint && settled && <p className="mb-3 text-sm font-medium text-warn">{view.hint}</p>}
          <AnimatePresence mode="wait">
            {view && settled && !pending ? (
              <motion.div
                key={view.step + (view.input.kind === 'connect' ? String(view.input.invite_url) : '')}
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 8 }}
                transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
              >
                <StepControls
                  input={view.input}
                  done={view.done}
                  agentName={agentName}
                  me={me}
                  onAnswer={answer}
                  reload={reload}
                  openCall={() => void openCall()}
                  refreshMe={session.refresh}
                />
              </motion.div>
            ) : (
              <motion.p key="wait" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="py-2 text-center text-sm text-muted">
                {agentName} is typing…
              </motion.p>
            )}
          </AnimatePresence>
        </div>
      </div>
      {calling && firstQuestion && (
        <VideoCall
          key={callCount}
          agentName={agentName}
          firstQuestion={firstQuestion}
          onAnswer={(questionId, transcript) =>
            call<CallTurnResult>('/intro/call/answer', { method: 'POST', json: { question_id: questionId, transcript } })
          }
          onFinish={() => void endCall(true)}
          onCancel={() => void endCall(false)}
        />
      )}
    </div>
  )
}
