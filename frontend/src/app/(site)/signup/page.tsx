import type { Metadata } from 'next'
import { AuthScreen } from '@/app/(site)/screens'

export const metadata: Metadata = { title: 'Create your account' }

export default async function SignupPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const { next } = await searchParams
  return <AuthScreen mode="signup" next={next ?? null} />
}
