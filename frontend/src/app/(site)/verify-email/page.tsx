import type { Metadata } from 'next'
import { VerifyEmail } from '@/features/auth/EmailLinks'

export const metadata: Metadata = { title: 'Confirm your email' }

export default function Page() {
  return <VerifyEmail />
}
