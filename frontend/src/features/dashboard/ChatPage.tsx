'use client'

import { ArrowUp, BookOpen, Lightbulb, PenLine, Wand2 } from 'lucide-react'
import Link from 'next/link'
import { motion } from 'motion/react'
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Conversation, type LiveTurn } from '@/components/Conversation'
import { useReveal } from '@/hooks/use-reveal'
import { Notice } from '@/components/site'
import { AppError, call, streamChat, type ChatEvent, type Message, type WorkStep } from '@/lib/api'
import { isSendKey } from '@/lib/keys'
import { useSession } from '@/lib/session-context'

const QUICK = [
  { icon: Lightbulb, label: 'How would you handle…', prefill: 'If someone messages you saying ' },
  { icon: PenLine, label: 'Draft a reply', prefill: 'Help me reply to this message:\n\n' },
  { icon: Wand2, label: 'Remember something', prefill: 'Remember that ' },
  { icon: BookOpen, label: 'What do you know about me?', send: 'What do you know about me so far?' },
]

export function ChatPage() {
  const { me } = useSession()
  const [messages, setMessages] = useState<Message[]>([])
  const [pending, setPending] = useState(false)
  const [live, setLive] = useState<LiveTurn | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  const [loaded, setLoaded] = useState(false)
  const refreshed = useRef(false)
  const localCount = useRef(0)
  const refreshedWhileSending = useRef(false)
  const unmounted = useRef(false)
  const abort = useRef<AbortController | null>(null)

  // Leaving the page ends the stream (the turn carries on server-side); coming back reads the saved reply.
  useEffect(() => {
    unmounted.current = false
    return () => {
      unmounted.current = true
      abort.current?.abort()
    }
  }, [])

  useEffect(() => {
    call<Message[]>('/messages')
      .then((history) => {
        // Something sent before the history arrived stays, after it; a newer copy wins.
        if (!refreshed.current) setMessages((now) => [...history, ...now.filter((m) => m.id.startsWith('local-'))])
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoaded(true))
  }, [])

  // The last saved message is the owner's and no reply is streaming: the reply is still being written
  // (they left mid-turn, or the stream was cut). Check again every few seconds, for a few minutes.
  const waiting = loaded && !pending && messages.length > 0 && messages[messages.length - 1].from === 'you'
  useEffect(() => {
    if (!waiting) return
    let tries = 0
    const t = window.setInterval(() => {
      if (document.visibilityState !== 'visible') return
      if (++tries > 60) return window.clearInterval(t)
      call<Message[]>('/messages')
        .then((fresh) => {
          if (!unmounted.current && !refreshedWhileSending.current) setMessages(fresh)
        })
        .catch(() => {})
    }, 3000)
    return () => window.clearInterval(t)
  }, [waiting])

  // Reveal nothing until the history is in, so only new replies get the typing animation.
  const { visible, typing } = useReveal(loaded ? messages : [], pending && !live, false, true)

  // Keep the newest message in view.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [visible.length, typing, live])

  useEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`
  }, [draft])

  async function send(text: string) {
    const t = text.trim()
    if (!t || pending) return
    localCount.current += 1
    const localId = `local-${localCount.current}`
    setDraft('')
    setError(null)
    setMessages((m) => [...m, { id: localId, from: 'you', text: t, at: new Date().toISOString() }])
    setPending(true)
    setLive({ steps: [], reasoning: '', text: '', status: 'Thinking' })
    let started = false // the server has the message, so it's saved whatever happens next
    let mode = ''
    let failure: string | null = null
    let finished = false // a 'done' or 'error' event arrived: the turn really ended
    const controller = new AbortController()
    abort.current = controller
    refreshedWhileSending.current = true
    try {
      await streamChat(
        t,
        (e) => {
          started = true
          if (e.event === 'done') {
            mode = e.data.mode
            finished = true
          } else if (e.event === 'error') {
            failure = e.data.message
            finished = true
          } else setLive((now) => (now ? apply(now, e) : now))
        },
        controller.signal,
      )
      if (unmounted.current) return
      // The stream closed without saying it was done (a proxy idle timeout, a restart): same as a dropped connection.
      if (started && !finished) throw new Error('stream ended early')
      const fresh = await call<Message[]>('/messages')
      refreshed.current = true
      setMessages(fresh)
      if (failure) setError(failure)
      // Paused: the message is kept, but say why nothing came back instead of leaving silence.
      else if (mode === 'frozen') setError(`${name} is paused, so she didn't reply. She'll see this once she's resumed.`)
    } catch (e) {
      if (unmounted.current) return
      if (started) {
        // The connection dropped mid-reply; the turn carries on server-side, so show what's saved.
        call<Message[]>('/messages').then(setMessages).catch(() => {})
        setError("Lost the live connection. The reply will appear here when it's ready.")
      } else {
        // Not sent: take the bubble back and give you your words to try again.
        setMessages((m) => m.filter((x) => x.id !== localId))
        setDraft((d) => d || t)
        setError(e instanceof AppError ? e.message : String(e))
      }
    } finally {
      refreshedWhileSending.current = false
      if (!unmounted.current) {
        setLive(null)
        setPending(false)
      }
    }
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (isSendKey(e)) {
      e.preventDefault()
      void send(draft)
    }
  }

  if (!me) return null
  const name = me.agent.name
  const first = me.person.name.split(' ')[0]

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-line/70 px-4 pt-5 pb-4 sm:px-8">
        <h1 className="text-2xl font-medium text-ink tight sm:text-3xl">Hello, {first}. What shall we work on?</h1>
        <div className="scrollbar-none mt-4 flex gap-2 overflow-x-auto">
          {QUICK.map(({ icon: Icon, label, prefill, send: direct }) => (
            <button
              key={label}
              type="button"
              onClick={() => {
                if (direct) void send(direct)
                else {
                  setDraft(prefill ?? '')
                  window.setTimeout(() => inputRef.current?.focus(), 0)
                }
              }}
              className="flex shrink-0 items-center gap-2 rounded-xl border border-line bg-white px-3.5 py-2 text-sm text-ink hover:border-lilac-200 hover:bg-lilac-50"
            >
              <Icon className="size-4 text-violet-600" aria-hidden />
              {label}
            </button>
          ))}
        </div>
      </div>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-8">
        <div className="mx-auto max-w-3xl">
          {me.brain === 'offline' && (
            <div className="mb-4">
              <Notice tone="warn">
                {name}&apos;s AI model isn&apos;t connected yet, so replies are placeholders.{' '}
                <Link href="/dashboard/models" className="font-medium underline">
                  Choose an AI model
                </Link>{' '}
                to switch her on.
              </Notice>
            </div>
          )}
          {me.paused && (
            <div className="mb-4">
              <Notice tone="warn">{name} is paused from the operator console. She won&apos;t reply until she&apos;s resumed.</Notice>
            </div>
          )}
          <Conversation messages={visible} typing={typing} hue="indigo" agentName={name} live={live} />
        </div>
      </div>

      <div className="px-4 pb-4 sm:px-8">
        <div className="mx-auto max-w-3xl">
          {error && (
            <div className="mb-2">
              <Notice tone="error">{error}</Notice>
            </div>
          )}
          <div className="flex items-end gap-2 rounded-2xl border border-line bg-white p-1.5 pl-4 shadow-[0_10px_30px_-24px_rgba(27,22,56,0.6)] focus-within:border-violet-400 focus-within:ring-2 focus-within:ring-violet-400/30">
            <textarea
              ref={inputRef}
              rows={1}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKey}
              placeholder={`Message ${name}`}
              aria-label={`Message ${name}`}
              className="max-h-[220px] flex-1 resize-none bg-transparent py-2.5 text-ink outline-none placeholder:text-muted"
            />
            <motion.button
              whileTap={{ scale: 0.9 }}
              onClick={() => void send(draft)}
              disabled={!draft.trim() || pending}
              className="grid size-10 shrink-0 place-items-center rounded-xl bg-violet-600 text-white hover:bg-violet-700 disabled:bg-lilac-200"
              aria-label="Send"
            >
              <ArrowUp className="size-5" />
            </motion.button>
          </div>
          <p className="mt-2 text-center text-xs text-muted">Enter to send, Shift and Enter for a new line.</p>
        </div>
      </div>
    </div>
  )
}

// Fold one streamed event into the reply being shown.
function apply(turn: LiveTurn, e: ChatEvent): LiveTurn {
  switch (e.event) {
    case 'status':
      return { ...turn, status: e.data.text }
    case 'reasoning':
      return { ...turn, reasoning: turn.reasoning + e.data.delta }
    case 'text':
      return { ...turn, text: turn.text + e.data.delta, status: 'Writing' }
    case 'narration': {
      // What she wrote before using a tool was her saying what she's doing, not the answer.
      const say: WorkStep = { kind: 'say', label: e.data.text }
      return { ...turn, text: '', steps: [...turn.steps, say] }
    }
    case 'retract':
      // The draft failed the source check; the rewrite streams in its place.
      return { ...turn, text: '' }
    case 'tool': {
      const step: WorkStep = { kind: 'tool', ...e.data }
      const at = turn.steps.findIndex((s) => s.kind === 'tool' && s.id === e.data.id)
      const steps = at >= 0 ? turn.steps.map((s, i) => (i === at ? step : s)) : [...turn.steps, step]
      return { ...turn, steps, status: e.data.state === 'running' ? 'Working' : 'Thinking' }
    }
    default:
      return turn
  }
}
