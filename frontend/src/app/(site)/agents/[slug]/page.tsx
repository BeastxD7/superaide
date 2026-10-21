import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { ProfileScreen } from '@/app/(site)/screens'
import { agentCatalog } from '@/lib/server'

type Props = { params: Promise<{ slug: string }> }

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params
  const agent = (await agentCatalog())?.find((a) => a.slug === slug)
  if (!agent) return { title: 'Agent not found' }
  return {
    title: `${agent.name}, ${agent.role.toLowerCase()}`,
    description: agent.tagline,
    openGraph: { title: `${agent.name}, ${agent.role}`, description: agent.tagline },
  }
}

export default async function AgentPage({ params }: Props) {
  const { slug } = await params
  const agents = await agentCatalog()
  if (agents && !agents.some((a) => a.slug === slug)) notFound()
  return <ProfileScreen slug={slug} initialAgents={agents} />
}
