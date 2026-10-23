// Windowed rendering for long lists (ADR 0014): only the rows near the viewport are in the DOM, a spacer above and
// below keeps the scroll bar honest. Fixed row height on purpose: it is what makes the skeleton rows, the real
// rows and the scroll arithmetic agree, and a list whose rows vary in height pages instead of windowing.
export type WindowInput = { count: number; rowHeight: number; scrollTop: number; viewport: number; overscan?: number }
export type WindowRange = { start: number; end: number; padTop: number; padBottom: number; total: number }

export function computeWindow({ count, rowHeight, scrollTop, viewport, overscan = 6 }: WindowInput): WindowRange {
  const total = count * rowHeight
  if (count <= 0 || rowHeight <= 0) return { start: 0, end: 0, padTop: 0, padBottom: 0, total: 0 }
  const top = Math.max(0, Math.min(scrollTop, Math.max(0, total - viewport)))
  const first = Math.floor(top / rowHeight)
  const last = Math.ceil((top + Math.max(viewport, 0)) / rowHeight)
  const start = Math.max(0, first - overscan)
  const end = Math.min(count, last + overscan)
  return { start, end, padTop: start * rowHeight, padBottom: (count - end) * rowHeight, total }
}
