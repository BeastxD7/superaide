'use client'

import type { Gate } from '@/features/console/api'

const STAGES = [
  { name: 'Infant', does: 'Observes and drafts' },
  { name: 'Child', does: 'Imitates under supervision' },
  { name: 'Adolescent', does: 'Acts with guardrails' },
  { name: 'Adult', does: 'Works on its own' },
] as const

const X = [70, 330, 590, 850]
const LINE_Y = 92

// A growth chart on graph paper: four stations, the current one filled, and
// the progress line drawn as far as the share of the next gate already met.
export function GrowthTrack({ stage, gates }: { stage: string; gates: Gate[] }) {
  const current = Math.max(
    0,
    STAGES.findIndex((s) => s.name === stage),
  )
  const met = gates.filter((g) => g.status === 'met').length
  const share = gates.length ? met / gates.length : 0
  const next = Math.min(current + 1, STAGES.length - 1)
  const progressEnd = X[current] + (X[next] - X[current]) * share

  return (
    <svg className="growth" viewBox="0 0 920 170" role="img" aria-label={`Growth track: ${stage}, ${met} of ${gates.length} gates to the next stage met`}>
      <defs>
        <pattern id="grid-small" width="10" height="10" patternUnits="userSpaceOnUse">
          <path d="M10 0H0V10" fill="none" stroke="var(--grid)" strokeWidth="0.6" />
        </pattern>
        <pattern id="grid-large" width="50" height="50" patternUnits="userSpaceOnUse">
          <rect width="50" height="50" fill="url(#grid-small)" />
          <path d="M50 0H0V50" fill="none" stroke="var(--grid-strong)" strokeWidth="0.8" />
        </pattern>
      </defs>
      <rect width="920" height="170" fill="url(#grid-large)" />
      <line x1={X[0]} x2={X[3]} y1={LINE_Y} y2={LINE_Y} stroke="var(--rule-strong)" strokeWidth="2" strokeDasharray="2 6" strokeLinecap="round" />
      <line x1={X[0]} x2={progressEnd} y1={LINE_Y} y2={LINE_Y} stroke="var(--trust)" strokeWidth="4" strokeLinecap="round" />
      {STAGES.map((s, i) => {
        const reached = i <= current
        return (
          <g key={s.name}>
            {i === current && <circle cx={X[i]} cy={LINE_Y} r="20" fill="var(--trust)" opacity="0.14" />}
            <circle
              cx={X[i]}
              cy={LINE_Y}
              r={i === current ? 10 : 7}
              fill={reached ? 'var(--trust)' : 'var(--surface)'}
              stroke={reached ? 'var(--trust)' : 'var(--rule-strong)'}
              strokeWidth="2"
            />
            <text x={X[i]} y={LINE_Y + 42} textAnchor="middle" className={i === current ? 'growth-name current' : 'growth-name'}>
              {s.name}
            </text>
            <text x={X[i]} y={LINE_Y + 62} textAnchor="middle" className="growth-does">
              {s.does}
            </text>
          </g>
        )
      })}
      {current < STAGES.length - 1 && (
        <text x={X[next]} y={LINE_Y - 26} textAnchor="middle" className="growth-gate">
          {met} of {gates.length} gates met
        </text>
      )}
    </svg>
  )
}
