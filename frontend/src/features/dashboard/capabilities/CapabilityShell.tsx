'use client'

import Link from 'next/link'
import type { ReactNode } from 'react'
import { Breadcrumb, BreadcrumbItem, BreadcrumbLink, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from '@/components/ui/breadcrumb'
import { capabilityHref, type CapabilityRoute } from '@/features/dashboard/capabilities/registry'

// The frame of every capability page: breadcrumbs (Capabilities / Gmail / Reading), the title, and the capability's own
// sub-navigation. Tabs on every screen size (scrollable on a phone, 44px targets), so a setting is always one tap away.
export function CapabilityShell({ cap, page, children }: { cap: CapabilityRoute; page?: string; children: ReactNode }) {
  const current = page ? cap.pages.find((p) => p.slug === page) : undefined
  const title = current?.label ?? cap.title
  const lead = current?.summary ?? cap.summary
  const tabs = [{ slug: undefined as string | undefined, label: 'Overview' }, ...cap.pages.map((p) => ({ slug: p.slug as string | undefined, label: p.label }))]
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-4 py-6 sm:px-6 lg:py-8">
        <Breadcrumb className="mb-2">
          <BreadcrumbList>
            <BreadcrumbItem>
              <BreadcrumbLink asChild>
                <Link href="/dashboard/capabilities">Capabilities</Link>
              </BreadcrumbLink>
            </BreadcrumbItem>
            <BreadcrumbSeparator />
            <BreadcrumbItem>
              {current ? (
                <BreadcrumbLink asChild>
                  <Link href={capabilityHref(cap.slug)}>{cap.title}</Link>
                </BreadcrumbLink>
              ) : (
                <BreadcrumbPage>{cap.title}</BreadcrumbPage>
              )}
            </BreadcrumbItem>
            {current && (
              <>
                <BreadcrumbSeparator />
                <BreadcrumbItem>
                  <BreadcrumbPage>{current.label}</BreadcrumbPage>
                </BreadcrumbItem>
              </>
            )}
          </BreadcrumbList>
        </Breadcrumb>
        <h1 className="text-3xl font-medium text-ink tight">{title}</h1>
        <p className="mt-1 max-w-[60ch] text-body">{lead}</p>
        <nav aria-label={`${cap.title} settings`} className="scrollbar-none mt-5 mb-6 flex gap-1 overflow-x-auto border-b border-line">
          {tabs.map((t) => {
            const active = t.slug === page
            return (
              <Link
                key={t.slug ?? 'overview'}
                href={capabilityHref(cap.slug, t.slug)}
                aria-current={active ? 'page' : undefined}
                className={`-mb-px flex min-h-11 shrink-0 items-center border-b-2 px-3.5 text-sm ${
                  active ? 'border-violet-600 font-medium text-violet-700' : 'border-transparent text-body hover:text-ink'
                }`}
              >
                {t.label}
              </Link>
            )
          })}
        </nav>
        {children}
      </div>
    </div>
  )
}
