import type { Metadata } from 'next'
import { PrincipalCardPage } from '@/app/(site)/dashboard/pages'

export const metadata: Metadata = { title: 'Principal card' }

export default function Page() {
  return <PrincipalCardPage />
}
