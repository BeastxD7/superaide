// Which capabilities have their own pages under /dashboard/capabilities/<slug>, and the settings pages inside each
// (docs/design/dashboard-ia.md). A capability gains a page here as its card is converted; the rest stay on the index.
export type CapabilityPage = { slug: string; label: string; summary: string }
export type CapabilityRoute = { slug: string; skillId: string; title: string; summary: string; pages: CapabilityPage[] }

export const CAPABILITIES: CapabilityRoute[] = [
  {
    slug: 'gmail',
    skillId: 'superaide/gmail',
    title: 'Gmail',
    summary: 'Connect Google once, then choose what Ava may read and how she works with your mail.',
    pages: [{ slug: 'reading', label: 'Reading', summary: 'What Ava may read in your Gmail.' }],
  },
]

export const capabilityBySlug = (slug: string) => CAPABILITIES.find((c) => c.slug === slug)
export const capabilityBySkill = (skillId: string) => CAPABILITIES.find((c) => c.skillId === skillId)
export const capabilityHref = (slug: string, page?: string) => `/dashboard/capabilities/${slug}${page ? `/${page}` : ''}`

// Old addresses: a `#gmail` anchor on the index (the hash never reaches the server, so the page reads it) and the pre-routes
// sign-in return `?gmail=...`.
export const HASH_REDIRECTS: Record<string, string> = {
  '#gmail': capabilityHref('gmail'),
  '#gmail-read': capabilityHref('gmail', 'reading'),
}
