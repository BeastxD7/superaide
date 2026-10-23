'use client'

import { Check, Copy } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useRef, useState } from 'react'
import type { Hue, Message, SourceRef, WorkStep } from '@/lib/api'
import { Markdown } from '@/components/Markdown'
import { Orb } from '@/components/Orb'
import { WorkLog } from '@/components/WorkLog'

// A reply being worked on right now (streamed from /app/chat/stream).
export type LiveTurn = { steps: WorkStep[]; reasoning: string; text: string; status: string }

export function Conversation({
  messages,
  typing,
  hue,
  agentName,
  scrollKey,
  live,
}: {
  messages: Message[]
  typing: boolean
  hue: Hue
  agentName: string
  scrollKey?: unknown
  live?: LiveTurn | null
}) {
  const endRef = useRef<HTMLDivElement>(null)

  // One small hidden status instead of a live region over the whole transcript and the streaming
  // bubble (a screen reader would re-read it on every token): say she is working, then that she
  // replied, once. The reply itself is the last message in the transcript, one step up from the box.
  const working = Boolean(live) || typing
  const [finished, setFinished] = useState(false)
  const [wasWorking, setWasWorking] = useState(false)
  if (working !== wasWorking) {
    setWasWorking(working)
    if (working) setFinished(false)
    else setFinished(true)
  }
  const announcement = working ? `${agentName} is working` : finished && messages.some((m) => m.from === 'agent') ? `${agentName} replied` : ''

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' })
  }, [messages.length, typing, scrollKey, live])

  return (
    <div className="flex flex-col gap-2.5">
      <p className="sr-only" role="status" data-testid="chat-status">
        {announcement}
      </p>
      <AnimatePresence initial={false}>
        {messages.map((m, i) => {
          const firstOfRun = i === 0 || messages[i - 1].from !== m.from
          return m.from === 'agent' ? (
            <motion.div
              key={`${i}-${m.from}`}
              layout="position"
              initial={{ opacity: 0, y: 12, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              transition={{ type: 'spring', stiffness: 260, damping: 24 }}
              className={`flex items-end gap-2.5 ${firstOfRun ? 'mt-3' : ''}`}
            >
              <div className="w-8 shrink-0">{isLastOfRun(messages, i) && <Orb hue={hue} size={32} />}</div>
              <AgentBubble
                text={m.text}
                agentName={agentName}
                copyable={m.kind === 'chat' && m.text.length > 240}
                sources={m.sources}
                activity={m.activity}
                reasoning={m.reasoning}
              />
            </motion.div>
          ) : (
            <motion.div
              key={`${i}-${m.from}`}
              layout="position"
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ type: 'spring', stiffness: 300, damping: 26 }}
              className={`flex justify-end ${firstOfRun ? 'mt-3' : ''}`}
            >
              <p className="max-w-[80%] rounded-3xl rounded-br-md bg-gradient-to-br from-violet-600 to-violet-700 px-4 py-2.5 whitespace-pre-wrap [overflow-wrap:anywhere] text-white shadow-[0_10px_24px_-16px_rgba(103,71,238,0.9)]">
                {m.text}
              </p>
            </motion.div>
          )
        })}
        {typing && !live && (
          <motion.div
            key="typing"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="mt-3 flex items-end gap-2.5"
          >
            <Orb hue={hue} size={32} mood="thinking" />
            <div className="flex gap-1 rounded-3xl rounded-bl-md bg-white px-4 py-3.5 shadow-[0_8px_24px_-18px_rgba(15,42,35,0.6)]" aria-label={`${agentName} is typing`}>
              {[0, 1, 2].map((d) => (
                <motion.span
                  key={d}
                  className="size-2 rounded-full bg-violet-400"
                  animate={{ y: [0, -4, 0], opacity: [0.5, 1, 0.5] }}
                  transition={{ duration: 0.9, repeat: Infinity, delay: d * 0.15 }}
                />
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      {/* Outside AnimatePresence: the saved reply replaces it at once, so the two never show together. */}
      {live && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          className="mt-3 flex items-end gap-2.5"
        >
          <Orb hue={hue} size={32} mood="thinking" />
          <div
            className="max-w-[85%] min-w-[min(18rem,85%)] rounded-3xl rounded-bl-md bg-white px-4 py-2.5 text-ink shadow-[0_8px_24px_-18px_rgba(15,42,35,0.6)]"
            data-testid="live-reply"
            aria-label={`${agentName} is working`}
          >
            <WorkLog steps={live.steps} reasoning={live.reasoning} status={live.status} live />
            {live.text && <Markdown text={live.text} />}
          </div>
        </motion.div>
      )}
      <div ref={endRef} className="h-px" />
    </div>
  )
}

// Source addresses come from the model or a web page, so only http(s) ones become links (no javascript: or data:).
function isWebUrl(uri: string): boolean {
  try {
    const { protocol } = new URL(uri)
    return protocol === 'https:' || protocol === 'http:'
  } catch {
    return false
  }
}

function isLastOfRun(messages: Message[], i: number): boolean {
  return i === messages.length - 1 || messages[i + 1].from !== messages[i].from
}

function AgentBubble({
  text,
  agentName,
  copyable,
  sources,
  activity,
  reasoning,
}: {
  text: string
  agentName: string
  copyable: boolean
  sources?: SourceRef[]
  activity?: WorkStep[]
  reasoning?: string
}) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      // Clipboard can be blocked; the text is still selectable.
    }
  }
  return (
    <div data-testid="agent-bubble" className="group max-w-[85%] rounded-3xl rounded-bl-md bg-white px-4 py-2.5 text-ink shadow-[0_8px_24px_-18px_rgba(15,42,35,0.6)]">
      <WorkLog steps={activity ?? []} reasoning={reasoning} />
      <Markdown text={text} />
      {sources && sources.length > 0 && <Sources sources={sources} />}
      {copyable && (
        <button
          onClick={() => void copy()}
          className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-mist px-3 py-1 text-xs font-medium text-body hover:bg-lilac-100 hover:text-violet-700"
          aria-label={`Copy ${agentName}'s message`}
        >
          {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
          {copied ? 'Copied' : 'Copy'}
        </button>
      )}
    </div>
  )
}

const SOURCE_KIND: Record<SourceRef['kind'], string> = {
  memory: 'Note',
  message: 'Message',
  web: 'Web',
  calendar: 'Calendar',
  email: 'Email',
  other: 'Source',
}

// Where the answer came from: numbered to match the [1] [2] marks in the text. Web
// sources open the page; notes and messages show the exact words that were used.
function Sources({ sources }: { sources: SourceRef[] }) {
  return (
    <details className="group mt-2 border-t border-line/70 pt-2 text-xs">
      <summary className="cursor-pointer list-none font-medium text-violet-700 select-none">
        Sources ({sources.length})
      </summary>
      <ol className="mt-1.5 space-y-1.5" aria-label="Sources">
        {sources.map((src) => (
          <li key={src.n} className="flex gap-2">
            <span className="w-5 shrink-0 text-muted">[{src.n}]</span>
            <span className="min-w-0 [overflow-wrap:anywhere]">
              <span className="font-medium text-ink">{SOURCE_KIND[src.kind]}</span>
              {' · '}
              {src.kind === 'web' && isWebUrl(src.uri) ? (
                <a href={src.uri} target="_blank" rel="noopener noreferrer" className="text-violet-700 underline">
                  {src.title || src.uri}
                </a>
              ) : (
                <span className="text-body">{src.title || src.uri}</span>
              )}
              {src.cited_text && <span className="mt-0.5 block text-muted">&ldquo;{src.cited_text}&rdquo;</span>}
            </span>
          </li>
        ))}
      </ol>
    </details>
  )
}
