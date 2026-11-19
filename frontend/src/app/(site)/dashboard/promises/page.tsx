import type { Metadata } from 'next'
import { PromisesPage } from '@/app/(site)/dashboard/pages'

export const metadata: Metadata = { title: 'Promises' }

export default function Page() {
  return <PromisesPage />
}
