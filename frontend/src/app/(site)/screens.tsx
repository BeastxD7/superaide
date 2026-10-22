'use client'

// Client entry points for each page: they read the session and render the feature.
// Screens that only make sense in a browser (camera, speech, live chat) skip server rendering.
import dynamic from 'next/dynamic'
import type { AgentCard } from '@/lib/api'
import { useSession } from '@/lib/session-context'
import { AuthPage } from '@/features/auth/AuthPage'
import { Hire } from '@/features/hire/Hire'
import { Marketplace } from '@/features/marketplace/Marketplace'
import { Profile } from '@/features/marketplace/Profile'

const Meet = dynamic(() => import('@/features/onboarding/Meet').then((m) => m.Meet), {
  ssr: false,
  loading: () => <div className="min-h-dvh mesh" aria-busy="true" />,
})

export function MarketplaceScreen({ initialAgents }: { initialAgents: AgentCard[] | null }) {
  return <Marketplace session={useSession()} initialAgents={initialAgents} />
}

export function ProfileScreen({ slug, initialAgents }: { slug: string; initialAgents: AgentCard[] | null }) {
  return <Profile slug={slug} session={useSession()} initialAgents={initialAgents} />
}

export function AuthScreen({ mode, next, google = null }: { mode: 'signup' | 'login'; next: string | null; google?: string | null }) {
  return <AuthPage mode={mode} next={next} google={google} session={useSession()} />
}

export function HireScreen({ slug }: { slug: string }) {
  const session = useSession()
  if (session.loading) return <div className="min-h-dvh mesh" aria-busy="true" />
  return <Hire slug={slug} session={session} />
}

export function MeetScreen() {
  const session = useSession()
  if (session.loading) return <div className="min-h-dvh mesh" aria-busy="true" />
  return <Meet session={session} />
}
