// The guided tours (#626): plain data. A step points at a real element by its `data-tour` attribute; the popover
// text is written here, so a wording change is one edit. A step whose element is not on screen (a collapsed
// sidebar on a phone, a feature that is off) is left out of that run, never shown pointing at nothing.

export type TourId = 'welcome' | 'home' | 'chat' | 'connections' | 'notebook' | 'approvals' | 'settings'

export type TourStep = {
  /** The value of the target's data-tour attribute. */
  target: string
  title: string
  body: string
}

export type PageTourId = Exclude<TourId, 'welcome'>

// `{name}` in a text is replaced by the agent's name.
export const TOURS: Record<PageTourId, { label: string; steps: TourStep[] }> = {
  home: {
    label: 'Home',
    steps: [
      { target: 'agent-card', title: 'Meet {name}', body: 'This is the assistant you hired. The card shows her name, role and how far she has grown.' },
      { target: 'nav', title: 'Everything is one click away', body: 'Chat, approvals, reminders, her notebook and your settings all live in this menu.' },
      { target: 'home-stage', title: 'How {name} earns trust', body: 'She starts small and takes on more as you correct her and approve her work. You are always the one who decides.' },
      { target: 'home-channels', title: 'Where people reach her', body: 'Telegram, Slack and the chat in this app. Connect more from here.' },
      { target: 'agent-status', title: 'Is she working?', body: 'This pill shows whether {name} is online, paused or in offline mode.' },
      { target: 'account-menu', title: 'Your account and help', body: 'Settings, sign out, and "Tour this page" to replay a walkthrough any time.' },
    ],
  },
  chat: {
    label: 'Chat',
    steps: [
      { target: 'chat-composer', title: 'Talk to {name}', body: 'Ask a question, give her a task or tell her something to remember. Enter sends, Shift and Enter starts a new line.' },
      { target: 'chat-attach', title: 'Share a file', body: 'Attach a PDF or a text file and she will read it and learn from it.' },
    ],
  },
  connections: {
    label: 'Connections',
    steps: [
      { target: 'connections-list', title: 'Give {name} a way to reach people', body: 'Add Telegram, Slack, email or your calendar. Keys are checked with each service and stored encrypted.' },
    ],
  },
  notebook: {
    label: 'Notebook',
    steps: [
      { target: 'notebook-search', title: 'What {name} knows', body: 'Everything she has learned about you, in one place. Search it to find a fact.' },
      { target: 'notebook-filters', title: 'Filter by kind or source', body: 'Narrow the list down, then correct or remove anything that is wrong. She forgets what you delete.' },
    ],
  },
  approvals: {
    label: 'Approvals',
    steps: [
      { target: 'approvals-list', title: 'Your yes comes first', body: 'When {name} wants to do something that has side effects, such as sending an email, it waits here until you approve it.' },
    ],
  },
  settings: {
    label: 'Settings',
    steps: [
      { target: 'settings-account', title: 'Your account', body: 'Your name, email and sign-in details.' },
      { target: 'settings-rules', title: 'Your rules', body: 'What you told {name} about what she may share and what she must never say. Change them any time.' },
      { target: 'settings-tour', title: 'Replay the tour', body: 'Want to see these walkthroughs again? Start over from here.' },
    ],
  },
}

/** Which page tour belongs to a pathname, or null when the page has none. */
export function tourForPath(pathname: string): PageTourId | null {
  if (pathname === '/dashboard') return 'home'
  const map: [string, PageTourId][] = [
    ['/dashboard/chat', 'chat'],
    ['/dashboard/connections', 'connections'],
    ['/dashboard/notebook', 'notebook'],
    ['/dashboard/approvals', 'approvals'],
    ['/dashboard/settings', 'settings'],
  ]
  return map.find(([p]) => pathname === p)?.[1] ?? null
}

export function fill(text: string, name: string): string {
  return text.replaceAll('{name}', name)
}

export type Progress = Partial<Record<TourId, 'completed' | 'skipped'>>

/**
 * Whether a tour should start by itself. The welcome prompt shows once per account; a page tour starts by itself
 * only after the owner took the tour (welcome completed) and has not finished or skipped that page.
 */
export function autoStart(progress: Progress, page: PageTourId | null): 'welcome' | PageTourId | null {
  if (!progress.welcome) return 'welcome'
  if (progress.welcome === 'completed' && page && !progress[page]) return page
  return null
}

export type Box = { top: number; left: number; width: number; height: number }

/** A rectangle grown by `pad` and kept inside the viewport. */
export function spotlight(rect: Box, pad: number, viewW: number, viewH: number): Box {
  const top = Math.max(0, rect.top - pad)
  const left = Math.max(0, rect.left - pad)
  const right = Math.min(viewW, rect.left + rect.width + pad)
  const bottom = Math.min(viewH, rect.top + rect.height + pad)
  return { top, left, width: Math.max(0, right - left), height: Math.max(0, bottom - top) }
}

/**
 * Where the popover goes: below the spotlight when it fits, else above, else centred over it. Always inside the
 * viewport with an 8px margin, and never wider than the viewport (a phone).
 */
export function popoverPosition(
  hole: Box,
  pop: { width: number; height: number },
  viewW: number,
  viewH: number,
): { top: number; left: number } {
  const m = 8
  const gap = 12
  const left = Math.min(Math.max(m, hole.left + hole.width / 2 - pop.width / 2), Math.max(m, viewW - pop.width - m))
  const below = hole.top + hole.height + gap
  const above = hole.top - gap - pop.height
  let top: number
  if (below + pop.height + m <= viewH) top = below
  else if (above >= m) top = above
  else top = Math.min(Math.max(m, viewH - pop.height - m), Math.max(m, hole.top + hole.height / 2 - pop.height / 2))
  return { top, left }
}
