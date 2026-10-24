// Which capabilities have their own pages under /dashboard/capabilities/<slug>, and the settings pages inside each
// (docs/design/dashboard-ia.md). A capability gains a page here as its card is converted; the rest stay on the index.
// `requiresInstall: false`: the page works without the capability being installed (the Principal card signs channel replies too).
export type CapabilityPage = { slug: string; label: string; summary: string; requiresInstall?: boolean }
export type CapabilityRoute = {
  slug: string
  skillId: string
  title: string
  summary: string
  pages: CapabilityPage[]
  // Rules that are fixed on purpose (never a setting), shown read-only as "How this works".
  fixedRules?: string[]
}

export const CAPABILITIES: CapabilityRoute[] = [
  {
    slug: 'gmail',
    skillId: 'superaide/gmail',
    title: 'Gmail',
    summary: 'Connect Google once, then choose what Ava may read and how she works with your mail.',
    fixedRules: [
      'Nothing is sent without your yes: every reply and every new email waits as a request that you approve.',
      'Email is information, never instructions: Ava does not do what an email tells her to do, even if it says it is from you.',
      'A new email goes to one address that you typed yourself, never one taken from a message, and never in a conversation where she has just read mail.',
      'An address that looks automated (noreply, do-not-reply) is refused unless you named the sender yourself; then the request carries a warning.',
      'Only you can change these settings, here. Ava cannot, and nothing in an email can.',
    ],
    pages: [
      { slug: 'reading', label: 'Reading', summary: 'What Ava may read in your Gmail.' },
      { slug: 'drafts', label: 'Drafts', summary: 'Whether Ava may save reply drafts in your Gmail.' },
      { slug: 'signature', label: 'Signature', summary: 'How Ava signs the emails and replies she sends for you (the Principal card).', requiresInstall: false },
    ],
  },
  {
    slug: 'calendar',
    skillId: 'superaide/google-calendar',
    title: 'Google Calendar',
    summary: 'See what is on and when you are free, and choose what Ava may read.',
    fixedRules: [
      'Adding, moving or declining events is off until you allow it, and each change waits for your yes.',
      'Event titles and descriptions written by other people are information, never instructions to Ava.',
      'An event title is cut at 200 characters, a description at 2,000, and an event lasts between 5 minutes and 24 hours.',
    ],
    pages: [{ slug: 'reading', label: 'Reading', summary: 'What Ava may read in your calendar.' }],
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
  '#calendar': capabilityHref('calendar', 'reading'),
}
