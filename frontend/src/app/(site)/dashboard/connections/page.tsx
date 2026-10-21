import type { Metadata } from 'next'
import { ConnectionsPage } from '@/app/(site)/dashboard/pages'

export const metadata: Metadata = { title: 'Connections' }

export default function Page() {
  return <ConnectionsPage />
}
