'use client'

import { AlarmClock, Bell, Menu, MessageCircle, ShieldQuestion } from 'lucide-react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useSidebar } from '@/components/ui/sidebar'
import { useNeedsYou } from '@/features/needs-you/useNeedsYou'

type Tab = { href: string; label: string; icon: typeof Bell; triage?: boolean }

// The phone's main places (ADR 0011 section 2). "Needs you" is ADR 0010 TR9's page: a link with
// the count while triage is on; while it's off, the tab shows disabled so the bar doesn't shift.
const TABS: Tab[] = [
  { href: '/dashboard/needs-you', label: 'Needs you', icon: Bell, triage: true },
  { href: '/dashboard/chat', label: 'Chat', icon: MessageCircle },
  { href: '/dashboard/approvals', label: 'Approvals', icon: ShieldQuestion },
  { href: '/dashboard/reminders', label: 'Reminders', icon: AlarmClock },
]

const ITEM = 'flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 py-1.5 text-[11px] leading-tight'

// A bottom tab bar below the md breakpoint; "More" opens the usual sidebar sheet with every page.
// It sits in the page's column (not over it), so the chat composer stays above it.
export function MobileTabBar() {
  const pathname = usePathname() ?? ''
  const { setOpenMobile } = useSidebar()
  const triage = useNeedsYou(60_000)
  const waiting = triage.data?.counts.needs_you ?? 0
  return (
    <nav
      aria-label="Main"
      className="flex shrink-0 border-t border-line/70 bg-white/95 pb-[env(safe-area-inset-bottom)] md:hidden"
    >
      {TABS.map(({ href, label, icon: Icon, triage: needsTriage }) => {
        if (needsTriage && !triage.enabled) {
          return (
            <span key={href} className={`${ITEM} text-muted/60`} aria-disabled="true" title="Coming soon">
              <Icon className="size-5" aria-hidden />
              <span className="truncate">{label}</span>
              <span className="sr-only">(coming soon)</span>
            </span>
          )
        }
        const active = pathname.startsWith(href)
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? 'page' : undefined}
            className={`${ITEM} ${active ? 'font-medium text-violet-700' : 'text-body'}`}
          >
            <span className="relative">
              <Icon className="size-5" aria-hidden />
              {needsTriage && waiting > 0 && (
                <span className="absolute -top-1.5 -right-2.5 min-w-4 rounded-full bg-violet-600 px-1 text-center text-[10px] leading-4 font-medium text-white">
                  {waiting}
                  <span className="sr-only"> waiting</span>
                </span>
              )}
            </span>
            <span className="truncate">{label}</span>
          </Link>
        )
      })}
      <button type="button" className={`${ITEM} text-body`} onClick={() => setOpenMobile(true)}>
        <Menu className="size-5" aria-hidden />
        <span className="truncate">More</span>
      </button>
    </nav>
  )
}
