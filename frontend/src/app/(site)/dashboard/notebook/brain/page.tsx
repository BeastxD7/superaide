import type { Metadata } from 'next'
import { BrainPage } from '@/features/dashboard/BrainPage'

export const metadata: Metadata = { title: 'Glass brain' }

export default function Page() {
  return <BrainPage />
}
