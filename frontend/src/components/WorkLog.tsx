'use client'

import {
  BadgeCheck,
  Bell,
  Brain,
  CalendarDays,
  Check,
  ChevronRight,
  FileText,
  Globe,
  Loader2,
  MessageSquareText,
  Search,
  ShieldCheck,
  Wrench,
  X,
  type LucideIcon,
} from 'lucide-react'
import { motion, useReducedMotion } from 'motion/react'
import type { WorkStep } from '@/lib/api'

const TOOL_ICON: Record<string, LucideIcon> = {
  recall: Search,
  create_reminder: Bell,
  calendar_events: CalendarDays,
  web_search: Globe,
  web_fetch: FileText,
  propose_action: ShieldCheck,
  verify: BadgeCheck,
}

// What Ava did while answering: each tool or skill call with how it went, the lines where
// she says what she's doing, and her reasoning. Live while she works, folded away after.
export function WorkLog({
  steps,
  reasoning,
  live = false,
  status,
}: {
  steps: WorkStep[]
  reasoning?: string
  live?: boolean
  status?: string
}) {
  if (!live && steps.length === 0 && !reasoning) return null
  const tools = steps.filter((s) => s.kind === 'tool').length
  const summary = tools ? `Worked through ${tools} step${tools === 1 ? '' : 's'}` : 'How I worked this out'

  const body = (
    <div className="space-y-1.5">
      {reasoning && <Reasoning text={reasoning} live={live} />}
      <ol className="space-y-1.5" aria-label="What Ava did">
        {steps.map((step, i) => (
          <Step key={step.id ?? `say-${i}`} step={step} />
        ))}
      </ol>
      {live && status && <Status text={status} />}
    </div>
  )

  if (live) {
    return (
      <div className="mb-2 border-b border-line/70 pb-2 text-xs" data-testid="work-log" aria-live="polite">
        {body}
      </div>
    )
  }
  return (
    <details className="group/work mb-2 border-b border-line/70 pb-2 text-xs" data-testid="work-log">
      <summary className="flex cursor-pointer list-none items-center gap-1 font-medium text-muted select-none hover:text-violet-700">
        <ChevronRight className="size-3.5 transition-transform group-open/work:rotate-90" aria-hidden />
        {summary}
      </summary>
      <div className="mt-1.5">{body}</div>
    </details>
  )
}

function Step({ step }: { step: WorkStep }) {
  if (step.kind === 'say') {
    return (
      <li className="flex gap-2 text-body">
        <MessageSquareText className="mt-0.5 size-3.5 shrink-0 text-violet-500" aria-hidden />
        <span className="min-w-0 [overflow-wrap:anywhere]">{step.label}</span>
      </li>
    )
  }
  const Icon = TOOL_ICON[step.name ?? ''] ?? Wrench
  const state = step.state ?? 'done'
  return (
    <motion.li
      initial={{ opacity: 0, x: -4 }}
      animate={{ opacity: 1, x: 0 }}
      className="flex gap-2"
      data-state={state}
      data-tool={step.name}
    >
      <Icon className="mt-0.5 size-3.5 shrink-0 text-violet-600" aria-hidden />
      <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">
        <span className="text-ink">{step.label}</span>
        {step.detail && <span className="text-muted"> · {step.detail}</span>}
      </span>
      {state === 'running' ? (
        <Loader2 className="mt-0.5 size-3.5 shrink-0 animate-spin text-violet-500" aria-label="Working" />
      ) : state === 'error' ? (
        <X className="mt-0.5 size-3.5 shrink-0 text-rose-500" aria-label="Didn't work" />
      ) : (
        <Check className="mt-0.5 size-3.5 shrink-0 text-emerald-600" aria-label="Done" />
      )}
    </motion.li>
  )
}

function Reasoning({ text, live }: { text: string; live: boolean }) {
  return (
    <details open={live} className="group/why">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 text-muted select-none hover:text-violet-700">
        <Brain className="size-3.5 text-violet-500" aria-hidden />
        Reasoning
        <ChevronRight className="size-3 transition-transform group-open/why:rotate-90" aria-hidden />
      </summary>
      <p className="mt-1 max-h-40 overflow-y-auto border-l-2 border-lilac-200 pl-2 whitespace-pre-wrap text-muted italic">
        {text}
      </p>
    </details>
  )
}

function Status({ text }: { text: string }) {
  const reduce = useReducedMotion()
  return (
    <div className="flex items-center gap-2 text-muted" role="status">
      <Loader2 className="size-3.5 animate-spin text-violet-500" aria-hidden />
      <motion.span
        animate={reduce ? undefined : { opacity: [0.55, 1, 0.55] }}
        transition={{ duration: 1.6, repeat: Infinity }}
      >
        {text}…
      </motion.span>
    </div>
  )
}
