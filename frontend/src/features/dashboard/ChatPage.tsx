'use client'

import { ArrowUp, BookOpen, Lightbulb, PenLine, Wand2 } from 'lucide-react'
import Link from 'next/link'
import { motion } from 'motion/react'
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Conversation, useReveal } from '@/components/Conversation'
import { Notice } from '@/components/site'
import { AppError, call, type Message } from '@/lib/api'
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
  const [error, setError] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    call<Message[]>('/messages')
      .then(setMessages)
      .catch((e: Error) => setError(e.message))
  }, [])

  const { visible, typing } = useReveal(messages, pending, false)

  // Keep the newest message in view.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [visible.length, typing])

  useEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`
  }, [draft])

  async function send(text: string) {
    const t = text.trim()
    if (!t || pending) return
    setDraft('')
    setError(null)
    setMessages((m) => [...m, { id: `local-${Date.now()}`, from: 'you', text: t, at: new Date().toISOString() }])
    setPending(true)
    try {
      await call('/chat', { method: 'POST', json: { text: t } })
      setMessages(await call<Message[]>('/messages'))
    } catch (e) {
      setError(e instanceof AppError ? e.message : String(e))
    } finally {
      setPending(false)
    }
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
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
                {name}&apos;s Claude brain isn&apos;t connected yet, so replies are placeholders.{' '}
                <Link href="/dashboard/connections" className="font-medium underline">
                  Connect Claude
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
          <Conversation messages={visible} typing={typing} hue="indigo" agentName={name} />
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
              className="max-h-[220px] flex-1 resize-none bg-transparent py-2.5 text-ink outline-none placeholder:text-muted/70"
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
