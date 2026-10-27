import type { Metadata } from 'next'
import { MirrorPage } from '@/features/dashboard/notebook/MirrorPage'

export const metadata: Metadata = { title: 'The Mirror' }

export default function Page() {
  return <MirrorPage />
}
