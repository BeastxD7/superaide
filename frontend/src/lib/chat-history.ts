import type { Message } from '@/lib/api'

// The chat asks for the newest PAGE messages; older ones are fetched on request with `before` (#299).
export const PAGE = 200

// Messages sent from this page before the server has them carry a local id.
const saved = (m: Message) => !m.id.startsWith('local-')

// A fresh read returns only the newest PAGE, so messages that were on screen can fall out of it (the window
// moved by what was just sent). They are not lost: they join the older ones above.
export function droppedBy(shown: Message[], fresh: Message[]): Message[] {
  const cut = fresh[0]?.at
  return cut ? shown.filter((m) => saved(m) && m.at < cut) : []
}

// Older ones plus the newly dropped, each message once, oldest first.
export function joinOlder(older: Message[], dropped: Message[]): Message[] {
  const seen = new Set<string>()
  return [...older, ...dropped].filter((m) => (seen.has(m.id) ? false : (seen.add(m.id), true)))
}

// The oldest saved message on screen: where "show earlier" continues from.
export function oldestSaved(older: Message[], shown: Message[]): Message | undefined {
  return older[0] ?? shown.find(saved)
}
