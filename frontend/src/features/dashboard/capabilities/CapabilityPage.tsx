'use client'

import Link from 'next/link'
import { notFound } from 'next/navigation'
import { Notice } from '@/components/site'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useEffect, useState } from 'react'
import { useAppData } from '@/features/dashboard/data'
import { CapabilityShell } from '@/features/dashboard/capabilities/CapabilityShell'
import { capabilityBySlug, capabilityHref } from '@/features/dashboard/capabilities/registry'
import { PrincipalCardPage } from '@/features/principal-card/PrincipalCardPage'
import { GmailPanel } from '@/features/dashboard/GmailPanel'
import { ConnectorSettings } from '@/features/dashboard/ConnectorSettings'
import { GmailDrafts } from '@/features/dashboard/GmailDrafts'
import { GmailReadSettings } from '@/features/dashboard/GmailReadSettings'
import { SettingRow } from '@/features/dashboard/settings/SettingRow'
import { SettingsSection } from '@/features/dashboard/settings/SettingsSection'
import { call, type GmailStatus, type SkillCard } from '@/lib/api'
import { useSession } from '@/lib/session-context'

// /dashboard/capabilities/<capability>[/<setting>]. Each page is the capability's own: its connection on the overview, one group
// of settings per sub-page. A capability that is not installed says so and points back to the index, where installing lives.
export function CapabilityPage({ slug, page }: { slug: string; page?: string }) {
  const { me } = useSession()
  const name = me?.agent.name ?? 'Ava'
  const cap = capabilityBySlug(slug)
  const { data } = useAppData<SkillCard[]>('/skills')
  if (!cap || (page && !cap.pages.some((p) => p.slug === page))) notFound()
  const card = data?.find((c) => c.id === cap.skillId)
  const needsInstall = cap.pages.find((p) => p.slug === page)?.requiresInstall !== false

  return (
    <CapabilityShell cap={cap} page={page}>
      {!data ? (
        <div className="space-y-3" aria-busy="true">
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : needsInstall && !card?.installed ? (
        <Notice tone="warn">
          {cap.title} isn&apos;t installed yet.{' '}
          <Link href="/dashboard/capabilities" className="font-medium underline">
            Install it on the Capabilities page
          </Link>{' '}
          first, then come back here for its settings.
        </Notice>
      ) : !page ? (
        <>
          {slug === 'gmail' && <GmailPanel name={name} />}
          {slug === 'calendar' && card?.google_connected === false && (
            <Notice tone="warn">
              Google isn&apos;t connected, so Ava can&apos;t see your calendar yet.{' '}
              <Link href="/dashboard/capabilities#google" className="font-medium underline">
                Connect your Google account on the Capabilities page
              </Link>
              .
            </Notice>
          )}
          <SettingsSection title="Settings in this capability" description="Each group has its own page.">
            {cap.pages.map((p) => (
              <SettingRow
                key={p.slug}
                title={p.label}
                why={p.summary}
                control={() => (
                  <Button asChild variant="outline" className="touch:min-h-11">
                    <Link href={capabilityHref(cap.slug, p.slug)} aria-label={`Open ${p.label}`}>
                      Open
                    </Link>
                  </Button>
                )}
              />
            ))}
          </SettingsSection>
        </>
      ) : page === 'use' && slug === 'web' ? (
        <ConnectorSettings path="web" title={`How ${name} uses the web`} anchor="web" />
      ) : page === 'reading' && slug === 'calendar' ? (
        <ConnectorSettings path="calendar" title={`What ${name} may read in your calendar`} anchor="calendar" />
      ) : page === 'reading' ? (
        <GmailReading name={name} />
      ) : page === 'drafts' ? (
        <GmailDraftsGate name={name} />
      ) : page === 'signature' ? (
        <PrincipalCardPage embedded />
      ) : null}
    </CapabilityShell>
  )
}

// The reading rules and drafts only mean something once Gmail is connected: until then, point to the overview where it is connected.
function useGmailConnected() {
  const [status, setStatus] = useState<GmailStatus | null>(null)
  useEffect(() => {
    let live = true
    call<GmailStatus>('/gmail').then((s) => live && setStatus(s)).catch(() => live && setStatus(null))
    return () => {
      live = false
    }
  }, [])
  return status
}

function ConnectGmailFirst() {
  return (
    <Notice tone="warn">
      Gmail isn&apos;t connected yet, so there is nothing to change here.{' '}
      <Link href={capabilityHref('gmail')} className="font-medium underline">
        Connect it on the Gmail overview
      </Link>
      .
    </Notice>
  )
}

function GmailReading({ name }: { name: string }) {
  const status = useGmailConnected()
  if (!status) return <Skeleton className="h-24 w-full" />
  return status.connected ? <GmailReadSettings name={name} /> : <ConnectGmailFirst />
}

function GmailDraftsGate({ name }: { name: string }) {
  const status = useGmailConnected()
  if (!status) return <Skeleton className="h-24 w-full" />
  return status.connected ? <GmailDrafts name={name} /> : <ConnectGmailFirst />
}
