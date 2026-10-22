'use client'

import { usePathname, useRouter } from 'next/navigation'
import { useEffect, type ReactNode } from 'react'
import { Separator } from '@/components/ui/separator'
import { SidebarInset, SidebarProvider, SidebarTrigger } from '@/components/ui/sidebar'
import { TooltipProvider } from '@/components/ui/tooltip'
import { AppSidebar } from '@/features/dashboard/AppSidebar'
import { agentStatus, navItemFor } from '@/features/dashboard/nav'
import { MobileTabBar } from '@/features/mobile/TabBar'
import { InstallHint } from '@/features/pwa/install'
import { MOBILE_TABS_ENABLED, PWA_ENABLED } from '@/lib/flags'
import { useSession } from '@/lib/session-context'

// The signed-in app: a sidebar for moving around, and the page beside it.
// Only for an owner who has hired an agent and finished the introduction.
export function DashboardShell({ defaultOpen, children }: { defaultOpen: boolean; children: ReactNode }) {
  const session = useSession()
  const router = useRouter()
  const pathname = usePathname() ?? '/dashboard'
  const me = session.me

  useEffect(() => {
    if (session.loading) return
    if (!session.account) router.replace(`/login?next=${encodeURIComponent(pathname)}`)
    else if (!session.account.hire || !me) router.replace('/')
    else if (!me.onboarding.done) router.replace('/meet')
  }, [session.loading, session.account, me, pathname, router])

  if (session.loading || !session.account || !me || !me.onboarding.done) {
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
          {PWA_ENABLED && <InstallHint />}
          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">{children}</div>
          {/* Phones only (below md), behind NEXT_PUBLIC_MOBILE_TABS; desktop is unchanged. */}
          {MOBILE_TABS_ENABLED && <MobileTabBar />}
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  )
}
