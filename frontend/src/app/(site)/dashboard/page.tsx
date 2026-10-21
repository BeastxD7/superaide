import type { Metadata } from 'next'
import { DashboardHome } from '@/app/(site)/dashboard/pages'

export const metadata: Metadata = { title: 'Home' }

export default function Page() {
  return <DashboardHome />
}
