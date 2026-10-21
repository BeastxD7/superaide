import type { Metadata } from 'next'
import { ResetPassword } from '@/features/auth/EmailLinks'

export const metadata: Metadata = { title: 'Choose a new password' }

export default function Page() {
  return <ResetPassword />
}
