// A <input type="datetime-local"> value is the person's own wall clock; the API wants an instant with a zone.
export function instant(local: string): string {
  return new Date(local).toISOString()
}

export function localValue(iso: string): string {
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}
