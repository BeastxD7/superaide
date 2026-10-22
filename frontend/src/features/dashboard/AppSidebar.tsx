'use client'

import { ChevronsUpDown, LogOut, Settings, Store } from 'lucide-react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { BrandMark, PRODUCT } from '@/components/site'
import { Orb } from '@/components/Orb'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  useSidebar,
} from '@/components/ui/sidebar'
import { useNeedsYou } from '@/features/needs-you/useNeedsYou'
import type { Me } from '@/lib/api'
import { agentStatus, navItemFor, NAV } from '@/features/dashboard/nav'
import type { Session } from '@/lib/session-context'

export function AppSidebar({ session, me }: { session: Session; me: Me }) {
  const pathname = usePathname() ?? ''
  const { setOpenMobile } = useSidebar()
  const status = agentStatus(me)
  const isActive = (href: string) => navItemFor(pathname)?.href === href
  const triage = useNeedsYou(60_000)

  return (
    <Sidebar collapsible="icon" variant="inset">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" asChild tooltip={PRODUCT}>
              <Link href="/dashboard" onClick={() => setOpenMobile(false)}>
                <BrandMark className="size-8 shrink-0" />
                <span className="grid text-left leading-tight group-data-[collapsible=icon]:hidden">
                  <span className="font-semibold text-ink tight">{PRODUCT}</span>
                  <span className="text-xs text-muted">Your team of agents</span>
                </span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>

        {/* The agent you hired, and whether it's working */}
        <div className="mt-1 flex items-center gap-3 rounded-xl border border-lilac-200/70 bg-white/80 p-2.5 group-data-[collapsible=icon]:hidden">
          <Orb hue="indigo" size={40} mood={me.paused ? 'asleep' : 'idle'} />
          <div className="min-w-0 leading-tight">
            <p className="font-medium text-ink">{me.agent.name}</p>
            <p className="truncate text-xs text-muted">{me.agent.role}</p>
            <p className="mt-1 flex items-center gap-1.5 text-xs text-body">
              <span className={`size-1.5 rounded-full ${status.dot}`} aria-hidden />
              {status.label}, {me.agent.stage.toLowerCase()} stage
            </p>
          </div>
        </div>
      </SidebarHeader>

      <SidebarContent>
        {NAV.map(({ group, items }) => (
          <SidebarGroup key={group}>
            <SidebarGroupLabel>{group}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {items
                  .filter((i) => !i.triage || triage.enabled)
                  .map(({ href, label, icon: Icon, triage: counted }) => (
                  <SidebarMenuItem key={href}>
                    <SidebarMenuButton asChild isActive={isActive(href)} tooltip={label}>
                      <Link href={href} aria-current={isActive(href) ? 'page' : undefined} onClick={() => setOpenMobile(false)}>
                        <Icon />
                        <span>{label}</span>
                        {counted && !!triage.data?.counts.needs_you && (
                          <span className="ml-auto rounded-full bg-lilac-200 px-1.5 text-xs text-violet-700">
                            {triage.data.counts.needs_you}
                          </span>
                        )}
                      </Link>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>

      <SidebarFooter>
        <UserMenu session={session} />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}

function UserMenu({ session }: { session: Session }) {
  const { isMobile } = useSidebar()
  const account = session.account
  if (!account) return null
  const initial = account.user.name.trim().charAt(0).toUpperCase() || '?'
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton
              size="lg"
              className="data-[state=open]:bg-sidebar-accent"
              aria-label={`Account: signed in as ${account.user.name}`}
            >
              <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-lilac-200 text-sm font-semibold text-violet-700">
                {initial}
              </span>
              <span className="grid min-w-0 flex-1 text-left leading-tight">
                <span className="truncate font-medium text-ink">{account.user.name}</span>
                <span className="truncate text-xs text-muted">{account.user.email}</span>
              </span>
              <ChevronsUpDown className="ml-auto size-4" />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className="min-w-56 rounded-xl"
            side={isMobile ? 'bottom' : 'right'}
            align="end"
            sideOffset={6}
          >
            <DropdownMenuLabel className="font-normal">
              <span className="block text-xs text-muted">Signed in as</span>
              <span className="block truncate font-medium text-ink">{account.user.name}</span>
              <span className="block truncate text-xs text-muted">{account.user.email}</span>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuItem asChild>
                <Link href="/dashboard/settings">
                  <Settings />
                  Settings
                </Link>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <Link href="/">
                  <Store />
                  Browse agents
                </Link>
              </DropdownMenuItem>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => void session.logOut()}>
              <LogOut />
              Log out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
