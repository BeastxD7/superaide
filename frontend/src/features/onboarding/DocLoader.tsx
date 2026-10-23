'use client'

import { Check, FileText, Link2, Loader2 } from 'lucide-react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useEffect, useState } from 'react'

// What the introduction shows while a file or link is being read, and after. The captions advance on a timer: they say what is
// usually going on, not how far along the server is, so there is no percentage and nothing here claims progress it cannot know.
const FILE_STAGES = ['Uploading', 'Reading the pages', 'Picking out what matters', 'Saving to your notebook']
const LINK_STAGES = ['Fetching the page', 'Reading it', 'Picking out what matters', 'Saving to your notebook']
const STAGE_MS = [0, 1800, 6000, 12000]

export type Working = { kind: 'file' | 'link'; name: string; detail: string }

function hostOf(url: string): string {
  try {
    return new URL(url.includes('://') ? url : `https://${url}`).host
  } catch {
    return url
  }
}

export function workingFile(file: File): Working {
  const ext = file.name.includes('.') ? file.name.split('.').pop()!.toUpperCase() : 'File'
  return { kind: 'file', name: file.name, detail: ext }
}

export function workingLink(url: string): Working {
  return { kind: 'link', name: url, detail: hostOf(url) }
}

function useStage(count: number, active: boolean): number {
  const [stage, setStage] = useState(0)
  useEffect(() => {
    if (!active) return
    const timers = STAGE_MS.slice(1, count).map((ms, i) => window.setTimeout(() => setStage(i + 1), ms))
    return () => timers.forEach(window.clearTimeout)
  }, [count, active])
  return stage
}

// A page with a highlight line sweeping down it. Still (just the icon) when the person asks for less motion.
function ScanningPage({ kind }: { kind: 'file' | 'link' }) {
  const reduce = useReducedMotion()
  const Icon = kind === 'link' ? Link2 : FileText
  if (reduce)
    return (
      <span className="grid size-14 shrink-0 place-items-center rounded-xl bg-lilac-100" aria-hidden>
        <Icon className="size-6 text-violet-600" />
      </span>
    )
  return (
    <span className="relative grid h-16 w-12 shrink-0 place-items-center overflow-hidden rounded-lg border border-lilac-200 bg-white shadow-sm" aria-hidden>
      <span className="absolute inset-x-2 top-3 space-y-1.5">
        {[100, 85, 95, 60, 90, 70].map((w, i) => (
          <span key={i} className="block h-1 rounded-full bg-lilac-200" style={{ width: `${w}%` }} />
        ))}
      </span>
      <motion.span
        className="absolute inset-x-0 h-4 bg-gradient-to-b from-transparent via-violet-400/40 to-transparent"
        initial={{ top: '-20%' }}
        animate={{ top: ['-20%', '100%'] }}
        transition={{ duration: 1.6, repeat: Infinity, ease: 'easeInOut' }}
      />
    </span>
  )
}

export function DocLoader({ work }: { work: Working }) {
  const stages = work.kind === 'link' ? LINK_STAGES : FILE_STAGES
  const stage = useStage(stages.length, true)
  const reduce = useReducedMotion()
  const Icon = work.kind === 'link' ? Link2 : FileText
  return (
    <div className="rounded-2xl border border-lilac-200 bg-white p-4" data-testid="doc-loader">
      <div className="flex items-center gap-4">
        <ScanningPage kind={work.kind} />
        <div className="min-w-0 flex-1">
          {/* The one announcement: screen readers hear this line once, not every stage. */}
          <p role="status" aria-live="polite" className="truncate text-sm font-medium text-ink">
            Reading {work.name}…
          </p>
          <p className="mt-0.5 flex items-center gap-1.5 text-xs text-muted">
            <Icon className="size-3.5 shrink-0" aria-hidden />
            <span className="truncate">{work.detail}</span>
          </p>
          <div className="mt-2 h-5 text-sm text-violet-700" aria-hidden data-testid="doc-stage">
            <AnimatePresence mode="wait" initial={false}>
              <motion.p
                key={stage}
                className="flex items-center gap-1.5"
                initial={reduce ? false : { opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduce ? undefined : { opacity: 0, y: -6 }}
                transition={{ duration: 0.2 }}
              >
                {!reduce && <Loader2 className="size-3.5 animate-spin" />}
                {stages[stage]}
                {reduce ? '…' : ''}
              </motion.p>
            </AnimatePresence>
          </div>
        </div>
      </div>
    </div>
  )
}

export function DocDone({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-3 rounded-2xl border border-lilac-200 bg-lilac-50 p-4 text-sm text-ink" role="status" data-testid="doc-done">
      <span className="grid size-8 shrink-0 place-items-center rounded-full bg-ok/10 text-ok" aria-hidden>
        <Check className="size-4" />
      </span>
      <span>{text}</span>
    </div>
  )
}
