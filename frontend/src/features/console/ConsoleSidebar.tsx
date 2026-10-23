'use client'

import { useEffect, useRef } from 'react'
import { LogOut } from 'lucide-react'
import { BrandMark } from '@/components/BrandMark'
import { Orb } from '@/components/Orb'
import type { Overview } from '@/features/console/api'
import { GROUPS, TABS, type TabKey } from '@/features/console/tabs'
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

export function ConsoleSidebar({
  tab,
  onTab,
  data,
  onDisconnect,
}: {
  tab: TabKey
  onTab: (t: TabKey) => void
  data: Overview | null
  onDisconnect: () => void
}) {
  // No tooltips in the drawer: a focused item's hidden tooltip would swallow the first Escape press meant to close it.
  const { setOpenMobile, openMobile, isMobile } = useSidebar()
  // The drawer has no trigger of its own to hand focus back to, so when it closes focus returns to the top bar's trigger.
  const wasOpen = useRef(false)
  useEffect(() => {
    if (wasOpen.current && !openMobile) document.querySelector<HTMLElement>('[data-sidebar="trigger"]')?.focus()
    wasOpen.current = openMobile
  }, [openMobile])
  return (
    <Sidebar collapsible="icon" variant="inset">
      <SidebarHeader className="pt-[max(0.5rem,env(safe-area-inset-top))]">
        <div className="flex items-center gap-2 px-1 py-1">
          <BrandMark className="size-8 shrink-0" />
          <span className="grid text-left leading-tight group-data-[collapsible=icon]:hidden">
            <span className="font-semibold text-ink tight">Superaide</span>
            <span className="text-xs text-muted">Operator console</span>
          </span>
        </div>
        {data && (
          <div className="mt-1 flex items-center gap-3 rounded-xl border border-lilac-200/70 bg-white/80 p-2.5 group-data-[collapsible=icon]:hidden">
            <Orb hue="indigo" size={40} mood={data.frozen ? 'asleep' : 'idle'} />
            <div className="min-w-0 leading-tight">
              <p className="font-medium text-ink">{data.agent_name}</p>
              <p className="mt-1 flex items-center gap-1.5 text-xs text-body">
                <span className={`size-1.5 rounded-full ${data.frozen ? 'bg-danger' : 'bg-ok'}`} aria-hidden />
                {data.frozen ? 'Paused' : 'Running'}, {data.stage.toLowerCase()} stage
              </p>
            </div>
          </div>
        )}
      </SidebarHeader>

      <SidebarContent>
        <nav aria-label="Sections" className="flex flex-col gap-2">
          {GROUPS.map((g) => (
            <SidebarGroup key={g.label}>
              <SidebarGroupLabel>{g.label}</SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu>
                  {g.keys.map((k) => {
                    const t = TABS.find((x) => x.key === k)!
                    return (
                      <SidebarMenuItem key={t.key}>
                        <SidebarMenuButton
                          isActive={tab === t.key}
                          tooltip={isMobile ? undefined : t.label}
                          aria-current={tab === t.key ? 'page' : undefined}
                          onClick={() => {
                            onTab(t.key)
                            setOpenMobile(false)
                          }}
                        >
                          <t.icon />
                          <span>{t.label}</span>
                        </SidebarMenuButton>
                      </SidebarMenuItem>
                    )
                  })}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          ))}
        </nav>
      </SidebarContent>

      <SidebarFooter className="pb-[max(0.5rem,env(safe-area-inset-bottom))]">
        <p className="px-2 text-xs text-muted group-data-[collapsible=icon]:hidden">
          Connected with the admin token, which is kept in this browser only.
        </p>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton tooltip={isMobile ? undefined : 'Disconnect'} aria-label="Disconnect" onClick={onDisconnect}>
              <LogOut />
              <span>Disconnect</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
