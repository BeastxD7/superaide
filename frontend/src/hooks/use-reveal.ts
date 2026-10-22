import { useReducedMotion } from 'motion/react'
import { useEffect, useState } from 'react'
import type { Message } from '@/lib/api'

// Ava's lines arrive one at a time, with a moment of "typing" before each, the
// way a person introduces themselves. Your own messages appear at once.

// `instant`: replies were already watched arriving (streamed), so show them without the pause.
export function useReveal(messages: Message[], pending: boolean, animateAll: boolean, instant = false) {
  const reduce = useReducedMotion()
  const [shown, setShown] = useState<number | null>(null)

  // First load: show history at once, unless this is a brand-new conversation.
  useEffect(() => {
    if (shown === null && messages.length > 0) setShown(animateAll ? 0 : messages.length)
  }, [messages.length, animateAll, shown])

  const count = Math.min(shown ?? 0, messages.length)
  const next = messages[count]

  useEffect(() => {
    if (shown === null || !next) return
    if (next.from === 'you' || reduce || instant) {
      setShown(count + 1)
      return
    }
    const delay = Math.min(1900, Math.max(650, 380 + next.text.length * 14))
    const t = window.setTimeout(() => setShown(count + 1), delay)
    return () => window.clearTimeout(t)
  }, [shown, next, count, reduce, instant])

  const typing = pending || (shown !== null && Boolean(next) && next.from === 'agent')
  return { visible: messages.slice(0, count), typing, settled: !typing && shown !== null && count >= messages.length }
}
