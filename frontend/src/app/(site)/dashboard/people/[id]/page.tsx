import type { Metadata } from 'next'
import { ContactPage } from '@/app/(site)/dashboard/pages'

export const metadata: Metadata = { title: 'Person' }

export default function Page() {
  return <ContactPage />
}
