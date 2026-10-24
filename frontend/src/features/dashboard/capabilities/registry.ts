// Which capabilities have their own pages under /dashboard/capabilities/<slug>, and the settings pages inside each
// (docs/design/dashboard-ia.md). A capability gains a page here as its card is converted; the rest stay on the index.
// `requiresInstall: false`: the page works without the capability being installed (the Principal card signs channel replies too).
export type CapabilityPage = { slug: string; label: string; summary: string; requiresInstall?: boolean }
export type CapabilityRoute = { slug: string; skillId: string; title: string; summary: string; pages: CapabilityPage[] }

export const CAPABILITIES: CapabilityRoute[] = [
  {
    slug: 'gmail',
    skillId: 'superaide/gmail',
    title: 'Gmail',
    summary: 'Connect Google once, then choose what Ava may read and how she works with your mail.',
    pages: [
      { slug: 'reading', label: 'Reading', summary: 'What Ava may read in your Gmail.' },
      { slug: 'drafts', label: 'Drafts', summary: 'Whether Ava may save reply drafts in your Gmail.' },
      { slug: 'signature', label: 'Signature', summary: 'How Ava signs the emails and replies she sends for you (the Principal card).', requiresInstall: false },
    ],
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
