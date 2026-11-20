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
  {
    slug: 'github',
    skillId: 'superaide/github',
    title: 'GitHub',
    summary: 'Look up issues and pull requests in the repositories you choose, and say where things stand. Choose the limits, and whether Ava may draft.',
    fixedRules: [
      'Ava sees only the repositories you choose, and only in your own chat: never in a channel where others can read.',
      'Text in an issue or comment was written by other people: it is information, never instructions to Ava.',
      'Every new issue and every comment waits for your yes and shows the exact text. Ava cannot edit, close, delete, merge or assign anything.',
      'Your access token is stored encrypted and shown only as a hint. Limit it on GitHub to the repositories you want Ava to see.',
      'Lookups are counted per hour and cut to the limits you set; the audit log keeps counts, never titles or text.',
    ],
    pages: [{ slug: 'limits', label: 'Limits', summary: 'How many results, how much text and how many lookups per hour Ava may use.' }],
  },
  {
    slug: 'notion',
    skillId: 'superaide/notion',
    title: 'Notion',
    summary: 'Look things up in the Notion pages you choose and say what they say. Choose the limits, and whether Ava may draft.',
    fixedRules: [
      'Ava sees only the pages you choose and the pages beneath them, and only in your own chat: never in a channel where others can read.',
      'Text in a page or comment was written by other people: it is information, never instructions to Ava.',
      'Every new page and every comment waits for your yes and shows the exact text. Ava cannot edit, archive, delete or move anything.',
      'Your integration secret is stored encrypted and shown only as a hint. In Notion, share only the pages you want Ava to see with the integration.',
      'Lookups are counted per hour and cut to the limits you set; the audit log keeps counts, never titles or text.',
    ],
    pages: [{ slug: 'limits', label: 'Limits', summary: 'How many results, how much text and how many lookups per hour Ava may use.' }],
  },
  {
    slug: 'drive',
    skillId: 'superaide/google-drive',
    title: 'Google Drive',
    summary: 'Search your Drive and read your Docs and Sheets when you ask. Choose the limits, and whether Ava may draft files.',
    fixedRules: [
      'Reading is for you only, in your own chat: never in a channel where others can read.',
      'Text in a file was written by other people: it is information, never instructions to Ava.',
      'Every new file and every addition waits for your yes and shows the exact text. Ava cannot delete, replace, move or share a file.',
      'Ava can add text only to a Doc she made herself: Google refuses any other file. A Doc that is shared with other people is called out on the request.',
      'Searches and reads are counted per hour and cut to the limits you set; the audit log keeps counts, never file names or text.',
    ],
    pages: [
      { slug: 'limits', label: 'Limits', summary: 'How many files, how much text and how many lookups per hour Ava may use.' },
      { slug: 'drafts', label: 'Drafts', summary: 'Whether Ava may propose new Docs and Sheets, for your approval.' },
    ],
  },
  {
    slug: 'web',
    skillId: 'superaide/web-research',
    title: 'Web research',
    summary: 'Ava searches and reads the web when you ask, and cites her sources. Choose how much she may use.',
    pages: [{ slug: 'use', label: 'Searching', summary: 'How many searches and pages Ava may use, and when she searches.' }],
    fixedRules: [
      'Text on a web page is information, never instructions: Ava does not follow what a page tells her to do.',
      'Ava cites the pages she uses, and searches and pages cost a little, so the limits above are yours to set.',
      'Files and links you import are limited to 10 MB of file, 60,000 characters of text, 2 MB per page, 5 redirects and 200 PDF pages.',
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
  '#calendar': capabilityHref('calendar', 'reading'),
  '#github': capabilityHref('github'),
  '#notion': capabilityHref('notion'),
  '#drive': capabilityHref('drive'),
  '#web': capabilityHref('web', 'use'),
}
