import type { Metadata } from 'next'
import { MirrorPage } from '@/features/dashboard/MirrorPage'

export const metadata: Metadata = { title: 'The Mirror' }

export default function Page() {
  return <MirrorPage />
}
