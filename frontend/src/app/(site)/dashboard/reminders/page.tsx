import type { Metadata } from 'next'
import { RemindersPage } from '@/app/(site)/dashboard/pages'

export const metadata: Metadata = { title: 'Reminders' }

export default function Page() {
  return <RemindersPage />
}
