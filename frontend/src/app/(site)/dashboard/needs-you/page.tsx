import type { Metadata } from 'next'
import { NeedsYouPage } from '@/app/(site)/dashboard/pages'

export const metadata: Metadata = { title: 'Needs you' }

export default function Page() {
  return <NeedsYouPage />
}
