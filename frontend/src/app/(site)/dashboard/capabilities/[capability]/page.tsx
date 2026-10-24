import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { CapabilityPage } from '@/features/dashboard/capabilities/CapabilityPage'
import { CAPABILITIES, capabilityBySlug } from '@/features/dashboard/capabilities/registry'

// Only the capabilities in the registry exist: anything else is a real 404.
export const dynamicParams = false
export function generateStaticParams() {
  return CAPABILITIES.map((c) => ({ capability: c.slug }))
}

export async function generateMetadata({ params }: { params: Promise<{ capability: string }> }): Promise<Metadata> {
  const cap = capabilityBySlug((await params).capability)
  return { title: cap ? `${cap.title} | Capabilities` : 'Capabilities' }
}

export default async function Page({ params }: { params: Promise<{ capability: string }> }) {
  const { capability } = await params
  if (!capabilityBySlug(capability)) notFound()
  return <CapabilityPage slug={capability} />
}
