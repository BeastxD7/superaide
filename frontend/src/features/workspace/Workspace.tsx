'use client'

import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  ArrowUp,
  BookOpen,
  Brain,
  Lightbulb,
  LogOut,
  PenLine,
  Wand2,
} from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { AppError, call, type Memory, type Message } from '@/lib/api'
import type { Session } from '@/lib/session-context'
import { Connections } from '@/features/workspace/Connections'
import { Conversation, useReveal } from '@/components/Conversation'
import { Orb } from '@/components/Orb'
import { Notice, PRODUCT, Sparkle, STAGES } from '@/components/ui'

const QUICK = [
  { icon: Lightbulb, label: 'How would you handle…', prefill: 'If someone messages you saying ' },
  { icon: PenLine, label: 'Draft a reply', prefill: 'Help me reply to this message:\n\n' },
  { icon: Wand2, label: 'Remember something', prefill: 'Remember that ' },
  { icon: BookOpen, label: 'What do you know about me?', send: 'What do you know about me so far?' },
]

type Tab = 'chat' | 'notebook' | 'ava'

const CATEGORY_LABEL: Record<string, string> = {
  about: 'About you',
  work: 'Work',
  person: 'People',
  preference: 'Preference',
  urgency: 'Urgent',
  availability: 'Availability',
  sharing: 'Sharing',
  other: 'Note',
}
const SOURCE_LABEL: Record<string, string> = {
  chat: 'from chat',
  intro_chat: 'from your introduction',
  intro_call: 'from our video call',
  document: 'from a file you shared',
  link: 'from a link you shared',
  telegram: 'from Telegram',
  slack: 'from Slack',
  owner_edit: 'added by you',
}

export function Workspace({ session }: { session: Session }) {
  const router = useRouter()
  const me = session.me
  const [messages, setMessages] = useState<Message[]>([])
  const [notebook, setNotebook] = useState<Memory[]>([])
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [tab, setTab] = useState<Tab>('chat')
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const loadNotebook = useCallback(() => call<{ memories: Memory[] }>('/notebook').then((r) => setNotebook(r.memories)), [])

  useEffect(() => {
    if (!me) {
      router.push(session.account ? '/' : `/login?next=${encodeURIComponent('/workspace')}`)
      return
    }
    if (!me.onboarding.done) {
      router.push('/meet')
      return
    }
    call<Message[]>('/messages').then(setMessages).catch((e: Error) => setError(e.message))
    void loadNotebook()
  }, [me?.person.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const { visible, typing } = useReveal(messages, pending, false)

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
    setTab('chat')
    setMessages((m) => [...m, { id: `local-${Date.now()}`, from: 'you', text: t, at: new Date().toISOString() }])
    setPending(true)
    try {
      await call('/chat', { method: 'POST', json: { text: t } })
      setMessages(await call<Message[]>('/messages'))
      void loadNotebook()
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
  const agentName = me.agent.name
  const first = me.person.name.split(' ')[0]

  return (
    <div className="mesh flex h-dvh flex-col">
      {/* Top bar */}
      <header className="px-3 pt-3 sm:px-4">
        <div className="glass mx-auto flex max-w-7xl items-center gap-3 rounded-full border border-white/70 py-1.5 pr-1.5 pl-4 shadow-[0_8px_30px_-20px_rgba(15,42,35,0.5)]">
          <Link href="/" className="flex items-center gap-2 font-semibold text-ink tight">
            <Sparkle className="size-5 text-violet-600" />
            <span className="hidden sm:inline">{PRODUCT}</span>
          </Link>
          <div className="mx-auto flex items-center gap-1 rounded-full bg-mist p-1 text-sm lg:hidden">
            {(['chat', 'notebook', 'ava'] as Tab[]).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`rounded-full px-3 py-1.5 ${tab === t ? 'bg-white font-medium text-ink shadow-sm' : 'text-muted'}`}
              >
                {t === 'chat' ? 'Chat' : t === 'notebook' ? 'Notebook' : agentName}
              </button>
            ))}
          </div>
          <span className="ml-auto hidden items-center gap-2 text-sm text-muted lg:flex">
            <span className={`size-2 rounded-full ${me.paused ? 'bg-warn' : me.brain === 'claude' ? 'bg-ok' : 'bg-muted'}`} />
            {me.paused ? `${agentName} is paused` : me.brain === 'claude' ? `${agentName} is online` : `${agentName} is in offline mode`}
          </span>
          <button
            onClick={() => void session.logOut()}
            className="grid size-10 place-items-center rounded-full text-muted hover:bg-white hover:text-ink"
            aria-label="Log out"
            title="Log out"
          >
            <LogOut className="size-4" />
          </button>
        </div>
      </header>

      <div className="mx-auto grid min-h-0 w-full max-w-7xl flex-1 gap-4 px-3 py-3 sm:px-4 lg:grid-cols-[1fr_360px]">
        {/* Chat */}
        <section className={`min-h-0 flex-col overflow-hidden rounded-[28px] border border-white/70 bg-white/55 ${tab === 'chat' ? 'flex' : 'hidden lg:flex'}`}>
          <div className="border-b border-white/80 px-5 pt-5 pb-4 sm:px-7">
            <h1 className="text-2xl font-medium tight sm:text-3xl">
              Hello, {first}. What shall we work on?
            </h1>
            <div className="scrollbar-none mt-4 flex gap-2 overflow-x-auto">
              {QUICK.map(({ icon: Icon, label, prefill, send: direct }) => (
                <motion.button
                  key={label}
                  whileTap={{ scale: 0.96 }}
                  onClick={() => {
                    if (direct) void send(direct)
                    else {
                      setDraft(prefill ?? '')
                      window.setTimeout(() => inputRef.current?.focus(), 0)
                    }
                  }}
                  className="flex shrink-0 items-center gap-2 rounded-2xl bg-white px-3.5 py-2.5 text-sm text-ink shadow-[0_6px_18px_-14px_rgba(15,42,35,0.6)] hover:bg-lilac-50"
                >
                  <Icon className="size-4 text-violet-600" />
                  {label}
                </motion.button>
              ))}
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-7">
            {me.brain === 'offline' && (
              <div className="mb-4">
                <Notice tone="warn">
                  {agentName}'s Claude brain isn't connected yet, so replies are placeholders. Connect Claude under
                  Connections (on the right) to switch it on.
                </Notice>
              </div>
            )}
            {me.paused && (
              <div className="mb-4">
                <Notice tone="warn">{agentName} is paused from the operator console. It won't reply until it's resumed.</Notice>
              </div>
            )}
            <Conversation messages={visible} typing={typing} hue="indigo" agentName={agentName} />
          </div>

          <div className="p-3 sm:p-4">
            {error && (
              <div className="mb-2">
                <Notice tone="error">{error}</Notice>
              </div>
            )}
            <div className="flex items-end gap-2 rounded-[24px] bg-white p-1.5 pl-5 shadow-[0_10px_30px_-22px_rgba(15,42,35,0.8)] focus-within:ring-2 focus-within:ring-violet-400/50">
              <textarea
                ref={inputRef}
                rows={1}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={onKey}
                placeholder={`Message ${agentName}`}
                aria-label={`Message ${agentName}`}
                className="max-h-[220px] flex-1 resize-none bg-transparent py-3 text-ink outline-none placeholder:text-muted/70"
              />
              <motion.button
                whileTap={{ scale: 0.9 }}
                onClick={() => void send(draft)}
                disabled={!draft.trim() || pending}
                className="grid size-11 shrink-0 place-items-center rounded-full bg-gradient-to-br from-violet-500 to-violet-600 text-white disabled:from-lilac-200 disabled:to-lilac-200"
                aria-label="Send"
              >
                <ArrowUp className="size-5" />
              </motion.button>
            </div>
          </div>
        </section>

        {/* Right rail: Ava, the notebook, channels */}
        <aside className={`min-h-0 flex-col gap-4 overflow-y-auto ${tab === 'chat' ? 'hidden lg:flex' : 'flex'}`}>
          <div className={tab === 'notebook' ? 'hidden lg:block' : ''}>
            <AgentCard session={session} />
          </div>
          <div className={tab === 'ava' ? 'hidden lg:block' : ''}>
            <Notebook memories={notebook} agentName={agentName} onChange={loadNotebook} />
          </div>
          <div className={tab === 'notebook' ? 'hidden lg:block' : ''}>
            <div className="rounded-[28px] bg-sky-100 p-5">
              <h2 className="mb-3 px-1 text-lg font-medium tight">Connections</h2>
              <Connections agentName={me.agent.name} me={me} refreshMe={session.refresh} />
            </div>
          </div>
        </aside>
      </div>
    </div>
  )
}

function AgentCard({ session }: { session: Session }) {
  const me = session.me!
  const stage = STAGES.findIndex((s) => s.name === me.agent.stage)
  return (
    <div className="relative overflow-hidden rounded-[28px] bg-lilac-100 p-6">
      <div className="flex items-center gap-4">
        <Orb hue="indigo" size={72} track />
        <div>
          <p className="text-2xl font-medium text-ink tight">{me.agent.name}</p>
          <p className="text-sm text-violet-700">{me.agent.role}</p>
        </div>
      </div>
      <div className="mt-6">
        <div className="flex justify-between text-xs text-muted">
          {STAGES.map((s, i) => (
            <span key={s.name} className={i === stage ? 'font-medium text-violet-700' : ''}>
              {s.name}
            </span>
          ))}
        </div>
        <div className="mt-2 grid grid-cols-4 gap-1.5">
          {STAGES.map((s, i) => (
            <div key={s.name} className="h-1.5 overflow-hidden rounded-full bg-white">
              {i <= stage && (
                <motion.div
                  className="h-full rounded-full bg-gradient-to-r from-violet-400 to-violet-600"
                  initial={{ width: 0 }}
                  animate={{ width: i < stage ? '100%' : '30%' }}
                  transition={{ delay: 0.3, duration: 0.8 }}
                />
              )}
            </div>
          ))}
        </div>
        <p className="mt-3 text-sm text-body">{STAGES[Math.max(stage, 0)].does}</p>
      </div>
      <div className="mt-5 flex items-center gap-2 rounded-2xl bg-white/70 px-3 py-2 text-sm text-body">
        <Brain className="size-4 text-violet-600" />
        {me.brain === 'claude' ? 'Thinking with Claude' : 'Offline mode: placeholder replies'}
      </div>
    </div>
  )
}

function Notebook({ memories, agentName, onChange }: { memories: Memory[]; agentName: string; onChange: () => Promise<void> }) {
  const [confirm, setConfirm] = useState<string | null>(null)
  async function forget(id: string) {
    await call(`/notebook/${id}/forget`, { method: 'POST' })
    setConfirm(null)
    await onChange()
  }
  return (
    <div className="rounded-[28px] bg-white/80 p-6">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="flex items-center gap-2 text-lg font-medium tight">
          <BookOpen className="size-5 text-violet-600" /> {agentName}'s notebook
        </h2>
        <span className="text-sm text-muted">{memories.length}</span>
      </div>
      <p className="mt-1 text-sm text-muted">Everything {agentName} remembers about you. Only you can see it.</p>
      <ul className="mt-4 space-y-2">
        <AnimatePresence initial={false}>
          {memories.map((m) => (
            <motion.li
              key={m.id}
              layout
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, x: 30, transition: { duration: 0.2 } }}
              className="rounded-2xl bg-mist px-4 py-3 text-sm text-ink"
            >
              <p className="mb-1.5 flex flex-wrap items-center gap-1.5 text-[11px] font-medium text-muted">
                <span className="rounded-full bg-white px-2 py-0.5 text-violet-700">{CATEGORY_LABEL[m.category] ?? 'Note'}</span>
                <span>{m.document ? `from ${m.document}` : (SOURCE_LABEL[m.source] ?? 'from chat')}</span>
                {m.shareable && <span className="rounded-full bg-sky-100 px-2 py-0.5 text-ok">Shareable</span>}
              </p>
              <p className="line-clamp-4 whitespace-pre-wrap">{m.text}</p>
              {confirm === m.id ? (
                <div className="mt-2 flex items-center gap-2 text-xs">
                  <span className="text-muted">{agentName} won't use this again.</span>
                  <button onClick={() => void forget(m.id)} className="rounded-full bg-danger px-3 py-1 font-medium text-white">
                    Forget
                  </button>
                  <button onClick={() => setConfirm(null)} className="rounded-full px-2 py-1 text-muted hover:text-ink">
                    Keep
                  </button>
                </div>
              ) : (
                <button onClick={() => setConfirm(m.id)} className="mt-1.5 text-xs font-medium text-muted hover:text-danger">
                  Forget this
                </button>
              )}
            </motion.li>
          ))}
        </AnimatePresence>
        {memories.length === 0 && (
          <li className="rounded-2xl bg-mist px-4 py-3 text-sm text-muted">
            Nothing yet. Tell {agentName} "remember that…" and it will appear here.
          </li>
        )}
      </ul>
    </div>
  )
}
