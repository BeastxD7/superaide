let segmenter: Intl.Segmenter | null | undefined

/** The first whole character of a name, upper-cased, for an avatar. An emoji or an accented letter can span
 * several UTF-16 units, so `charAt(0)` would return half of it; take the first grapheme instead. */
export function initialOf(name: string): string {
  const text = name.trim()
  if (!text) return '?'
  if (segmenter === undefined) segmenter = typeof Intl !== 'undefined' && 'Segmenter' in Intl ? new Intl.Segmenter() : null
  const first = segmenter ? segmenter.segment(text)[Symbol.iterator]().next().value?.segment : Array.from(text)[0]
  return (first ?? '?').toUpperCase()
}
