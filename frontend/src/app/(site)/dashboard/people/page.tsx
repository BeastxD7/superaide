import type { Metadata } from 'next'
import { PeoplePage } from '@/app/(site)/dashboard/pages'

export const metadata: Metadata = { title: 'People' }

export default function Page() {
  return <PeoplePage />
}
