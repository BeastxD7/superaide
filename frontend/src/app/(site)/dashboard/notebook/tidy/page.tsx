import type { Metadata } from 'next'
import { TidyPage } from '@/features/dashboard/notebook/TidyPage'

export const metadata: Metadata = { title: 'What was tidied' }

export default function Page() {
  return <TidyPage />
}
