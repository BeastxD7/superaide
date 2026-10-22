import type { Metadata } from 'next'
import { AuthScreen } from '@/app/(site)/screens'

export const metadata: Metadata = { title: 'Log in' }

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ next?: string; google?: string }> }) {
  const { next, google } = await searchParams
  return <AuthScreen mode="login" next={next ?? null} google={google ?? null} />
}
