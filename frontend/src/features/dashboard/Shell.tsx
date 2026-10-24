'use client'

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, type ReactNode } from 'react'
import { Separator } from '@/components/ui/separator'
import { SessionProblem } from '@/components/SessionProblem'
import { SidebarInset, SidebarProvider, SidebarTrigger } from '@/components/ui/sidebar'
import { TooltipProvider } from '@/components/ui/tooltip'
import { AppSidebar } from '@/features/dashboard/AppSidebar'
import { agentStatus, navItemFor } from '@/features/dashboard/nav'
import { ReminderWatcher } from '@/features/dashboard/ReminderWatcher'
import { MobileTabBar } from '@/features/mobile/TabBar'
import { InstallHint } from '@/features/pwa/install'
import { MOBILE_TABS_ENABLED, PWA_ENABLED } from '@/lib/flags'
import { useSession } from '@/lib/session-context'
import { ToastHost } from '@/lib/toast-host'

// The signed-in app: a sidebar for moving around, and the page beside it.
// Only for an owner who has hired an agent and finished the introduction.
export function DashboardShell({ defaultOpen, children }: { defaultOpen: boolean; children: ReactNode }) {
  const session = useSession()
  const router = useRouter()
  const pathname = usePathname() ?? '/dashboard'
  const me = session.me
  // The one page open during the introduction: the AI model page, where the brain step sends people to connect a provider.
  const openDuringIntro = pathname === '/dashboard/models'

  // A failed read of the agent's state is not "not hired": stay put and offer a retry. The
  // state we have may be out of date too, so it is not used to redirect either.
  const hired = !session.account || Boolean(session.account.hire)
  const loadProblem = session.loadFailed && hired && (!me || !me.onboarding.done)

  useEffect(() => {
    if (session.loading || loadProblem) return
    if (!session.account) router.replace(`/login?next=${encodeURIComponent(pathname)}`)
    else if (!session.account.hire) router.replace('/')
    else if (me && !me.onboarding.done && !openDuringIntro) router.replace('/meet')
  }, [session.loading, session.account, me, loadProblem, pathname, router, openDuringIntro])

  if (!session.loading && loadProblem) return <SessionProblem session={session} dark />

  if (session.loading || !session.account || !me || (!me.onboarding.done && !openDuringIntro)) {
    return <div className="min-h-dvh bg-sidebar" aria-busy="true" />
  }

  const page = navItemFor(pathname)
  const status = agentStatus(me)

  return (
    <TooltipProvider delayDuration={300}>
      {/* The app fills the window exactly: the header stays put, and each page
          scrolls inside it (the chat keeps its composer on screen). */}
      <SidebarProvider defaultOpen={defaultOpen} className="h-svh">
        <AppSidebar session={session} me={me} />
        <ReminderWatcher />
        <ToastHost />
        <SidebarInset className="min-h-0 overflow-hidden md:peer-data-[variant=inset]:shadow-[0_1px_2px_rgba(27,22,56,0.06),0_12px_40px_-24px_rgba(27,22,56,0.25)]">
          <header className="flex h-14 shrink-0 items-center gap-2 border-b border-line/70 px-4">
            <SidebarTrigger className="-ml-1" />
            <Separator orientation="vertical" className="mr-1 data-[orientation=vertical]:h-4" />
            <p className="font-medium text-ink">{page?.label ?? 'Dashboard'}</p>
            <span className="ml-auto flex items-center gap-2 rounded-full bg-mist px-3 py-1 text-xs text-body">
              <span className={`size-1.5 rounded-full ${status.dot}`} aria-hidden />
              {me.agent.name}: {status.label.toLowerCase()}
            </span>
          </header>
          {(me.channels.health?.slack?.missing_scopes?.length ?? 0) > 0 && (
            <div role="alert" data-testid="slack-scopes-banner" className="shrink-0 border-b border-line/70 bg-rose-100 px-4 py-2 text-sm text-[#6b3d06]">
              Slack is missing permissions ({me.channels.health?.slack?.missing_scopes?.join(', ')}), so {me.agent.name} can&apos;t do
              everything there.{' '}
              <Link href="/dashboard/connections" className="font-medium underline">
                See how to fix it
              </Link>
            </div>
          )}
          {PWA_ENABLED && <InstallHint />}
          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">{children}</div>
          {/* Phones only (below md), behind NEXT_PUBLIC_MOBILE_TABS; desktop is unchanged. */}
          {MOBILE_TABS_ENABLED && <MobileTabBar />}
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  )
}
