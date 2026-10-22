import type { Metadata } from 'next'
import { JournalPage } from '@/features/dashboard/JournalPage'

export const metadata: Metadata = { title: 'Decision journal' }

export default function Page() {
  return <JournalPage />
}
