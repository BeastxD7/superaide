import type { Metadata } from 'next'
import { SchoolPage } from '@/app/(site)/dashboard/pages'

export const metadata: Metadata = { title: 'Ava School' }

export default function Page() {
  return <SchoolPage />
}
