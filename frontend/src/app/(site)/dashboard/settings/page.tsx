import type { Metadata } from 'next'
import { SettingsPage } from '@/app/(site)/dashboard/pages'

export const metadata: Metadata = { title: 'Settings' }

export default function Page() {
  return <SettingsPage />
}
