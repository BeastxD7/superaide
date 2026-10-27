import type { Metadata } from 'next'
import { EpisodesPage } from '@/features/dashboard/notebook/EpisodesPage'

export const metadata: Metadata = { title: 'Conversations' }

export default function Page() {
  return <EpisodesPage />
}
