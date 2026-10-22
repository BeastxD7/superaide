import type { Metadata } from 'next'
import { RehearsalPage } from '@/app/(site)/dashboard/pages'

export const metadata: Metadata = { title: 'Rehearsal room' }

export default function Page() {
  return <RehearsalPage />
}
