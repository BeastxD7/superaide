import { TRUST_HELP, TRUST_NAMES } from '../format'

// Five ticks, filled up to the tier. Colour follows meaning: amber below
// "Linked" (not yet a known person), blue from "Linked" up.
export function TrustTier({ level }: { level: number }) {
  const tone = level >= 2 ? 'trust' : 'stranger'
  return (
    <span className={`tier tier-${tone}`} title={TRUST_HELP[level] ?? ''}>
      <span className="tier-ticks" aria-hidden="true">
        {[0, 1, 2, 3, 4].map((i) => (
          <span key={i} className={i <= level ? 'tick on' : 'tick'} />
        ))}
      </span>
      <span className="tier-name">{TRUST_NAMES[level] ?? `Tier ${level}`}</span>
    </span>
  )
}
