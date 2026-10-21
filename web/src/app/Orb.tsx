import { motion, useMotionValue, useReducedMotion, useSpring } from 'motion/react'
import { useEffect, useRef, useState } from 'react'
import type { Hue } from './api'

// Each agent is an iridescent orb with two eyes: alive and friendly, but plainly
// not a person. Agents still in training sleep with their eyes closed.

export type Mood = 'idle' | 'thinking' | 'happy' | 'asleep'

const PALETTES: Record<Hue, string[]> = {
  indigo: ['#b9a6ff', '#f3b6f5', '#9cc7ff', '#d9c9ff', '#b9a6ff'],
  teal: ['#8ee3cf', '#a7d8ff', '#c8f2e2', '#6fd0bd', '#8ee3cf'],
  amber: ['#ffc98f', '#ffb3c1', '#ffe3a6', '#f7b27a', '#ffc98f'],
}

type Props = {
  hue: Hue
  size?: number
  mood?: Mood
  track?: boolean // eyes follow the pointer
  className?: string
  label?: string
}

export function Orb({ hue, size = 120, mood = 'idle', track = false, className = '', label }: Props) {
  const reduce = useReducedMotion()
  const ref = useRef<HTMLDivElement>(null)
  const x = useMotionValue(0)
  const y = useMotionValue(0)
  const ex = useSpring(x, { stiffness: 180, damping: 18 })
  const ey = useSpring(y, { stiffness: 180, damping: 18 })
  const [blink, setBlink] = useState(false)

  // Eyes follow the pointer, a little.
  useEffect(() => {
    if (!track || reduce || mood === 'asleep') return
    const onMove = (e: PointerEvent) => {
      const r = ref.current?.getBoundingClientRect()
      if (!r) return
      const dx = e.clientX - (r.left + r.width / 2)
      const dy = e.clientY - (r.top + r.height / 2)
      const d = Math.hypot(dx, dy) || 1
      const reach = Math.min(1, d / 400) * size * 0.07
      x.set((dx / d) * reach)
      y.set((dy / d) * reach)
    }
    window.addEventListener('pointermove', onMove)
    return () => window.removeEventListener('pointermove', onMove)
  }, [track, reduce, mood, size, x, y])

  // Thinking: look up and to the side, like someone working something out.
  useEffect(() => {
    if (mood === 'thinking') {
      x.set(size * 0.05)
      y.set(-size * 0.05)
    } else if (!track) {
      x.set(0)
      y.set(0)
    }
  }, [mood, track, size, x, y])

  // Blink every few seconds.
  useEffect(() => {
    if (reduce || mood === 'asleep' || mood === 'happy') return
    let timer: number
    const loop = () => {
      timer = window.setTimeout(() => {
        setBlink(true)
        window.setTimeout(() => setBlink(false), 140)
        loop()
      }, 2600 + Math.random() * 3200)
    }
    loop()
    return () => window.clearTimeout(timer)
  }, [reduce, mood])

  const colors = PALETTES[hue]
  const eyeW = size * 0.075
  const eyeH = size * 0.12
  const gap = size * 0.11
  const asleep = mood === 'asleep'

  return (
    <div
      ref={ref}
      className={`relative shrink-0 ${className}`}
      style={{ width: size, height: size }}
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      {/* soft glow under the orb */}
      <div
        className="absolute inset-[8%] rounded-full"
        style={{ background: colors[0], opacity: asleep ? 0.25 : 0.55, filter: `blur(${Math.max(3, size * 0.14)}px)` }}
      />
      <motion.div
        className="absolute inset-0 overflow-hidden rounded-full"
        style={{ filter: asleep ? 'saturate(0.35) brightness(1.05)' : undefined }}
        animate={reduce ? undefined : { scale: mood === 'thinking' ? [1, 1.04, 1] : mood === 'happy' ? [1, 1.08, 1] : 1 }}
        transition={{ duration: mood === 'thinking' ? 1.4 : 0.5, repeat: mood === 'thinking' ? Infinity : 0 }}
      >
        <motion.div
          className="absolute -inset-1/4"
          style={{ background: `conic-gradient(from 0deg, ${colors.join(', ')})` }}
          animate={reduce || asleep ? undefined : { rotate: 360 }}
          transition={{ duration: mood === 'thinking' ? 6 : 18, repeat: Infinity, ease: 'linear' }}
        />
        <div
          className="absolute inset-0"
          style={{
            background:
              'radial-gradient(circle at 32% 26%, rgba(255,255,255,0.95) 0%, rgba(255,255,255,0.35) 22%, transparent 48%), radial-gradient(circle at 70% 85%, rgba(255,255,255,0.45) 0%, transparent 45%)',
          }}
        />
        <div className="absolute inset-0 rounded-full shadow-[inset_0_-10px_30px_rgba(80,40,160,0.18),inset_0_6px_14px_rgba(255,255,255,0.7)]" />
      </motion.div>

      {/* eyes */}
      <motion.div className="absolute inset-0 flex items-center justify-center" style={{ x: ex, y: ey, gap }}>
        {[0, 1].map((i) =>
          asleep || mood === 'happy' ? (
            <svg key={i} width={eyeW * 1.9} height={eyeH * 0.8} viewBox="0 0 20 10" className="overflow-visible">
              <path
                d={asleep ? 'M2 3 Q10 10 18 3' : 'M2 8 Q10 0 18 8'}
                fill="none"
                stroke="#1d1640"
                strokeWidth="2.6"
                strokeLinecap="round"
              />
            </svg>
          ) : (
            <motion.span
              key={i}
              className="block rounded-full bg-[#1d1640]"
              style={{ width: eyeW, height: eyeH }}
              animate={{ scaleY: blink ? 0.1 : 1 }}
              transition={{ duration: 0.08 }}
            />
          ),
        )}
      </motion.div>
    </div>
  )
}

export function hueText(hue: Hue): string {
  return hue === 'teal' ? 'text-kai' : hue === 'amber' ? 'text-rhea' : 'text-violet-600'
}

export function huePanel(hue: Hue): string {
  return hue === 'teal' ? 'bg-sage-100' : hue === 'amber' ? 'bg-peach-100' : 'bg-lilac-100'
}
