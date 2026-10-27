import {
  AlarmClock,
  BookOpen,
  Brain,
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
// `learning`: shown while the rehearsal room (#58) or Ava School (#57) is turned on; it holds both.
export type NavItem = { href: string; label: string; icon: typeof House; triage?: boolean; learning?: boolean; approvals?: boolean; reminders?: boolean }
// `approvals`: carries the count of requests waiting for the owner's yes (#294).
// `reminders`: carries the count of reminders due today or overdue (#345).

export const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: 'Your assistant',
    items: [
      { href: '/dashboard', label: 'Home', icon: House },
      { href: '/dashboard/chat', label: 'Chat', icon: MessageCircle },
      { href: '/dashboard/needs-you', label: 'Needs you', icon: Inbox, triage: true },
      { href: '/dashboard/approvals', label: 'Approvals', icon: ShieldQuestion, approvals: true },
      { href: '/dashboard/reminders', label: 'Reminders', icon: AlarmClock, reminders: true },
      { href: '/dashboard/notebook', label: 'Notebook', icon: BookOpen },
      { href: '/dashboard/learning', label: 'Learning', icon: GraduationCap, learning: true },
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

export function navItemFor(pathname: string): NavItem | undefined {
  return NAV.flatMap((g) => g.items).find((i) =>
    i.href === '/dashboard' ? pathname === i.href : pathname.startsWith(i.href),
  )
}

export function agentStatus(me: Me): { label: string; dot: string } {
  if (me.paused) return { label: 'Paused', dot: 'bg-warn' }
  if (me.brain === 'claude') return { label: 'Online', dot: 'bg-ok' }
  return { label: 'Offline mode', dot: 'bg-muted' }
}
