import type { Metadata } from 'next'
import { ApprovalsPage } from '@/app/(site)/dashboard/pages'

export const metadata: Metadata = { title: 'Approvals' }

export default function Page() {
  return <ApprovalsPage />
}
