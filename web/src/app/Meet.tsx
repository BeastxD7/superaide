import {
  ArrowUp,
  BookOpen,
  Briefcase,
  Check,
  CircleCheck,
  ExternalLink,
  Lightbulb,
  Lock,
  Megaphone,
  MessageCircle,
  Send,
  ShieldCheck,
  Sparkles,
  UserPlus,
  Users,
  X,
} from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { AppError, call, type Me, type Message, type StepInput, type StepView } from './api'
import type { Session } from './AppRoot'
import { Conversation, useReveal } from './Conversation'
import { Orb } from './Orb'
import { Button, Notice } from './ui'

const GOAL_ICONS: Record<string, typeof Users> = {
  'Grow my audience': Users,
  'Win clients': Briefcase,
  'Hire great people': UserPlus,
  'Build my personal brand': Sparkles,
  'Share what I learn': Lightbulb,
  'Promote a product': Megaphone,
}
const RULE_ICONS = [Sparkles, Lock, CircleCheck, BookOpen, ShieldCheck]

export function Meet({ session }: { session: Session }) {
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
      window.location.hash = '/'
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
          <a href="#/" className="grid size-9 place-items-center rounded-full text-muted hover:bg-white hover:text-ink" aria-label="Leave for now" title="Leave for now. Ava will pick up where you left off.">
            <X className="size-4" />
          </a>
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
                key={view.step + (view.input.kind === 'channels' ? String(view.input.invite_url) : '')}
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 8 }}
                transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
              >
                <StepControls input={view.input} done={view.done} agentName={agentName} onAnswer={answer} me={me} refreshMe={session.refresh} />
              </motion.div>
            ) : (
              <motion.p key="wait" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="py-2 text-center text-sm text-muted">
                {agentName} is typing…
              </motion.p>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  )
}

type Answer = (answer: Record<string, unknown>, said: string | null) => Promise<void>

function StepControls({
  input,
  done,
  agentName,
  onAnswer,
  me,
  refreshMe,
}: {
  input: StepInput
  done: boolean
  agentName: string
  onAnswer: Answer
  me: Me
  refreshMe: () => Promise<Me | null>
}) {
  if (done || input.kind === 'none')
    return (
      <Button tone="violet" icon className="w-full justify-between text-base" onClick={() => (window.location.hash = '/workspace')}>
        Start working with {agentName}
      </Button>
    )

  switch (input.kind) {
    case 'chips':
      return (
        <div className="flex flex-wrap justify-end gap-2">
          {input.options.map((o, i) => (
            <Chip key={o} primary={i === 0} onClick={() => void onAnswer({ choice: o }, o)}>
              {o}
            </Chip>
          ))}
        </div>
      )
    case 'text':
      return <Composer placeholder={input.placeholder} onSend={(t) => onAnswer({ text: t }, t)} autoFocus />
    case 'multi':
      return <GoalPicker input={input} onAnswer={onAnswer} />
    case 'voice':
      return <VoicePicker input={input} onAnswer={onAnswer} />
    case 'rules':
      return (
        <div>
          <ul className="space-y-2">
            {input.items.map((r, i) => {
              const Icon = RULE_ICONS[i % RULE_ICONS.length]
              return (
                <motion.li
                  key={r}
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: i * 0.08 }}
                  className="flex items-start gap-3 rounded-2xl bg-white px-4 py-3 text-sm text-ink"
                >
                  <span className="grid size-8 shrink-0 place-items-center rounded-full bg-lilac-100 text-violet-600">
                    <Icon className="size-4" />
                  </span>
                  <span className="pt-1.5">{r}</span>
                </motion.li>
              )
            })}
          </ul>
          <Button tone="violet" className="mt-4 w-full py-3.5 text-base" onClick={() => void onAnswer({}, 'Sounds good')}>
            {input.submit}
          </Button>
        </div>
      )
    case 'channels':
      return <ChannelPicker input={input} agentName={agentName} onAnswer={onAnswer} me={me} refreshMe={refreshMe} />
    case 'topic':
      return (
        <div>
          <div className="grid gap-2">
            {input.options.map((o) => (
              <motion.button
                key={o}
                whileTap={{ scale: 0.98 }}
                onClick={() => void onAnswer({ topic: o }, `Please draft my first LinkedIn post about: ${o}`)}
                className="flex items-center gap-3 rounded-2xl border border-transparent bg-white px-4 py-3 text-left text-ink hover:border-violet-400"
              >
                <span className="grid size-8 shrink-0 place-items-center rounded-full bg-lilac-100 text-violet-600">
                  <Lightbulb className="size-4" />
                </span>
                {o}
              </motion.button>
            ))}
          </div>
          <div className="mt-3">
            <Composer placeholder={input.placeholder} onSend={(t) => onAnswer({ topic: t }, `Please draft my first LinkedIn post about: ${t}`)} />
          </div>
          <button onClick={() => void onAnswer({ topic: '' }, 'Not now')} className="mt-2 w-full rounded-full py-2 text-sm text-muted hover:text-ink">
            {input.skip}
          </button>
        </div>
      )
  }
}

function Chip({ children, onClick, primary = false, selected }: { children: ReactNode; onClick: () => void; primary?: boolean; selected?: boolean }) {
  return (
    <motion.button
      whileTap={{ scale: 0.95 }}
      onClick={onClick}
      aria-pressed={selected}
      className={`inline-flex items-center gap-1.5 rounded-full px-4 py-2.5 text-sm font-medium transition-colors ${
        selected
          ? 'bg-violet-600 text-white'
          : primary
            ? 'bg-gradient-to-r from-violet-500 to-violet-600 text-white'
            : 'bg-white text-ink hover:bg-lilac-100'
      }`}
    >
      {selected && <Check className="size-3.5" />}
      {children}
    </motion.button>
  )
}

function Composer({ placeholder, onSend, autoFocus = false }: { placeholder: string; onSend: (text: string) => Promise<void>; autoFocus?: boolean }) {
  const [text, setText] = useState('')
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [text])
  function send() {
    const t = text.trim()
    if (!t) return
    setText('')
    void onSend(t)
  }
  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }
  return (
    <div className="flex items-end gap-2 rounded-[22px] bg-white p-1.5 pl-4 shadow-[0_8px_24px_-20px_rgba(15,42,35,0.7)] focus-within:ring-2 focus-within:ring-violet-400/50">
      <textarea
        ref={ref}
        rows={1}
        autoFocus={autoFocus}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKey}
        placeholder={placeholder}
        aria-label={placeholder}
        className="max-h-[200px] flex-1 resize-none bg-transparent py-2.5 text-ink outline-none placeholder:text-muted/70"
      />
      <motion.button
        whileTap={{ scale: 0.9 }}
        onClick={send}
        disabled={!text.trim()}
        className="grid size-10 shrink-0 place-items-center rounded-full bg-gradient-to-br from-violet-500 to-violet-600 text-white disabled:from-lilac-200 disabled:to-lilac-200"
        aria-label="Send"
      >
        <ArrowUp className="size-5" />
      </motion.button>
    </div>
  )
}

function GoalPicker({ input, onAnswer }: { input: Extract<StepInput, { kind: 'multi' }>; onAnswer: Answer }) {
  const [picked, setPicked] = useState<string[]>([])
  const [other, setOther] = useState('')
  const toggle = (g: string) => setPicked((p) => (p.includes(g) ? p.filter((x) => x !== g) : [...p, g]))
  const said = [...picked, ...(other.trim() ? [other.trim()] : [])]
  return (
    <div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {input.options.map((g) => {
          const Icon = GOAL_ICONS[g] ?? Sparkles
          const on = picked.includes(g)
          return (
            <motion.button
              key={g}
              whileTap={{ scale: 0.96 }}
              onClick={() => toggle(g)}
              aria-pressed={on}
              className={`relative flex flex-col items-start gap-2 rounded-2xl border-2 p-3 text-left text-sm font-medium transition-colors ${
                on ? 'border-violet-500 bg-lilac-50 text-ink' : 'border-transparent bg-white text-ink hover:border-lilac-200'
              }`}
            >
              <span className={`grid size-8 place-items-center rounded-full ${on ? 'bg-violet-500 text-white' : 'bg-lilac-100 text-violet-600'}`}>
                {on ? <Check className="size-4" /> : <Icon className="size-4" />}
              </span>
              {g}
            </motion.button>
          )
        })}
      </div>
      {input.other && (
        <input
          value={other}
          onChange={(e) => setOther(e.target.value)}
          placeholder="Something else? Type it here"
          className="mt-2 w-full rounded-2xl bg-white px-4 py-3 text-sm text-ink outline-none placeholder:text-muted/70 focus:ring-2 focus:ring-violet-400/50"
        />
      )}
      <Button tone="violet" className="mt-3 w-full py-3.5 text-base" disabled={said.length === 0} onClick={() => void onAnswer({ goals: picked, other }, said.join(', '))}>
        {input.submit}
      </Button>
    </div>
  )
}

function VoicePicker({ input, onAnswer }: { input: Extract<StepInput, { kind: 'voice' }>; onAnswer: Answer }) {
  const [sample, setSample] = useState('')
  const [tones, setTones] = useState<string[]>([])
  const toggle = (t: string) => setTones((p) => (p.includes(t) ? p.filter((x) => x !== t) : [...p, t]))
  const said = [tones.length ? tones.join(', ') : 'No tone picked', sample.trim() ? `Here's a post I like:\n\n${sample.trim()}` : ''].filter(Boolean).join('. ')
  return (
    <div>
      <textarea
        value={sample}
        onChange={(e) => setSample(e.target.value)}
        rows={4}
        placeholder={input.placeholder}
        className="w-full resize-none rounded-2xl bg-white px-4 py-3 text-sm text-ink outline-none placeholder:text-muted/70 focus:ring-2 focus:ring-violet-400/50"
      />
      <p className="mt-3 mb-2 text-sm font-medium text-ink">How do you like to sound?</p>
      <div className="flex flex-wrap gap-2">
        {input.options.map((t) => (
          <Chip key={t} selected={tones.includes(t)} onClick={() => toggle(t)}>
            {t}
          </Chip>
        ))}
      </div>
      <Button tone="violet" className="mt-4 w-full py-3.5 text-base" onClick={() => void onAnswer({ sample, tones }, said)}>
        {input.submit}
      </Button>
    </div>
  )
}

function ChannelPicker({
  input,
  agentName,
  onAnswer,
  me,
  refreshMe,
}: {
  input: Extract<StepInput, { kind: 'channels' }>
  agentName: string
  onAnswer: Answer
  me: Me
  refreshMe: () => Promise<Me | null>
}) {
  const [linked, setLinked] = useState(input.telegram_linked || me.channels.telegram)

  // After opening the Telegram link, watch for the phone to be linked.
  useEffect(() => {
    if (!input.invite_url || linked) return
    const t = window.setInterval(async () => {
      const m = await refreshMe()
      if (m?.channels.telegram) setLinked(true)
    }, 3000)
    return () => window.clearInterval(t)
  }, [input.invite_url, linked, refreshMe])

  if (input.invite_url || linked)
    return (
      <div className="space-y-3">
        {linked ? (
          <div className="flex items-center gap-3 rounded-2xl bg-white px-4 py-3 text-ink">
            <span className="grid size-8 place-items-center rounded-full bg-sky-100 text-ok">
              <Check className="size-4" />
            </span>
            Telegram is connected. {agentName} knows it's you there.
          </div>
        ) : (
          <a
            href={input.invite_url ?? '#'}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center justify-between gap-3 rounded-2xl bg-[#e8f4fd] px-4 py-3.5 font-medium text-[#1f6fa8] hover:bg-[#dcedfb]"
          >
            <span className="flex items-center gap-3">
              <Send className="size-5" /> Open Telegram and press Start
            </span>
            <ExternalLink className="size-4" />
          </a>
        )}
        {!linked && <p className="text-center text-xs text-muted">Waiting for you to press Start in Telegram…</p>}
        <Button tone="violet" className="w-full py-3.5 text-base" onClick={() => void onAnswer({ choice: linked ? 'telegram_done' : 'here' }, linked ? "I've connected Telegram" : 'Here in the app is fine')}>
          {linked ? 'Continue' : 'Skip for now'}
        </Button>
      </div>
    )

  return (
    <div className="space-y-2">
      <Row icon={MessageCircle} title="Here in the app" text="We'll talk right here. You can add Telegram later." onClick={() => void onAnswer({ choice: 'here' }, 'Here in the app is fine')} />
      <Row
        icon={Send}
        title="On Telegram too"
        text={input.telegram_available ? `Message ${agentName} from your phone. I'll give you a one-time link.` : 'Not set up on this computer yet. Add a Telegram bot token to .env to turn it on.'}
        disabled={!input.telegram_available}
        onClick={() => void onAnswer({ choice: 'telegram' }, null)}
      />
    </div>
  )
}

function Row({ icon: Icon, title, text, onClick, disabled = false }: { icon: typeof Send; title: string; text: string; onClick: () => void; disabled?: boolean }) {
  return (
    <motion.button
      whileTap={disabled ? undefined : { scale: 0.98 }}
      onClick={onClick}
      disabled={disabled}
      className="flex w-full items-center gap-3 rounded-2xl border-2 border-transparent bg-white px-4 py-3 text-left hover:border-violet-400 disabled:cursor-not-allowed disabled:opacity-55 disabled:hover:border-transparent"
    >
      <span className="grid size-10 shrink-0 place-items-center rounded-full bg-lilac-100 text-violet-600">
        <Icon className="size-5" />
      </span>
      <span>
        <span className="block font-medium text-ink">{title}</span>
        <span className="block text-sm text-muted">{text}</span>
      </span>
    </motion.button>
  )
}
