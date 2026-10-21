import type { Metadata } from 'next'
import { AuthScreen } from '@/app/(site)/screens'

export const metadata: Metadata = { title: 'Log in' }

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const { next } = await searchParams
  return <AuthScreen mode="login" next={next ?? null} />
}
