// A whole number between lo and hi from a form field, or null (blank, text, a fraction, out of range).
export function wholeNumber(raw: FormDataEntryValue | null, lo: number, hi: number): number | null {
  const text = typeof raw === 'string' ? raw.trim() : ''
  if (!/^\d+$/.test(text)) return null
  const n = Number(text)
  return Number.isInteger(n) && n >= lo && n <= hi ? n : null
}
