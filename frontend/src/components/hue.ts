import type { Hue } from '@/lib/api'

// The soft panel behind an agent's card, in its hue.
export function huePanel(hue: Hue): string {
  return hue === 'sky' ? 'bg-sky-100' : hue === 'rose' ? 'bg-rose-100' : 'bg-lilac-100'
}
