import type { Metadata } from 'next'
import { ForgotPassword } from '@/features/auth/EmailLinks'

export const metadata: Metadata = { title: 'Forgot your password' }

export default function Page() {
  return <ForgotPassword />
}
