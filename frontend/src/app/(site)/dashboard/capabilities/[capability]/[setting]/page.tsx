import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { CapabilityPage } from '@/features/dashboard/capabilities/CapabilityPage'
import { CAPABILITIES, capabilityBySlug } from '@/features/dashboard/capabilities/registry'

// Only registered settings pages exist: anything else is a real 404.
export const dynamicParams = false
export function generateStaticParams() {
  return CAPABILITIES.flatMap((c) => c.pages.map((p) => ({ capability: c.slug, setting: p.slug })))
}

export async function generateMetadata({ params }: { params: Promise<{ capability: string; setting: string }> }): Promise<Metadata> {
  const { capability, setting } = await params
  const cap = capabilityBySlug(capability)
  const page = cap?.pages.find((p) => p.slug === setting)
  return { title: cap && page ? `${page.label} | ${cap.title}` : 'Capabilities' }
}

export default async function Page({ params }: { params: Promise<{ capability: string; setting: string }> }) {
  const { capability, setting } = await params
  if (!capabilityBySlug(capability)?.pages.some((p) => p.slug === setting)) notFound()
  return <CapabilityPage slug={capability} page={setting} />
}
