import {
  AlarmClock,
  BookOpen,
  Brain,
  Drama,
  GraduationCap,
  House,
  Inbox,
  MessageCircle,
  Plug,
  Puzzle,
  Settings,
  ShieldQuestion,
  Users,
} from 'lucide-react'
import type { Me } from '@/lib/api'

// `triage`: shown only while the backend has triage turned on (ADR 0010).
// `rehearsal`: shown only while the backend has the rehearsal room turned on (#58).
export type NavItem = { href: string; label: string; icon: typeof House; triage?: boolean; rehearsal?: boolean }
// `school`: shown only while the backend has Ava School turned on (#57).
export type NavItem = { href: string; label: string; icon: typeof House; triage?: boolean; school?: boolean }

export const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: 'Your assistant',
    items: [
      { href: '/dashboard', label: 'Home', icon: House },
      { href: '/dashboard/chat', label: 'Chat', icon: MessageCircle },
      { href: '/dashboard/needs-you', label: 'Needs you', icon: Inbox, triage: true },
      { href: '/dashboard/rehearsal', label: 'Rehearsal room', icon: Drama, rehearsal: true },
      { href: '/dashboard/reminders', label: 'Reminders', icon: AlarmClock },
      { href: '/dashboard/notebook', label: 'Notebook', icon: BookOpen },
      { href: '/dashboard/school', label: 'Ava School', icon: GraduationCap, school: true },
      { href: '/dashboard/people', label: 'People', icon: Users },
    ],
  },
  {
    group: 'Setup',
    items: [
      { href: '/dashboard/capabilities', label: 'Capabilities', icon: Puzzle },
      { href: '/dashboard/connections', label: 'Connections', icon: Plug },
      { href: '/dashboard/models', label: 'AI model', icon: Brain },
      { href: '/dashboard/settings', label: 'Settings', icon: Settings },
    ],
  },
]

// Pages reached from the phone's tab bar but not listed in the sidebar (the header still names them).
const EXTRA: NavItem[] = [{ href: '/dashboard/approvals', label: 'Approvals', icon: ShieldQuestion }]

export function navItemFor(pathname: string): NavItem | undefined {
  return [...NAV.flatMap((g) => g.items), ...EXTRA].find((i) =>
    i.href === '/dashboard' ? pathname === i.href : pathname.startsWith(i.href),
  )
}

export function agentStatus(me: Me): { label: string; dot: string } {
  if (me.paused) return { label: 'Paused', dot: 'bg-warn' }
  if (me.brain === 'claude') return { label: 'Online', dot: 'bg-ok' }
  return { label: 'Offline mode', dot: 'bg-muted' }
}
