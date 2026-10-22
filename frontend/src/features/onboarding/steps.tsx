'use client'

import { useRouter } from 'next/navigation'
import { speechSupport } from '@/features/onboarding/speech'
import {
  ArrowUp,
  Bot,
  BookOpen,
  Check,
  CircleCheck,
  FileText,
  Globe,
  Link2,
  Lock,
  MessageCircle,
  Moon,
  Paperclip,
  Phone,
  Plus,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  Video,
  X,
} from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useRef, useState, type DragEvent, type KeyboardEvent, type ReactNode } from 'react'
import { AppError, call, upload, type IngestResult, type Me, type SharedDoc, type StepInput } from '@/lib/api'
import { Connections } from '@/features/workspace/Connections'
import { BrandIcon } from '@/components/BrandIcon'
import { Button, Notice } from '@/components/site'

// The answer panel for each step of the introduction. Each control calls
// onAnswer(payload, whatYouSaid); intro actions (files, links, the call) talk to
// their own endpoints and then ask the page to reload the conversation.

export type Answer = (answer: Record<string, unknown>, said: string | null) => Promise<void>

type Props = {
  input: StepInput
  done: boolean
  agentName: string
  me: Me
  onAnswer: Answer
  reload: () => Promise<void>
  openCall: () => void
  refreshMe: () => Promise<Me | null>
}

export function StepControls({ input, done, agentName, me, onAnswer, reload, openCall, refreshMe }: Props) {
  const router = useRouter()
  if (done || input.kind === 'none')
    return (
      <Button tone="violet" icon className="w-full justify-between text-base" onClick={() => router.push('/dashboard')}>
        Start working with {agentName}
      </Button>
    )
  switch (input.kind) {
    case 'brain':
      return <BrainPicker input={input} agentName={agentName} onAnswer={onAnswer} />
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
    case 'intro':
      return <IntroPicker input={input} agentName={agentName} onAnswer={onAnswer} reload={reload} openCall={openCall} />
    case 'people':
      return <PeoplePicker input={input} onAnswer={onAnswer} />
    case 'urgent':
      return <UrgentPicker input={input} onAnswer={onAnswer} />
    case 'reach':
      return <ReachPicker input={input} onAnswer={onAnswer} />
    case 'share':
      return <SharePicker input={input} onAnswer={onAnswer} />
    case 'rules':
      return <RulesList input={input} onAnswer={onAnswer} />
    case 'connect':
      return <ConnectPicker input={input} agentName={agentName} me={me} onAnswer={onAnswer} refreshMe={refreshMe} />
  }
}

// --- shared bits ------------------------------------------------------------------

export function Chip({
  children,
  onClick,
  primary = false,
  selected,
}: {
  children: ReactNode
  onClick: () => void
  primary?: boolean
  selected?: boolean
}) {
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

export function Composer({
  placeholder,
  onSend,
  autoFocus = false,
}: {
  placeholder: string
  onSend: (text: string) => Promise<void>
  autoFocus?: boolean
}) {
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
    <div className="flex items-end gap-2 rounded-[22px] bg-white p-1.5 pl-4 shadow-[0_8px_24px_-20px_rgba(27,22,56,0.7)] focus-within:ring-2 focus-within:ring-violet-400/50">
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

function Toggle({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={() => onChange(!on)}
      className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${on ? 'bg-violet-600' : 'bg-lilac-200'}`}
    >
      <motion.span
        className="absolute top-0.5 left-0.5 size-5 rounded-full bg-white shadow"
        animate={{ x: on ? 20 : 0 }}
        transition={{ type: 'spring', stiffness: 500, damping: 30 }}
      />
    </button>
  )
}

function Submit({ children, onClick, disabled }: { children: ReactNode; onClick: () => void; disabled?: boolean }) {
  return (
    <Button tone="violet" className="mt-4 w-full py-3.5 text-base" disabled={disabled} onClick={onClick}>
      {children}
    </Button>
  )
}

// --- Choose Ava's brain ---------------------------------------------------------------

function BrainPicker({
  input,
  agentName,
  onAnswer,
}: {
  input: Extract<StepInput, { kind: 'brain' }>
  agentName: string
  onAnswer: Answer
}) {
  const router = useRouter()
  const [busy, setBusy] = useState(false)
  const [showKey, setShowKey] = useState(false)
  const [key, setKey] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const credits = input.credits
  const usd = (n: number | null) => (n === null ? null : `US$${n.toFixed(2)}`)
  const allowance = credits
    ? [
        usd(credits.allowance.daily_usd) && `${usd(credits.allowance.daily_usd)} a day`,
        usd(credits.allowance.monthly_usd) && `${usd(credits.allowance.monthly_usd)} a month`,
      ]
        .filter(Boolean)
        .join(' and ')
    : ''

  async function answer(choice: 'credits' | 'own' | 'skip', said: string) {
    setBusy(true)
    setProblem(null)
    try {
      await onAnswer({ choice }, said)
    } catch (e) {
      setProblem(e instanceof AppError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function saveKey() {
    setBusy(true)
    setProblem(null)
    try {
      await call('/providers/anthropic', { method: 'POST', json: { api_key: key.trim() } })
      setKey('')
      await onAnswer({ choice: 'own' }, 'I connected my own key')
    } catch (e) {
      setProblem(e instanceof AppError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (input.ready)
    return (
      <div>
        <p className="mb-2 text-sm text-body">{agentName} already has a brain to think with.</p>
        <Submit onClick={() => void answer('own', "That's set up")} disabled={busy}>
          Continue
        </Submit>
      </div>
    )

  return (
    <div className="space-y-3" role="group" aria-label={`Choose ${agentName}'s brain`}>
      {credits && (
        <div className="rounded-2xl bg-white p-4">
          <p className="font-medium text-ink">Use Superaide AI credits</p>
          <p className="mt-1 text-sm text-muted">
            One click, no key needed{allowance ? `: up to ${allowance}` : ''}. Powered by {credits.model}.
          </p>
          <Button
            tone="violet"
            className="mt-3 w-full py-3"
            disabled={busy}
            onClick={() => void answer('credits', 'Use Superaide AI credits')}
          >
            Use Superaide AI credits
          </Button>
        </div>
      )}
      <div className="rounded-2xl bg-white p-4">
        <p className="font-medium text-ink">Connect my own key</p>
        <p className="mt-1 text-sm text-muted">Your own Claude key, kept encrypted and never shown again.</p>
        {showKey ? (
          <form
            className="mt-3 space-y-2"
            onSubmit={(e) => {
              e.preventDefault()
              if (key.trim()) void saveKey()
            }}
          >
            <input
              type="password"
              autoComplete="off"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="sk-ant-…"
              aria-label="Claude API key"
              className="w-full rounded-xl bg-mist px-3 py-2.5 text-sm text-ink outline-none"
            />
            <div className="flex flex-wrap gap-2">
              <Button tone="violet" className="py-2.5" disabled={busy || !key.trim()} onClick={() => void saveKey()}>
                Save key
              </Button>
              <button
                type="button"
                onClick={() => router.push(input.models_url)}
                className="rounded-full px-3 py-2 text-sm text-violet-700 underline"
              >
                Other providers (OpenAI, OpenRouter…)
              </button>
            </div>
          </form>
        ) : (
          <Button className="mt-3 w-full py-3" disabled={busy} onClick={() => setShowKey(true)}>
            Connect my own key
          </Button>
        )}
      </div>
      {problem && <Notice tone="error">{problem}</Notice>}
      <button
        onClick={() => void answer('skip', 'Skip for now')}
        disabled={busy}
        className="w-full rounded-full py-2 text-sm text-muted hover:text-ink disabled:opacity-40"
      >
        Skip for now. Until a brain is set up, {agentName} can only echo you; what you share is saved and read later.
      </button>
    </div>
  )
}

// --- About you: chat, files & links, or a video call ----------------------------------

function IntroPicker({
  input,
  agentName,
  onAnswer,
  reload,
  openCall,
}: {
  input: Extract<StepInput, { kind: 'intro' }>
  agentName: string
  onAnswer: Answer
  reload: () => Promise<void>
  openCall: () => void
}) {
  const [noSpeechService] = useState(() => speechSupport().noService)
  const [tab, setTab] = useState<'chat' | 'files' | 'call'>(input.documents.length ? 'files' : 'chat')
  const tabs = [
    { key: 'chat', label: 'Tell me here', short: 'Chat', icon: MessageCircle },
    { key: 'files', label: 'Files & links', short: 'Files', icon: Paperclip },
    { key: 'call', label: 'Video call', short: 'Call', icon: Video },
  ] as const
  return (
    <div>
      <div className="mb-3 grid grid-cols-3 gap-1.5 rounded-2xl bg-white/70 p-1.5" role="tablist" aria-label="How to introduce yourself">
        {tabs.map(({ key, label, short, icon: Icon }) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            aria-label={label}
            onClick={() => setTab(key)}
            className={`flex items-center justify-center gap-2 rounded-xl px-2 py-2.5 text-sm font-medium transition-colors ${
              tab === key ? 'bg-violet-600 text-white shadow-sm' : 'text-body hover:bg-white'
            }`}
          >
            {/* Phones get the short label, so no tab is cut off mid-word. */}
            <Icon className="size-4" /> <span className="truncate sm:hidden">{short}</span>
            <span className="hidden truncate sm:inline">{label}</span>
          </button>
        ))}
      </div>
      <AnimatePresence mode="wait">
        <motion.div key={tab} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.2 }}>
          {tab === 'chat' && <Composer placeholder={input.placeholder} onSend={(t) => onAnswer({ mode: 'chat', text: t }, t)} autoFocus />}
          {tab === 'files' && <FilesAndLinks documents={input.documents} reload={reload} onContinue={() => onAnswer({ mode: 'continue' }, "That's me for now")} />}
          {tab === 'call' && (
            <div className="rounded-2xl bg-white p-5 text-center">
              <div className="mx-auto grid size-12 place-items-center rounded-full bg-lilac-100 text-violet-600">
                <Video className="size-6" />
              </div>
              <p className="mt-3 font-medium text-ink">A quick video call</p>
              <p className="mt-1 text-sm text-muted">
                {input.call_questions} short questions, about five minutes. Talk, and your browser turns it into text.
                Nothing is recorded.
              </p>
              {noSpeechService && (
                <p className="mt-3 rounded-xl bg-mist px-3 py-2 text-left text-sm text-body">
                  Brave can't turn speech into text, so you'll type your answers while {agentName} talks. To answer out
                  loud, open this page in Chrome, Edge or Safari.
                </p>
              )}
              <Button tone="violet" className="mt-4 w-full py-3" onClick={openCall}>
                Start the call
              </Button>
              {input.facts > 0 && (
                <button onClick={() => void onAnswer({ mode: 'continue' }, "That's me for now")} className="mt-2 w-full rounded-full py-2 text-sm text-muted hover:text-ink">
                  I'm done introducing myself
                </button>
              )}
            </div>
          )}
        </motion.div>
      </AnimatePresence>
    </div>
  )
}

// "Read resume.pdf: 5 facts saved", or that it's kept until Ava has a brain.
function readStatus(r: IngestResult): string {
  if (r.status === 'read')
    return r.facts > 0
      ? `Read ${r.title}: ${r.facts} ${r.facts === 1 ? 'fact' : 'facts'} saved.`
      : `Read ${r.title}: nothing new about you in it.`
  return `${r.title} is saved. It will be read as soon as Ava's brain is set up.`
}

function FilesAndLinks({ documents, reload, onContinue }: { documents: SharedDoc[]; reload: () => Promise<void>; onContinue: () => void }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [url, setUrl] = useState('')
  const [drag, setDrag] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  async function sendFile(file: File) {
    setBusy(`Reading ${file.name}…`)
    setError(null)
    setNote(null)
    setDone(null)
    try {
      const r = await upload<IngestResult>('/intro/files', file)
      if (r.duplicate) setNote(r.message)
      else setDone(readStatus(r))
      if (r.warnings.length) setNote(r.warnings.join(' '))
      await reload()
    } catch (e) {
      setError(e instanceof AppError ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function sendLink() {
    const u = url.trim()
    if (!u) return
    setBusy(`Reading ${u}…`)
    setError(null)
    setNote(null)
    setDone(null)
    try {
      const r = await call<IngestResult>('/intro/links', { method: 'POST', json: { url: u.includes('://') ? u : `https://${u}` } })
      if (r.duplicate) setNote(r.message)
      else setDone(readStatus(r))
      setUrl('')
      await reload()
    } catch (e) {
      setError(e instanceof AppError ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function remove(doc: SharedDoc) {
    setBusy(`Removing ${doc.title}…`)
    try {
      await call(`/intro/documents/${doc.id}/remove`, { method: 'POST' })
      await reload()
    } finally {
      setBusy(null)
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault()
    setDrag(false)
    const f = e.dataTransfer.files?.[0]
    if (f) void sendFile(f)
  }

  return (
    <div className="space-y-3">
      <div
        onDragOver={(e) => {
          e.preventDefault()
          setDrag(true)
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={onDrop}
        className={`rounded-2xl border-2 border-dashed p-4 text-center transition-colors ${drag ? 'border-violet-500 bg-lilac-50' : 'border-lilac-200 bg-white'}`}
      >
        <Upload className="mx-auto size-5 text-violet-600" />
        <p className="mt-1.5 text-sm text-ink">
          Drop your resume or CV here, or{' '}
          <button className="font-medium text-violet-700 underline" onClick={() => fileRef.current?.click()}>
            choose a file
          </button>
        </p>
        <p className="mt-0.5 text-xs text-muted">PDF, Word or text, up to 10 MB. Tip: your LinkedIn profile can be saved as a PDF.</p>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.docx,.txt,.md,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/markdown"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) void sendFile(f)
            e.target.value = ''
          }}
        />
      </div>
      <div className="flex items-center gap-2 rounded-2xl bg-white p-1.5 pl-3 focus-within:ring-2 focus-within:ring-violet-400/50">
        <Link2 className="size-4 shrink-0 text-muted" />
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void sendLink()}
          placeholder="Your website, GitHub or a page about you"
          aria-label="Link to a page about you"
          className="min-w-0 flex-1 bg-transparent py-2 text-sm text-ink outline-none placeholder:text-muted/70"
        />
        <button onClick={() => void sendLink()} disabled={!url.trim() || Boolean(busy)} className="rounded-full bg-ink px-4 py-2 text-sm font-medium text-white disabled:opacity-40">
          Add
        </button>
      </div>
      {busy && <p className="text-center text-sm text-muted" aria-live="polite">{busy}</p>}
      {error && <Notice tone="error">{error}</Notice>}
      {note && <Notice tone="warn">{note}</Notice>}
      {done && !busy && <Notice tone="info">{done}</Notice>}
      {documents.length > 0 && (
        <ul className="space-y-1.5">
          {documents.map((d) => {
            const Icon = d.kind === 'web' ? Globe : FileText
            return (
              <li key={d.id} className="flex items-center gap-3 rounded-xl bg-white px-3 py-2 text-sm">
                {d.kind === 'github' ? (
                  <BrandIcon brand="github" size={16} label="GitHub" className="text-ink" />
                ) : (
                  <Icon className="size-4 shrink-0 text-violet-600" />
                )}
                <span className="min-w-0 flex-1 truncate text-ink" title={d.source}>
                  {d.title}
                </span>
                <span className="shrink-0 text-xs text-muted">{d.status === 'read' ? `${d.facts} facts` : 'Saved, not read yet'}</span>
                <button onClick={() => void remove(d)} className="grid size-7 shrink-0 place-items-center rounded-full text-muted hover:bg-mist hover:text-danger" aria-label={`Remove ${d.title}`}>
                  <Trash2 className="size-3.5" />
                </button>
              </li>
            )
          })}
        </ul>
      )}
      <Submit onClick={onContinue} disabled={!documents.length || Boolean(busy)}>
        Continue
      </Submit>
    </div>
  )
}

// --- Your people ----------------------------------------------------------------------

type PersonRow = { name: string; relationship: string; vip: boolean }

function PeoplePicker({ input, onAnswer }: { input: Extract<StepInput, { kind: 'people' }>; onAnswer: Answer }) {
  const blank = (): PersonRow => ({ name: '', relationship: input.relationships[0], vip: false })
  const [rows, setRows] = useState<PersonRow[]>(
    input.existing.length
      ? input.existing.map((e) => ({ ...e, relationship: e.relationship.charAt(0).toUpperCase() + e.relationship.slice(1) }))
      : [{ ...blank(), vip: true }, blank()],
  )
  const set = (i: number, patch: Partial<PersonRow>) => setRows((r) => r.map((row, j) => (j === i ? { ...row, ...patch } : row)))
  const filled = rows.filter((r) => r.name.trim())
  return (
    <div>
      <ul className="space-y-2">
        {rows.map((r, i) => (
          <li key={i} className="rounded-2xl bg-white p-3">
            <div className="flex items-center gap-2">
              <input
                value={r.name}
                onChange={(e) => set(i, { name: e.target.value })}
                placeholder={i === 0 ? 'e.g. Priya' : 'Name'}
                aria-label={`Person ${i + 1} name`}
                className="min-w-0 flex-1 rounded-xl bg-mist px-3 py-2 text-sm text-ink outline-none focus:ring-2 focus:ring-violet-400/50"
              />
              <select
                value={r.relationship}
                onChange={(e) => set(i, { relationship: e.target.value })}
                aria-label={`Person ${i + 1} relationship`}
                className="rounded-xl bg-mist px-2 py-2 text-sm text-ink outline-none"
              >
                {input.relationships.map((rel) => (
                  <option key={rel}>{rel}</option>
                ))}
              </select>
              <button onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))} className="grid size-8 place-items-center rounded-full text-muted hover:bg-mist" aria-label={`Remove person ${i + 1}`}>
                <X className="size-4" />
              </button>
            </div>
            <label className="mt-2 flex items-center justify-between gap-3 px-1 text-sm text-body">
              <span>Can always get through, even when you're busy</span>
              <Toggle on={r.vip} onChange={(v) => set(i, { vip: v })} label={`${r.name || `Person ${i + 1}`} can always get through`} />
            </label>
          </li>
        ))}
      </ul>
      {rows.length < 10 && (
        <button onClick={() => setRows((r) => [...r, blank()])} className="mt-2 inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-medium text-violet-700 hover:bg-white">
          <Plus className="size-4" /> Add someone
        </button>
      )}
      <Submit disabled={!filled.length} onClick={() => void onAnswer({ people: filled }, filled.map((p) => p.name + (p.vip ? ' (always)' : '')).join(', '))}>
        {input.submit}
      </Submit>
      <button onClick={() => void onAnswer({ skip: true }, 'Skip for now')} className="mt-2 w-full rounded-full py-2 text-sm text-muted hover:text-ink">
        {input.skip}
      </button>
    </div>
  )
}

// --- Urgent ---------------------------------------------------------------------------

function UrgentPicker({ input, onAnswer }: { input: Extract<StepInput, { kind: 'urgent' }>; onAnswer: Answer }) {
  const [picked, setPicked] = useState<string[]>(input.selected)
  const [other, setOther] = useState('')
  const [digest, setDigest] = useState(input.digest)
  const toggle = (t: string) => setPicked((p) => (p.includes(t) ? p.filter((x) => x !== t) : [...p, t]))
  const all = [...picked, ...(other.trim() ? [other.trim()] : [])]
  return (
    <div>
      <div className="flex flex-wrap gap-2">
        {input.options.map((o) => (
          <Chip key={o} selected={picked.includes(o)} onClick={() => toggle(o)}>
            {o}
          </Chip>
        ))}
      </div>
      <input
        value={other}
        onChange={(e) => setOther(e.target.value)}
        placeholder="Something else? e.g. anything about the Series A"
        aria-label="Something else that's urgent"
        className="mt-3 w-full rounded-2xl bg-white px-4 py-3 text-sm text-ink outline-none placeholder:text-muted/70 focus:ring-2 focus:ring-violet-400/50"
      />
      <p className="mt-4 mb-2 text-sm font-medium text-ink">Everything else, in a digest:</p>
      <div className="grid grid-cols-2 gap-1.5 rounded-2xl bg-white/70 p-1.5 sm:grid-cols-4">
        {input.digests.map((d) => (
          <button
            key={d.value}
            onClick={() => setDigest(d.value)}
            aria-pressed={digest === d.value}
            className={`rounded-xl px-2 py-2 text-sm ${digest === d.value ? 'bg-violet-600 font-medium text-white' : 'text-body hover:bg-white'}`}
          >
            {d.label}
          </button>
        ))}
      </div>
      <Submit disabled={!all.length} onClick={() => void onAnswer({ topics: picked, other, digest }, null)}>
        {input.submit}
      </Submit>
    </div>
  )
}

// --- Reaching you ---------------------------------------------------------------------

function ReachPicker({ input, onAnswer }: { input: Extract<StepInput, { kind: 'reach' }>; onAnswer: Answer }) {
  const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone || input.timezone
  const [channel, setChannel] = useState(input.channel)
  const [start, setStart] = useState(input.quiet_start)
  const [end, setEnd] = useState(input.quiet_end)
  const [vip, setVip] = useState(input.vip_breaks_quiet)
  const icons: Record<string, typeof Phone> = { app: MessageCircle, call: Phone }
  return (
    <div>
      <div className="grid gap-2 sm:grid-cols-3">
        {input.channels.map((c) => {
          const Icon = icons[c.value] ?? MessageCircle
          const on = channel === c.value
          const brand = c.value === 'telegram' ? 'telegram' : c.value === 'slack' ? 'slack' : null
          return (
            <button
              key={c.value}
              disabled={!c.available}
              onClick={() => setChannel(c.value)}
              aria-pressed={on}
              className={`rounded-2xl border-2 p-3 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                on ? 'border-violet-500 bg-lilac-50' : 'border-transparent bg-white hover:border-lilac-200'
              }`}
            >
              {brand ? <BrandIcon brand={brand} size={20} decorative /> : <Icon className="size-5 text-violet-600" />}
              <span className="mt-2 block text-sm font-medium text-ink">{c.label}</span>
              {c.note && <span className="block text-xs text-muted">{c.note}</span>}
            </button>
          )
        })}
      </div>
      <div className="mt-3 rounded-2xl bg-white p-3">
        <p className="flex items-center gap-2 text-sm font-medium text-ink">
          <Moon className="size-4 text-violet-600" /> Quiet hours
        </p>
        <div className="mt-2 flex items-center gap-2 text-sm text-body">
          <input type="time" value={start} onChange={(e) => setStart(e.target.value)} aria-label="Quiet hours start" className="rounded-xl bg-mist px-3 py-2 text-ink outline-none" />
          to
          <input type="time" value={end} onChange={(e) => setEnd(e.target.value)} aria-label="Quiet hours end" className="rounded-xl bg-mist px-3 py-2 text-ink outline-none" />
          <span className="ml-auto truncate text-xs text-muted">{browserTz}</span>
        </div>
        <label className="mt-3 flex items-center justify-between gap-3 text-sm text-body">
          <span>The people who can always get through can still reach me</span>
          <Toggle on={vip} onChange={setVip} label="Let the people who can always get through break quiet hours" />
        </label>
      </div>
      <Submit onClick={() => void onAnswer({ channel, quiet_start: start, quiet_end: end, vip_breaks_quiet: vip, timezone: browserTz }, null)}>
        {input.submit}
      </Submit>
    </div>
  )
}

// --- Sharing --------------------------------------------------------------------------

function SharePicker({ input, onAnswer }: { input: Extract<StepInput, { kind: 'share' }>; onAnswer: Answer }) {
  const [picked, setPicked] = useState<string[]>(input.selected)
  const [never, setNever] = useState(input.never)
  const toggle = (v: string) => setPicked((p) => (p.includes(v) ? p.filter((x) => x !== v) : [...p, v]))
  return (
    <div>
      <ul className="space-y-1.5">
        {input.options.map((o) => (
          <li key={o.value}>
            <label className="flex cursor-pointer items-center justify-between gap-3 rounded-2xl bg-white px-4 py-3 text-sm text-ink">
              <span>{o.label}</span>
              <Toggle on={picked.includes(o.value)} onChange={() => toggle(o.value)} label={o.label} />
            </label>
          </li>
        ))}
      </ul>
      <textarea
        value={never}
        onChange={(e) => setNever(e.target.value)}
        rows={2}
        placeholder={input.never_placeholder}
        aria-label="Never share"
        className="mt-3 w-full resize-none rounded-2xl bg-white px-4 py-3 text-sm text-ink outline-none placeholder:text-muted/70 focus:ring-2 focus:ring-violet-400/50"
      />
      <Submit onClick={() => void onAnswer({ shareable: picked, never }, null)}>{input.submit}</Submit>
      <button onClick={() => void onAnswer({ skip: true, never }, null)} className="mt-2 w-full rounded-full py-2 text-sm text-muted hover:text-ink">
        {input.skip}
      </button>
    </div>
  )
}

// --- Ground rules ---------------------------------------------------------------------

const RULE_ICONS = [Bot, Lock, CircleCheck, Sparkles, BookOpen, ShieldCheck]

function RulesList({ input, onAnswer }: { input: Extract<StepInput, { kind: 'rules' }>; onAnswer: Answer }) {
  return (
    <div>
      <ul className="space-y-2">
        {input.items.map((r, i) => {
          const Icon = RULE_ICONS[i % RULE_ICONS.length]
          return (
            <motion.li key={r} initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.07 }} className="flex items-start gap-3 rounded-2xl bg-white px-4 py-3 text-sm text-ink">
              <span className="grid size-8 shrink-0 place-items-center rounded-full bg-lilac-100 text-violet-600">
                <Icon className="size-4" />
              </span>
              <span className="pt-1.5">{r}</span>
            </motion.li>
          )
        })}
      </ul>
      <Submit onClick={() => void onAnswer({}, 'Sounds good')}>{input.submit}</Submit>
    </div>
  )
}

// --- Connect --------------------------------------------------------------------------

function ConnectPicker({
  input,
  agentName,
  me,
  onAnswer,
  refreshMe,
}: {
  input: Extract<StepInput, { kind: 'connect' }>
  agentName: string
  me: Me
  onAnswer: Answer
  refreshMe: () => Promise<Me | null>
}) {
  return (
    <div>
      <Connections agentName={agentName} me={me} refreshMe={refreshMe} />
      <Submit onClick={() => void onAnswer({ choice: 'done' }, 'All done')}>{input.submit}</Submit>
    </div>
  )
}
